import unittest
import json
from datetime import datetime, timezone

from investment_agents.committee import compile_claims, validate_claim_support, claim_catalog_hash, CommitteeRuntime
from investment_agents.llm import InvestorResearchResult, ResearchResultStatus, LLMProvider, LLMResponse


class ClaimLayerTests(unittest.TestCase):
    def setUp(self):
        self.package = {
            "ticker": "ABNB",
            "current_fundamentals": {
                "revenue": 100.0,
                "net_income": 10.0,
                "cash": 50.0,
                "total_debt": 20.0,
            },
            "valuation": {"pe": 25.0},
            "piotroski": {"partial_score": 6},
            "provenance": {"source": "SEC"},
        }

    def test_compiler_is_deterministic_and_source_owned(self):
        left = compile_claims(self.package, run_id="r1")
        right = compile_claims(self.package, run_id="r1")
        self.assertEqual(left, right)
        self.assertEqual(claim_catalog_hash(left), claim_catalog_hash(right))
        self.assertEqual(left[0].ticker, "ABNB")
        self.assertTrue(all(item.atomic_evidence_ids for item in left))

    def test_missing_or_nested_values_are_not_claims(self):
        package = {**self.package, "current_fundamentals": {"revenue": {"value": 1}, "cash": None}}
        claims = compile_claims(package)
        self.assertFalse(any(item.fields == ("revenue",) for item in claims))
        self.assertFalse(any(item.fields == ("cash",) for item in claims))

    def test_unknown_and_cross_domain_claims_fail(self):
        claims = compile_claims(self.package)
        valid = next(item.claim_id for item in claims if item.domain == "VALUATION")
        self.assertTrue(validate_claim_support([valid], claims, domain="VALUATION"))
        self.assertFalse(validate_claim_support(["claim:ABNB:valuation:invented"], claims))
        self.assertFalse(validate_claim_support([valid], claims, domain="PROFITABILITY"))

    def test_wrong_issuer_claim_is_not_accepted(self):
        claims = compile_claims(self.package)
        self.assertFalse(validate_claim_support(["claim:MSFT:valuation:pe"], claims))

    def test_claim_reference_hydrates_canonical_source_fact(self):
        class Provider(LLMProvider):
            provider_name = "fixture"
            def generate(self, **kwargs):
                raw = {
                    "consensus_points": [], "disagreement_points": [], "complementary_points": [],
                    "shared_strengths": [], "shared_concerns": [], "valuation_synthesis": "mixed",
                    "financial_quality_synthesis": "mixed", "business_quality_synthesis": "interpretive",
                    "key_unknowns": [], "data_limitations": [], "evidence_map": [{
                        "statement": "model prose is ignored", "evidence_type": "DATA_SUPPORTED",
                        "source": "ResearchPackage", "supported_by": ["claim:ABNB:profitability:revenue"]}],
                    "unresolved_questions": [], "next_research_questions": [],
                    "committee_status": "BROAD_AGREEMENT", "confidence_in_synthesis": "HIGH",
                }
                return LLMResponse(json.dumps(raw), "fixture", kwargs["model"], {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        def agent(name):
            return InvestorResearchResult(
                ticker="ABNB", agent_name=name, provider="fixture", model="fixture",
                generated_at=datetime.now(timezone.utc), thesis_summary="interpretation",
                strengths=(), concerns=(), valuation_view="mixed", financial_quality_view="mixed",
                key_unknowns=(), data_limitations=(), conditions_that_would_change_view=(),
                research_status="MIXED", confidence=50, status=ResearchResultStatus.COMPLETE,
                source_run_id="r", research_package_methodology_version="v1", warnings=(),
            )
        package = self.package
        result = CommitteeRuntime(Provider(), model="fixture", max_retries=0).synthesize(package, [agent("buffett"), agent("munger")], source_run_id="r", execute=True)
        self.assertEqual(result.runtime_status, "COMPLETE")
        self.assertEqual(result.evidence_map[0].evidence_type, "DATA_SUPPORTED")
        self.assertIn("revenue = 100.0", result.evidence_map[0].statement)


if __name__ == "__main__":
    unittest.main()
