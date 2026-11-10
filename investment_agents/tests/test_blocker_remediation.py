"""Regression tests for independently audited P1/MDR remediations."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import pandas as pd

from investment_agents.budget import ResearchBudget, build_execution_plan, conservative_input_token_bound, execute_plan
from investment_agents.committee import CommitteeRuntime
from investment_agents.data import (
    CurrentFundamentalsInput,
    FinancialPeriod,
    FundamentalRecord,
    FundamentalsStatus,
    TradingAgentsFundamentalsAdapter,
    summarize_historical_roe,
)
from investment_agents.events import (
    EventEvidence,
    EventEvidencePackage,
    EventMateriality,
    EventSynthesisRuntime,
    EvidenceSourceClass,
    SECPrimaryDocumentAdapter,
    SECEdgarAdapter,
    ThesisImpact,
    collect_event_evidence,
    persist_event_package,
    synthesize_event_evidence,
)
from investment_agents.llm import InvestorAgentRuntime, InvestorResearchResult, LLMProvider, LLMResponse, ResearchResultStatus
from investment_agents.research import ResearchPackage, build_enhanced_research_package, create_run_manifest, verify_run_manifest
from investment_agents.valuation import PRCycleStatus, calculate_pr_cycle, calculate_pr_metrics


NOW = datetime.now(timezone.utc)
PACKAGE = {
    "ticker": "X", "current_fundamentals": {}, "historical_fundamentals": [],
    "valuation": {}, "financial_diagnostics": {}, "piotroski": {},
    "qualification": {}, "ranking_context": {},
    "provenance": {"methodology_version": "step6c-v2-piotroski-average-assets", "analysis_mode": "CURRENT_ANALYSIS"},
}
VALID_AGENT = json.dumps({
    "thesis_summary": "mixed", "strengths": [], "concerns": [],
    "valuation_view": "unknown", "financial_quality_view": "unknown",
    "key_unknowns": [], "data_limitations": ["CURRENT_ANALYSIS"],
    "conditions_that_would_change_view": [], "research_status": "MIXED", "confidence": 50,
})


class Provider(LLMProvider):
    provider_name = "fixture"

    def __init__(self, responses, *, with_usage=True):
        self.responses = list(responses)
        self.with_usage = with_usage
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        text = self.responses.pop(0) if self.responses else VALID_AGENT
        return LLMResponse(text, "fixture", kwargs["model"], {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5} if self.with_usage else None)


def queue_two():
    entries = tuple(SimpleNamespace(
        ticker="X", lane="PARETO_RANKED", qualification_status="QUALIFIED",
        pareto_front=1, research_package=PACKAGE,
    ) for _ in range(1))
    return SimpleNamespace(run_id="budget-remediation", universe="custom", entries=entries)


def agent_result(name):
    return InvestorResearchResult(
        ticker="X", agent_name=name, provider="fixture", model="fixture", generated_at=NOW,
        thesis_summary="mixed", strengths=(), concerns=(), valuation_view="unknown",
        financial_quality_view="unknown", key_unknowns=(), data_limitations=("CURRENT_ANALYSIS",),
        conditions_that_would_change_view=(), research_status="MIXED", confidence=50,
        status=ResearchResultStatus.COMPLETE, source_run_id="events",
        research_package_methodology_version="step6c-v2-piotroski-average-assets",
    )


class BlockerRemediationTests(unittest.TestCase):
    def record(self, year, roe=10.0, **overrides):
        values = dict(
            ticker="X", period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            roe_percent=roe, analysis_mode="CURRENT_ANALYSIS",
        )
        values.update(overrides)
        return FundamentalRecord(**values)

    def test_formal_roe_does_not_bridge_noncontiguous_years(self):
        summary = summarize_historical_roe([self.record(2025), self.record(2023), self.record(2022)])
        self.assertIsNone(summary.roe_avg_3y_percent)

    def test_adapter_rejects_nonpositive_equity_endpoint_for_roe(self):
        periods = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]
        class Ticker:
            info = {"trailingPE": 10, "priceToBook": 2, "returnOnEquity": .1, "payoutRatio": .2}
            financials = pd.DataFrame({p: [10, 100] for p in periods}, index=["Net Income", "Total Revenue"])
            cashflow = pd.DataFrame()
            balance_sheet = pd.DataFrame({periods[0]: [100], periods[1]: [-10], periods[2]: [100]}, index=["Stockholders Equity"])
        result = TradingAgentsFundamentalsAdapter(ticker_factory=lambda _: Ticker()).get_fundamentals("X", datetime.now(timezone.utc), "CURRENT_ANALYSIS")
        self.assertIsNone(result.current_history.annual[0].roe_percent)

    def test_historical_as_of_current_provider_is_unsupported(self):
        raw = CurrentFundamentalsInput(source="fixture", retrieved_at=NOW, pe=10)
        requested = NOW - timedelta(days=10)
        result = TradingAgentsFundamentalsAdapter(current_loader=lambda _: raw).get_fundamentals("X", requested, "CURRENT_ANALYSIS")
        self.assertEqual(result.status, FundamentalsStatus.CURRENT_AS_OF_UNSUPPORTED)
        self.assertIsNone(result.current_metrics)
        self.assertEqual(result.requested_as_of, requested)
        self.assertEqual(result.as_of, result.data_effective_at)

    def test_share_class_pb_metrics_fail_closed(self):
        raw = CurrentFundamentalsInput(source="fixture", retrieved_at=NOW, pb=.001)
        mapped = TradingAgentsFundamentalsAdapter(current_loader=lambda _: raw).get_fundamentals("BRK.B", NOW, "CURRENT_ANALYSIS")
        self.assertEqual(mapped.current_metrics.pb_basis, "share_class_unverified")
        self.assertIn("SHARE_CLASS_BASIS_UNCERTAIN", mapped.current_metrics.warnings)
        pr = calculate_pr_metrics(
            ticker="BRK.B", pe=10, roe_percent=10, roe_mode="ttm", pb=.001,
            dividend_payout_percent=0, data_as_of="now", earnings_positive=True,
            pe_basis="trailing", pb_basis="share_class_unverified",
        )
        self.assertEqual(pr.pr, 1.0)
        self.assertIsNone(pr.pr_from_pb_roe)
        self.assertIn("SHARE_CLASS_BASIS_UNCERTAIN", pr.warnings)
        cycle = calculate_pr_cycle(pe=10, roe_avg_percent=10, roe_period="3y_average", pe_basis="trailing", pb=.001, pb_basis="share_class_unverified")
        self.assertEqual(cycle.status, PRCycleStatus.NOT_COMPUTED)
        self.assertIn("SHARE_CLASS_BASIS_UNCERTAIN", cycle.warnings)

    def test_timestamped_headline_and_no_baseline_remain_unknown(self):
        news = EventEvidence(
            "news:1", "X", "NEWS_ARTICLE", EvidenceSourceClass.NEWS, "fixture",
            title="Timestamped headline", published_at=date.today(), reference="https://example/news",
        )
        package = EventEvidencePackage("X", "events", NOW, date.today(), date.today(), news_evidence=(news,))
        result = synthesize_event_evidence(package)
        self.assertEqual(result.event_clusters[0].materiality, EventMateriality.MATERIALITY_UNKNOWN)
        self.assertEqual(result.overall_materiality, EventMateriality.MATERIALITY_UNKNOWN)
        self.assertEqual(result.thesis_impact, ThesisImpact.INSUFFICIENT_EVIDENCE)

    def test_sec_official_item_metadata_is_used_without_guessing(self):
        class SEC(SECEdgarAdapter):
            def _json(self, url):
                if "company_tickers" in url:
                    return {"0": {"ticker": "X", "cik_str": 1}}
                return {"filings": {"recent": {
                    "form": ["8-K"], "filingDate": [date.today().isoformat()],
                    "accessionNumber": ["1-2-3"], "primaryDocument": ["x.htm"],
                    "reportDate": [date.today().isoformat()], "items": ["1.01, 2.02,not-an-item"],
                }}}
        evidence = SEC(user_agent="fixture").fetch_filings("X", start=date.today(), end=date.today())[0]
        self.assertEqual(evidence.raw_metadata["official_item_codes"], ("1.01", "2.02"))
        self.assertTrue(evidence.raw_metadata["structured_item_metadata_complete"])

    def test_partial_sec_extraction_is_machine_readable_and_non_automated(self):
        class Response:
            content = b"<html><h1>Item 1.01</h1><p>Body</p></html>"
            headers = {"Content-Type": "text/html"}
            def raise_for_status(self): pass
        with TemporaryDirectory() as temp:
            content = SECPrimaryDocumentAdapter(user_agent="fixture", get=lambda *a, **k: Response(), cache_dir=Path(temp)).fetch(
                ticker="X", cik="1", form="8-K", accession="1-2-3", primary_document="x.htm", official_item_codes=("1.01",),
            )
            self.assertEqual(content.structured_extraction_status, "PARTIAL")
            self.assertFalse(content.automated_materiality_eligible)
            sec = EventEvidence("sec:1-2-3", "X", "8-K", EvidenceSourceClass.PRIMARY_CORPORATE, "SEC", raw_metadata={"accession_number": "1-2-3"})
            package = EventEvidencePackage("X", "events", NOW, date.today(), date.today(), primary_corporate_evidence=(sec,))
            cluster = synthesize_event_evidence(package, filing_content=(content,)).event_clusters[0]
            self.assertEqual(cluster.materiality, EventMateriality.MATERIALITY_UNKNOWN)
            self.assertFalse(cluster.automated_materiality_eligible)

    def test_event_runtime_never_dispatches_discarded_model_call(self):
        class Never:
            provider_name = "never"
            def generate(self, **kwargs): raise AssertionError("provider call is disabled")
        package = EventEvidencePackage("X", "events", NOW, date.today(), date.today())
        result = EventSynthesisRuntime(Never(), model="none").synthesize(package, execute=True)
        self.assertEqual(result.runtime_status, "DETERMINISTIC_ONLY_LLM_DISABLED")

    def _enhanced(self):
        base = ResearchPackage("X", None, "Technology", None, {}, {}, {}, {}, {}, {}, {}, {"analysis_mode": "CURRENT_ANALYSIS", "methodology_version": "step6c-v2-piotroski-average-assets"})
        news = EventEvidence("news:real", "X", "NEWS_ARTICLE", EvidenceSourceClass.NEWS, "fixture", title="Exact headline", reference="https://example/news")
        event_package = EventEvidencePackage("X", "events", NOW, date.today(), date.today(), news_evidence=(news,))
        synthesis = synthesize_event_evidence(event_package)
        return build_enhanced_research_package(base, event_package, synthesis)

    def test_committee_fabricated_or_interpretation_evidence_ids_are_unverified(self):
        template = {
            "consensus_points": [], "disagreement_points": [], "complementary_points": [],
            "shared_strengths": [], "shared_concerns": [], "valuation_synthesis": "mixed",
            "financial_quality_synthesis": "mixed", "business_quality_synthesis": "mixed",
            "key_unknowns": [], "data_limitations": [], "unresolved_questions": [],
            "next_research_questions": [], "committee_status": "BROAD_AGREEMENT",
            "confidence_in_synthesis": "LOW",
        }
        for bad_id in ("news:fabricated", "synthesis:1"):
            raw = {**template, "evidence_map": [{"statement": "Exact headline", "evidence_type": "DATA_SUPPORTED", "source": "model", "supported_by": [bad_id]}]}
            result = CommitteeRuntime(Provider([json.dumps(raw)]), model="fixture", max_retries=0).synthesize(self._enhanced(), [agent_result("buffett"), agent_result("munger")], execute=True)
            self.assertEqual(result.evidence_map[0].evidence_type, "UNVERIFIED_MODEL_CLAIM")
        good = {**template, "evidence_map": [{"statement": "Exact headline", "evidence_type": "DATA_SUPPORTED", "source": "fixture", "supported_by": ["news:real"]}]}
        result = CommitteeRuntime(Provider([json.dumps(good)]), model="fixture", max_retries=0).synthesize(self._enhanced(), [agent_result("buffett"), agent_result("munger")], execute=True)
        self.assertEqual(result.evidence_map[0].evidence_type, "DATA_SUPPORTED")

    def test_hard_token_reservation_blocks_second_dispatch(self):
        queue = queue_two()
        max_output = 10
        runtime = InvestorAgentRuntime(Provider([]), model="m", max_retries=0, max_output_tokens_per_call=max_output)
        preview = runtime.preview(PACKAGE, "buffett")
        one_reservation = conservative_input_token_bound(preview.system_prompt, preview.user_prompt) + max_output
        plan = build_execution_plan(queue, agents=("buffett", "munger"), tickers=("X",), budget=ResearchBudget(model="m", max_output_tokens_per_call=max_output, max_total_completed_tokens=one_reservation))
        provider = Provider([VALID_AGENT, VALID_AGENT], with_usage=False)
        report = execute_plan(plan, queue, provider)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(report.usage.hard_budget_consumed_tokens, one_reservation)
        self.assertEqual(report.usage.reserved_token_upper_bound, one_reservation)
        self.assertEqual(report.unexecuted_calls[0].status, "UNEXECUTED_BUDGET_EXHAUSTED")

    def test_missing_provider_usage_consumes_full_reservation(self):
        queue = queue_two()
        max_output = 10
        preview = InvestorAgentRuntime(Provider([]), model="m").preview(PACKAGE, "buffett")
        reservation = conservative_input_token_bound(preview.system_prompt, preview.user_prompt) + max_output
        plan = build_execution_plan(queue, agents=("buffett",), tickers=("X",), budget=ResearchBudget(model="m", max_output_tokens_per_call=max_output, max_total_completed_tokens=reservation))
        report = execute_plan(plan, queue, Provider([VALID_AGENT], with_usage=False))
        self.assertEqual(report.usage.hard_budget_consumed_tokens, reservation)

    def test_manifest_detects_artifact_tamper(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "research_runs" / "run" / "research_queue.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text('{"run_id":"run"}', encoding="utf-8")
            create_run_manifest(root=root, run_id="run", methodology_version="v2", artifacts={"research_queue": artifact})
            verified = verify_run_manifest(root=root, run_id="run")
            self.assertEqual(verified.artifact_count, 1)
            artifact.write_text('{"run_id":"tampered"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_run_manifest(root=root, run_id="run")

    def test_provider_failures_are_sanitized_before_persistence(self):
        class SECFail:
            def fetch_filings(self, *args, **kwargs): raise RuntimeError("Bearer sk-super-secret")
        class Fail:
            def fetch(self, *args, **kwargs): raise RuntimeError("api_key=super-secret")
        package = collect_event_evidence("X", run_id="safe-errors", sec=SECFail(), news=Fail(), macro=Fail(), sentiment=Fail(), end=date.today())
        with TemporaryDirectory() as temp:
            path = persist_event_package(package, root=Path(temp))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret", text)
            self.assertIn("PROVIDER_RUNTIMEERROR", text)


if __name__ == "__main__":
    unittest.main()
