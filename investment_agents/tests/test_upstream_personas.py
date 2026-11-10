"""Offline adaptations of upstream persona tests; no LLM or data client."""

from __future__ import annotations

import unittest

from investment_agents.agents import BuffettAgent, MungerAgent
from investment_agents.data import FundamentalsContext
from investment_agents.models import Candidate, InvestorOpinion
from investment_agents.valuation import calculate_pr_metrics


class UpstreamPersonaTests(unittest.TestCase):
    def test_upstream_persona_identity_and_json_contract(self) -> None:
        for agent_type, expected_name in ((BuffettAgent, "buffett"), (MungerAgent, "munger")):
            with self.subTest(agent=expected_name):
                agent = agent_type()
                prompt = agent.get_system_prompt()
                self.assertEqual(agent.name, expected_name)
                self.assertIn('"signal": "bullish" | "bearish" | "neutral"', prompt)
                self.assertIn("Reason ONLY from the data provided", prompt)

    def test_pr_is_structured_context_not_a_persona_prompt_rewrite(self) -> None:
        agent = BuffettAgent()
        candidate = Candidate("TEST")
        context = FundamentalsContext("TEST", "2026-09-08", "point-in-time fundamentals")
        pr = calculate_pr_metrics(
            ticker="TEST", pe=15, roe_percent=15, roe_mode="ttm",
            pe_basis="trailing", data_as_of="2026-09-08"
        )
        prepared = agent.prepare_review(candidate, context, pr_metrics=pr)
        self.assertAlmostEqual(prepared.valuation_factors["pr"]["pr"], 1.0)
        self.assertNotIn("Profit-Ratio", prepared.system_prompt)

    def test_adapter_validates_original_json_shape_without_executing_llm(self) -> None:
        agent = MungerAgent()
        prepared = agent.prepare_review(
            Candidate("TEST"), FundamentalsContext("TEST", "2026-09-08T12:34:56Z", "facts")
        )
        signal = agent.parse_prepared_response(
            prepared,
            response={"signal": "neutral", "confidence": 55, "reasoning": "Insufficient evidence."},
        )
        self.assertEqual(signal.opinion, InvestorOpinion.NEUTRAL)
        self.assertEqual(signal.confidence, 55)
        self.assertEqual(signal.as_of, "2026-09-08T12:34:56Z")

    def test_adapter_rejects_invalid_opinion(self) -> None:
        with self.assertRaisesRegex(ValueError, "bullish, bearish, or neutral"):
            BuffettAgent().parse_response(
                Candidate("TEST"), data_as_of="2026-09-08",
                response={"signal": "BUY", "confidence": 100, "reasoning": "Not valid."},
            )


if __name__ == "__main__":
    unittest.main()
