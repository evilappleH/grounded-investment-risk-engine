"""Offline TEST_FIXTURE coverage for Step 7C synthesis."""

from datetime import datetime, timezone
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from investment_agents.committee import CommitteeStatus, CommitteeRuntime, persist_committee_result, synthesize_committee
from investment_agents.llm import InvestorResearchResult, ResearchResultStatus, LLMResponse, LLMProvider


PACKAGE = {
    "ticker": "KO", "current_fundamentals": {"revenue": 100, "free_cash_flow": 10}, "historical_fundamentals": [],
    "valuation": {"pr": 0.6, "warnings": ["ROE_BASIS_VENDOR_DEFINED"], "pr_corrected_experimental": True},
    "financial_diagnostics": {"warnings": ["DATA_BASIS_MISMATCH"]},
    "piotroski": {"status": "PARTIAL", "score": None}, "qualification": {"status": "QUALIFIED_WITH_WARNINGS"},
    "ranking_context": {"lane": "QUALIFIED_UNRANKED"},
    "provenance": {"analysis_mode": "CURRENT_ANALYSIS", "methodology_version": "step6c-v1", "filing_time_unavailable": True, "available_time_unavailable": True},
}


def result(agent, status, *, valuation="mixed", financial="mixed", strengths=("cash flow",), concerns=("data gap",), warnings=()):
    return InvestorResearchResult(
        ticker="KO", agent_name=agent, provider="fixture", model="fixture", generated_at=datetime.now(timezone.utc),
        thesis_summary=f"{agent} TEST_FIXTURE thesis", strengths=tuple(strengths), concerns=tuple(concerns), valuation_view=valuation,
        financial_quality_view=financial, key_unknowns=("filing date",), data_limitations=("CURRENT_ANALYSIS not PIT-safe",),
        conditions_that_would_change_view=("new evidence",), research_status=status, confidence=50,
        status=ResearchResultStatus.COMPLETE, source_run_id="fixture-run", research_package_methodology_version="step6c-v1", warnings=tuple(warnings),
    )


