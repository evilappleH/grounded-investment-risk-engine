import unittest
from datetime import datetime, timezone

from investment_agents.committee import build_agent_decision, build_committee_decision, compile_claims
from investment_agents.llm import InvestorResearchResult, ResearchResultStatus


def agent(name, *, stance="MIXED", status=ResearchResultStatus.COMPLETE, ticker="ABNB"):
    return InvestorResearchResult(
        ticker=ticker, agent_name=name, provider="fixture", model="fixture",
        generated_at=datetime.now(timezone.utc), thesis_summary="interpretation",
        strengths=(), concerns=(), valuation_view="mixed", financial_quality_view="mixed",
        key_unknowns=(), data_limitations=(), conditions_that_would_change_view=("new evidence",),
        research_status=stance, confidence=50, status=status, source_run_id="run-1",
        research_package_methodology_version="v1", warnings=(),
    )


class DeterministicCommitteeTests(unittest.TestCase):
    def setUp(self):
        self.package = {"ticker": "ABNB", "current_fundamentals": {"revenue": 100, "net_income": 10, "cash": 50, "total_debt": 20}, "valuation": {}, "piotroski": {"status": "PARTIAL"}}
        self.claims = compile_claims(self.package, run_id="run-1")

    def test_valid_agent_records_and_agreement_are_deterministic(self):
        b = build_agent_decision(agent("buffett"), self.claims, run_id="run-1")
        m = build_agent_decision(agent("munger"), self.claims, run_id="run-1")
        c = build_committee_decision(b, m, self.claims, valuation_status="INSUFFICIENT")
        self.assertEqual(c.committee_status, "AGREEMENT")
        self.assertEqual(c.evidence_sufficiency, "SUFFICIENT")
        self.assertEqual(c.valuation_sufficiency, "INSUFFICIENT")
        self.assertIn("VALUATION_EVIDENCE_INSUFFICIENT", c.reason_codes)
        self.assertTrue(c.shared_supporting_claim_ids)

    def test_disagreement_is_preserved_without_vote(self):
        b = build_agent_decision(agent("buffett", stance="MIXED"), self.claims, run_id="run-1")
        m = build_agent_decision(agent("munger", stance="UNATTRACTIVE_FOR_THIS_STYLE"), self.claims, run_id="run-1")
        self.assertEqual(build_committee_decision(b, m, self.claims).committee_status, "PARTIAL_AGREEMENT")

    def test_invalid_agent_fails_closed(self):
        b = build_agent_decision(agent("buffett", status=ResearchResultStatus.PARSE_ERROR), self.claims, run_id="run-1")
        m = build_agent_decision(agent("munger"), self.claims, run_id="run-1")
        c = build_committee_decision(b, m, self.claims)
        self.assertEqual(c.committee_status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(c.portfolio_policy_consideration, "INELIGIBLE")

    def test_agent_agreement_is_not_source_fact(self):
        b = build_agent_decision(agent("buffett"), self.claims, run_id="run-1")
        m = build_agent_decision(agent("munger"), self.claims, run_id="run-1")
        c = build_committee_decision(b, m, self.claims)
        self.assertFalse(hasattr(c, "source_facts"))


if __name__ == "__main__":
    unittest.main()
