"""Independent red-team regressions for already-defined package contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import pandas as pd

from investment_agents.budget import ResearchBudget, build_execution_plan, execute_plan
from investment_agents.committee import CommitteeRuntime
from investment_agents.data import (
    FinancialPeriod, FundamentalRecord, FundamentalsHistory,
    TradingAgentsFundamentalsAdapter, validate_fundamentals_usage,
)
from investment_agents.events import (
    EventEvidencePackage, StructuredYFinanceNewsAdapter, collect_event_evidence,
    persist_event_package, synthesize_event_evidence,
)
from investment_agents.financial import calculate_piotroski_score
from investment_agents.llm import (
    InvestorAgentRuntime, InvestorResearchResult, LLMProvider, LLMResponse,
    OpenAIProvider, ResearchResultStatus, persist_investor_result,
)
from investment_agents.qualification import QualificationStatus
from investment_agents.ranking import rank_scan_result
from investment_agents.research import build_research_queue
from investment_agents.scanner import ScanRecord, ScanResult, ScanStatus
from investment_agents.valuation import calculate_pr_cycle


NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
PACKAGE = {
    "ticker": "X", "current_fundamentals": {"a": 1, "b": 9},
    "historical_fundamentals": [], "valuation": {}, "financial_diagnostics": {},
    "piotroski": {}, "qualification": {}, "ranking_context": {},
    "provenance": {"analysis_mode": "CURRENT_ANALYSIS"},
}


def record(ticker="X", year=2025, **overrides):
    values = dict(
        ticker=ticker, period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
        filing_date=None, published_at=None, available_at=None, source="fixture",
        analysis_mode="CURRENT_ANALYSIS", net_income=10, operating_cash_flow=12,
        total_assets=100, long_term_debt=20, current_assets=50,
        current_liabilities=25, revenue=100, gross_profit=40,
        common_equity_issuance=0,
    )
    values.update(overrides)
    return FundamentalRecord(**values)


def scan_result(*records_, run_id="run"):
    return ScanResult(
        "custom", len(records_), len(records_), len(records_), len(records_), 0, 0, 0, 0,
        NOW, NOW, tuple(records_), "QUALIFIED_CANDIDATES_AVAILABLE", {}, run_id=run_id,
    )


def scan_record(ticker="X", pr=1.0, score=5):
    return ScanRecord(
        ticker, None, None, ScanStatus.ANALYZED, QualificationStatus.QUALIFIED,
        "ANALYZED", {"pr": pr, "status": "PR_AVAILABLE"},
        {"status": "COMPLETE", "score": score}, 0, (), None, NOW,
    )


class Provider(LLMProvider):
    provider_name = "fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return LLMResponse(value, "fixture", kwargs["model"], {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


def valid_agent(thesis="ok"):
    return json.dumps({
        "thesis_summary": thesis, "strengths": [], "concerns": [],
        "valuation_view": "unknown", "financial_quality_view": "unknown",
        "key_unknowns": [], "data_limitations": ["CURRENT_ANALYSIS"],
        "conditions_that_would_change_view": [], "research_status": "MIXED",
        "confidence": 1,
    })


class SolRegressionTests(unittest.TestCase):
    def test_research_cli_exposes_total_llm_call_cap(self):
        completed = subprocess.run(
            [sys.executable, "-m", "investment_agents", "research", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--max-llm-calls", completed.stdout)

    def test_current_snapshot_does_not_backfill_annual_statement(self):
        period = pd.Timestamp("2025-12-31")

        class Ticker:
            info = {"totalRevenue": 999, "netIncomeToCommon": 777, "freeCashflow": 555,
                    "trailingEps": 9, "trailingPE": 10, "priceToBook": 2,
                    "returnOnEquity": .2, "payoutRatio": .3}
            financials = pd.DataFrame({period: [None, None, None]}, index=["Total Revenue", "Net Income", "Diluted EPS"])
            cashflow = pd.DataFrame({period: [None]}, index=["Free Cash Flow"])
            balance_sheet = pd.DataFrame({period: [100]}, index=["Total Assets"])

        result = TradingAgentsFundamentalsAdapter(ticker_factory=lambda _: Ticker()).get_fundamentals("X", datetime.now(timezone.utc), "CURRENT_ANALYSIS")
        annual = result.current_history.annual[0]
        self.assertIsNone(annual.revenue)
        self.assertIsNone(annual.net_income)
        self.assertIsNone(annual.eps)
        self.assertIsNone(annual.free_cash_flow)

    def test_forged_pit_history_fails_closed(self):
        with self.assertRaises(ValueError):
            FundamentalsHistory("X", NOW, (record(),), analysis_mode="BACKTEST_PIT")
        pit_record = replace(record(), analysis_mode="BACKTEST_PIT", available_at=NOW)
        history = FundamentalsHistory("X", NOW, (pit_record,), analysis_mode="BACKTEST_PIT")
        validate_fundamentals_usage(history, "BACKTEST_PIT")

    def test_piotroski_rejects_mixed_issuer_and_nonannual_inputs(self):
        with self.assertRaises(ValueError):
            calculate_piotroski_score([record("X"), record("Y", 2024), record("X", 2023)])
        quarterly = replace(record(), period=FinancialPeriod.QUARTERLY)
        with self.assertRaises(ValueError):
            calculate_piotroski_score([quarterly])

    def test_pr_cycle_rejects_ttm_window(self):
        with self.assertRaises(ValueError):
            calculate_pr_cycle(pe=10, roe_avg_percent=10, roe_period="ttm", pe_basis="trailing", pb=1, pb_basis="current")

    def test_nonfinite_pr_and_invalid_score_are_unranked(self):
        ranked = rank_scan_result(scan_result(scan_record(pr=float("nan")), scan_record("Y", score=99)))
        self.assertEqual(ranked.rankable_count, 0)
        self.assertEqual({x.reason for x in ranked.unranked_candidates}, {"UNRANKED_VALUATION_UNAVAILABLE", "UNRANKED_DIAGNOSTIC_INVALID"})

    def test_news_window_filters_explicit_old_time_but_keeps_unknown(self):
        class Ticker:
            def get_news(self):
                return [
                    {"content": {"title": "old", "canonicalUrl": {"url": "https://old"}, "providerPublishTime": 946684800}},
                    {"content": {"title": "unknown", "canonicalUrl": {"url": "https://unknown"}}},
                ]
        items, _ = StructuredYFinanceNewsAdapter(ticker_factory=lambda _: Ticker()).fetch("X", start=date(2026, 1, 1), end=date(2026, 1, 2))
        self.assertEqual([item.title for item in items], ["unknown"])
        self.assertIsNone(items[0].published_at)

    def test_sentiment_rate_limit_is_not_plain_unavailable(self):
        empty = type("Empty", (), {"fetch_filings": lambda *a, **k: ()})
        no_items = type("NoItems", (), {"fetch": lambda *a, **k: ((), None)})
        limited = type("Limited", (), {"fetch": lambda *a, **k: ((), "PROVIDER_RATE_LIMITED")})
        package = collect_event_evidence("X", end=date(2026, 1, 2), sec=empty(), news=no_items(), macro=no_items(), sentiment=limited())
        self.assertEqual(synthesize_event_evidence(package).sentiment_status, "RAW_SENTIMENT_PROVIDER_RATE_LIMITED")

    def test_agent_rejects_trading_language_and_wrong_scalar_types(self):
        output = InvestorAgentRuntime(Provider([valid_agent("BUY 100 shares")]), model="m", max_retries=0).analyze(PACKAGE, "buffett")
        self.assertEqual(output.status, ResearchResultStatus.PARSE_ERROR)
        raw = json.loads(valid_agent())
        raw["thesis_summary"] = {"not": "a string"}
        output = InvestorAgentRuntime(Provider([json.dumps(raw)]), model="m", max_retries=0).analyze(PACKAGE, "buffett")
        self.assertEqual(output.status, ResearchResultStatus.PARSE_ERROR)

    def test_scientific_numeric_claim_is_flagged_and_unicode_is_encodable(self):
        output = InvestorAgentRuntime(Provider([valid_agent("unsupported 1e9")]), model="m", max_retries=0).analyze(PACKAGE, "buffett")
        self.assertIn("UNVERIFIED_MODEL_CLAIM", output.warnings)
        hostile = {**PACKAGE, "company_name": "bad\ud800name"}
        prompt = InvestorAgentRuntime(Provider([valid_agent()]), model="m").preview(hostile, "buffett")
        prompt.user_prompt.encode("utf-8")

    def test_provider_error_retries_once_outside_budget_scheduler(self):
        provider = Provider([RuntimeError("transient"), valid_agent()])
        output = InvestorAgentRuntime(provider, model="m", max_retries=1).analyze(PACKAGE, "buffett")
        self.assertEqual(output.status, ResearchResultStatus.COMPLETE)
        self.assertEqual(len(provider.calls), 2)

    def test_committee_budget_counts_each_ticker_and_both_caps(self):
        entries = tuple(SimpleNamespace(ticker=t, lane="PARETO_RANKED", qualification_status="QUALIFIED", pareto_front=1, research_package={**PACKAGE, "ticker": t}) for t in ("X", "Y"))
        queue = SimpleNamespace(run_id="run", universe="custom", entries=entries)
        plan = build_execution_plan(queue, agents=("buffett",), tickers=("X", "Y"), include_committee=True, budget=ResearchBudget(model="m", max_llm_calls=3))
        self.assertEqual(plan.planned_call_count, 4)
        self.assertEqual(plan.status, "BUDGET_EXCEEDED")
        plan = build_execution_plan(queue, agents=("buffett",), tickers=("X", "Y"), budget=ResearchBudget(model="m", max_agent_calls=1, max_llm_calls=10))
        self.assertEqual(plan.status, "BUDGET_EXCEEDED")

    def test_budget_scheduler_disables_hidden_retry_and_reports_committee_parse_failure(self):
        entry = SimpleNamespace(ticker="X", lane="PARETO_RANKED", qualification_status="QUALIFIED", pareto_front=1, research_package=PACKAGE)
        queue = SimpleNamespace(run_id="run", universe="custom", entries=(entry,))
        plan = build_execution_plan(queue, agents=("buffett",), tickers=("X",), budget=ResearchBudget(model="m"))
        provider = Provider([RuntimeError("fail"), valid_agent()])
        report = execute_plan(plan, queue, provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(report.usage.failed_calls, 1)

    def test_persistence_rejects_run_mismatch_traversal_and_redacts_metadata(self):
        result = InvestorResearchResult(
            "X", "buffett", "f", "m", NOW, "x", (), (), "x", "x", (), (), (), "MIXED", 1,
            ResearchResultStatus.COMPLETE, "actual", None,
            raw_response_metadata={"api_key": "TOP-SECRET", "finish_reason": "stop"},
        )
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                persist_investor_result(result, root=Path(temp), source_run_id="../escape")
            path = persist_investor_result(result, root=Path(temp), source_run_id="actual")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("TOP-SECRET", text)
            self.assertIn("finish_reason", text)
            event = EventEvidencePackage("X", "../escape", NOW, date(2026, 1, 1), date(2026, 1, 2))
            with self.assertRaises(ValueError):
                persist_event_package(event, root=Path(temp))

    def test_research_queue_rejects_unrelated_ranking(self):
        scan = scan_result(scan_record("X"))
        unrelated = rank_scan_result(scan_result(scan_record("Y")))
        with self.assertRaises(ValueError):
            build_research_queue(scan, unrelated)

    def test_openai_provider_uses_current_completion_token_parameter(self):
        class Usage:
            prompt_tokens = 1; completion_tokens = 1; total_tokens = 2
        class Response:
            choices = [SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")]
            usage = Usage()
        class Completions:
            def __init__(self): self.kwargs = None
            def create(self, **kwargs): self.kwargs = kwargs; return Response()
        completions = Completions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        OpenAIProvider(client=client).generate(system_prompt="s", user_prompt="u", model="m", max_output_tokens=7)
        self.assertEqual(completions.kwargs["max_completion_tokens"], 7)
        self.assertNotIn("max_tokens", completions.kwargs)


if __name__ == "__main__":
    unittest.main()