class FakeProvider(LLMProvider):
    provider_name = "fixture"
    def __init__(self, response="{}", responses=None): self.calls = 0; self.response = response; self.responses = list(responses or [])
    def generate(self, *, system_prompt, user_prompt, model, temperature=None, max_output_tokens=None):
        self.calls += 1
        text = self.responses.pop(0) if self.responses else self.response
        if isinstance(text, Exception):
            raise text
        return LLMResponse(text, "fixture", model, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


VALID_COMMITTEE = '{"consensus_points":["cash flow is discussed"],"disagreement_points":[],"complementary_points":["different lenses"],"shared_strengths":["cash flow"],"shared_concerns":["data gap"],"valuation_synthesis":"mixed","financial_quality_synthesis":"mixed","business_quality_synthesis":"interpretive","key_unknowns":["filing date"],"data_limitations":["CURRENT_ANALYSIS not PIT-safe"],"evidence_map":[{"statement":"cash flow is present","evidence_type":"DATA_SUPPORTED","source":"ResearchPackage","supported_by":[]}],"unresolved_questions":[],"next_research_questions":["validate filing date"],"committee_status":"BROAD_AGREEMENT","confidence_in_synthesis":"HIGH"}'


class CommitteeTests(unittest.TestCase):
    def test_broad_agreement_and_data_evidence(self):
        out = synthesize_committee(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], source_run_id="fixture-run")
        self.assertEqual(out.committee_status, CommitteeStatus.BROAD_AGREEMENT)
        self.assertEqual(out.participating_agents, ("buffett", "munger"))
        self.assertTrue(any(item.evidence_type == "DATA_SUPPORTED" for item in out.evidence_map))
        self.assertIn("CURRENT_ANALYSIS", out.data_limitations)
        self.assertIn("Piotroski PARTIAL", out.data_limitations)

    def test_disagreement_is_preserved_without_vote(self):
        out = synthesize_committee(PACKAGE, [result("buffett", "ATTRACTIVE_FOR_FURTHER_RESEARCH", valuation="attractive"), result("munger", "UNATTRACTIVE_FOR_THIS_STYLE", valuation="expensive")], source_run_id="fixture-run")
        self.assertIn(out.committee_status, (CommitteeStatus.MATERIAL_DISAGREEMENT, CommitteeStatus.PARTIAL_AGREEMENT))
        self.assertTrue(out.disagreement_points)
        self.assertEqual(out.agent_statuses["buffett"], "ATTRACTIVE_FOR_FURTHER_RESEARCH")
        self.assertEqual(out.agent_statuses["munger"], "UNATTRACTIVE_FOR_THIS_STYLE")
        self.assertFalse(hasattr(out, "recommendation_score"))

    def test_missing_or_failed_agent_is_insufficient(self):
        out = synthesize_committee(PACKAGE, [result("buffett", "MIXED")], source_run_id="fixture-run")
        self.assertEqual(out.committee_status, CommitteeStatus.INSUFFICIENT_AGENT_EVIDENCE)
        self.assertEqual(out.missing_agents, ("munger",))
        failed = InvestorResearchResult(**{**result("munger", "MIXED").__dict__, "status": ResearchResultStatus.PARSE_ERROR})
        out = synthesize_committee(PACKAGE, [result("buffett", "MIXED"), failed], source_run_id="fixture-run")
        self.assertEqual(out.committee_status, CommitteeStatus.INSUFFICIENT_AGENT_EVIDENCE)
        self.assertIn("munger", out.missing_agents)

    def test_unverified_claim_is_not_promoted(self):
        out = synthesize_committee(PACKAGE, [result("buffett", "MIXED", warnings=("UNVERIFIED_MODEL_CLAIM",)), result("munger", "MIXED")])
        types = {(item.evidence_type, item.source) for item in out.evidence_map}
        self.assertIn(("UNVERIFIED_MODEL_CLAIM", "buffett"), types)

    def test_dry_run_reuses_provider_boundary_without_call(self):
        provider = FakeProvider()
        runtime = CommitteeRuntime(provider, model="fixture")
        prompt = runtime.preview(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")])
        self.assertIn("DATA_SUPPORTED", prompt)
        self.assertEqual(provider.calls, 0)
        out = runtime.synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")])
        self.assertEqual(out.committee_status, CommitteeStatus.BROAD_AGREEMENT)

    def test_committee_persistence_is_separate(self):
        out = synthesize_committee(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], source_run_id="fixture-run")
        with TemporaryDirectory() as temp:
            path = persist_committee_result(out, root=Path(temp), source_run_id="fixture-run", source_agent_files=("KO_buffett.json", "KO_munger.json"))
            text = path.read_text(encoding="utf-8")
            self.assertIn("source_agent_files", text)
            self.assertNotIn("recommendation_score", text)

    def test_structured_committee_parse_and_provenance(self):
        provider = FakeProvider(VALID_COMMITTEE)
        runtime = CommitteeRuntime(provider, model="fixture")
        out = runtime.synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], source_run_id="r", execute=True)
        self.assertEqual(out.runtime_status, "COMPLETE")
        self.assertEqual(out.committee_status, CommitteeStatus.BROAD_AGREEMENT)
        self.assertEqual(out.source_run_id, "r")
        self.assertEqual(provider.calls, 1)

    def test_malformed_and_illegal_status_are_parse_error_with_bounded_retry(self):
        provider = FakeProvider(responses=["not json", VALID_COMMITTEE])
        out = CommitteeRuntime(provider, model="fixture", max_retries=1).synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], execute=True)
        self.assertEqual(out.runtime_status, "COMPLETE")
        self.assertEqual(provider.calls, 2)
        illegal = VALID_COMMITTEE.replace("BROAD_AGREEMENT", "BUY")
        out = CommitteeRuntime(FakeProvider(illegal), model="fixture", max_retries=0).synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], execute=True)
        self.assertEqual(out.runtime_status, "PARSE_ERROR")

    def test_out_of_scope_output_is_rejected(self):
        text = VALID_COMMITTEE.replace("mixed", "BUY at target price 9999", 1)
        out = CommitteeRuntime(FakeProvider(text), model="fixture", max_retries=0).synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], execute=True)
        self.assertEqual(out.runtime_status, "PARSE_ERROR")

    def test_single_agent_model_status_cannot_override_minimum_evidence(self):
        out = CommitteeRuntime(FakeProvider(VALID_COMMITTEE), model="fixture").synthesize(PACKAGE, [result("buffett", "MIXED")], execute=True)
        self.assertEqual(out.committee_status, CommitteeStatus.INSUFFICIENT_AGENT_EVIDENCE)

    def test_provider_error_is_explicit(self):
        out = CommitteeRuntime(FakeProvider(RuntimeError("timeout")), model="fixture", max_retries=0).synthesize(PACKAGE, [result("buffett", "MIXED"), result("munger", "MIXED")], execute=True)
        self.assertEqual(out.runtime_status, "PROVIDER_ERROR")


if __name__ == "__main__":
    unittest.main()
