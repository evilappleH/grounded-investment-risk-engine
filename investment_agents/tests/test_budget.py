"""Offline tests for Step 7B planning and bounded execution."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from investment_agents.budget import ResearchBudget, build_execution_plan, execute_plan, persist_execution_artifacts
from investment_agents.llm import LLMProvider, LLMResponse, ResearchResultStatus


PACKAGE = {"ticker": "X", "current_fundamentals": {}, "historical_fundamentals": [], "valuation": {}, "financial_diagnostics": {}, "piotroski": {}, "qualification": {}, "ranking_context": {}, "provenance": {"methodology_version": "step6c-v1", "analysis_mode": "CURRENT_ANALYSIS"}}
VALID = '{"thesis_summary":"mixed","strengths":[],"concerns":[],"valuation_view":"unknown","financial_quality_view":"unknown","key_unknowns":[],"data_limitations":["CURRENT_ANALYSIS"],"conditions_that_would_change_view":[],"research_status":"MIXED","confidence":50}'


def queue(*specs, run_id="budget-run"):
    entries = []
    for ticker, lane, qualification, front, partial in specs:
        entries.append(SimpleNamespace(ticker=ticker, lane=lane, qualification_status=qualification, pareto_front=front, research_package={**PACKAGE, "ticker": ticker}, partial_score=partial))
    return SimpleNamespace(run_id=run_id, universe="sp_500", entries=tuple(entries))


class FakeProvider(LLMProvider):
    provider_name = "fake"
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def generate(self, *, system_prompt, user_prompt, model, temperature=None, max_output_tokens=None):
        self.calls.append((model, max_output_tokens))
        response = self.responses.pop(0) if self.responses else VALID
        if isinstance(response, Exception):
            raise response
        return LLMResponse(response, "fake", model, {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5})


class BudgetTests(unittest.TestCase):
    def test_explicit_ranked_and_unranked_are_researchable(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED_WITH_WARNINGS", 1, None), ("AAPL", "QUALIFIED_UNRANKED", "QUALIFIED_WITH_WARNINGS", None, 7))
        p = build_execution_plan(q, agents=("buffett", "munger"), tickers=("KO", "AAPL"), budget=ResearchBudget(model="m"))
        self.assertEqual(p.status, "READY")
        self.assertEqual(p.planned_call_count, 4)

    def test_nonqualified_and_outside_queue_rejected(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None), ("BAD", "", "NOT_QUALIFIED", None, None))
        p = build_execution_plan(q, agents=("buffett",), tickers=("KO", "MISSING", "BAD"), budget=ResearchBudget(model="m"))
        self.assertEqual(p.status, "SELECTION_INVALID")
        self.assertIn("MISSING", p.excluded_candidates)
        self.assertIn("BAD", p.excluded_candidates)

    def test_front_full_selection_and_no_hidden_cutoff(self):
        q = queue(("A", "PARETO_RANKED", "QUALIFIED", 1, None), ("B", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett",), selection_mode="PARETO_FRONT", pareto_front=1, budget=ResearchBudget(max_candidates=1, model="m"))
        self.assertEqual(p.status, "SELECTION_EXCEEDS_BUDGET")
        self.assertEqual(p.selected_tickers, ("A", "B"))
        self.assertEqual(p.planned_call_count, 2)

    def test_call_budget_fails_before_execution(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett", "munger"), budget=ResearchBudget(max_agent_calls=1, model="m"), tickers=("KO",))
        provider = FakeProvider([VALID])
        report = execute_plan(p, q, provider)
        self.assertEqual(report.status, "BUDGET_EXCEEDED")
        self.assertEqual(provider.calls, [])

    def test_plan_zero_calls_and_output_limit(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett",), budget=ResearchBudget(model="m", max_output_tokens_per_call=77), tickers=("KO",))
        provider = FakeProvider([VALID])
        report = execute_plan(p, q, provider)
        self.assertEqual(report.status, "COMPLETE")
        self.assertEqual(provider.calls, [("m", 77)])

    def test_missing_model_and_missing_openai_key_are_zero_call(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett",), budget=ResearchBudget(model=None), tickers=("KO",))
        report = execute_plan(p, q, FakeProvider([VALID]))
        self.assertEqual(report.status, "LLM_MODEL_NOT_CONFIGURED")
        from investment_agents.llm import OpenAIProvider
        p = build_execution_plan(q, agents=("buffett",), budget=ResearchBudget(model="m"), tickers=("KO",))
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            report = execute_plan(p, q, OpenAIProvider(api_key=None))
        self.assertEqual(report.status, "LLM_PROVIDER_NOT_CONFIGURED")

    def test_failure_isolation_and_disagreement_preserved(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett", "munger"), budget=ResearchBudget(model="m"), tickers=("KO",))
        provider = FakeProvider([VALID, RuntimeError("timeout")])
        report = execute_plan(p, q, provider)
        self.assertEqual(report.status, "EXECUTION_PARTIAL")
        self.assertEqual(len(report.completed_executions), 1)
        self.assertEqual(len(report.failed_executions), 1)
        self.assertEqual(report.usage.completed_calls, 1)

    def test_hard_token_budget_requires_output_reservation(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett", "munger"), budget=ResearchBudget(model="m", max_total_completed_tokens=5), tickers=("KO",))
        provider = FakeProvider([VALID, VALID])
        report = execute_plan(p, q, provider)
        self.assertEqual(len(provider.calls), 0)
        self.assertEqual(report.budget_status, "BUDGET_EXHAUSTED")
        self.assertEqual(len(report.unexecuted_calls), 2)
        self.assertTrue(all(item.status == "UNEXECUTED_TOKEN_RESERVATION_UNAVAILABLE" for item in report.unexecuted_calls))

    def test_persistence_plan_report_no_key(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None), run_id="persist")
        p = build_execution_plan(q, agents=("buffett",), budget=ResearchBudget(model="m"), tickers=("KO",))
        report = execute_plan(p, q, FakeProvider([VALID]))
        with TemporaryDirectory() as temp:
            paths = persist_execution_artifacts(p, report, root=Path(temp))
            text = Path(paths["execution_report"]).read_text(encoding="utf-8")
            self.assertNotIn("OPENAI_API_KEY", text)
            self.assertTrue(Path(paths["execution_plan"]).exists())

    def test_committee_call_is_counted_before_execution(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett", "munger"), include_committee=True, budget=ResearchBudget(model="m", max_llm_calls=2), tickers=("KO",))
        self.assertEqual(p.planned_call_count, 3)
        self.assertEqual(p.status, "BUDGET_EXCEEDED")

    def test_committee_execution_is_separate_call(self):
        q = queue(("KO", "PARETO_RANKED", "QUALIFIED", 1, None))
        p = build_execution_plan(q, agents=("buffett", "munger"), include_committee=True, budget=ResearchBudget(model="m", max_llm_calls=3), tickers=("KO",))
        committee_json = '{"consensus_points":[],"disagreement_points":[],"complementary_points":[],"shared_strengths":[],"shared_concerns":[],"valuation_synthesis":"mixed","financial_quality_synthesis":"mixed","business_quality_synthesis":"mixed","key_unknowns":[],"data_limitations":[],"evidence_map":[],"unresolved_questions":[],"next_research_questions":[],"committee_status":"BROAD_AGREEMENT","confidence_in_synthesis":"HIGH"}'
        provider = FakeProvider([VALID, VALID, committee_json])
        report = execute_plan(p, q, provider)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(report.usage.completed_calls, 3)
        self.assertEqual([item.agent_name for item in report.completed_executions], ["buffett", "munger", "committee"])


if __name__ == "__main__":
    unittest.main()
