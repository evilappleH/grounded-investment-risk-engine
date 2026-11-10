"""Offline smoke tests for the strategy-free package boundary."""

from __future__ import annotations

import unittest

from investment_agents import AgentSignal, AgentSignalStatus, Candidate
from investment_agents.interfaces import (
    CandidateScanner,
    InvestmentCommittee,
    InvestorAgent,
    PortfolioBoundary,
    RiskBoundary,
    TradingAgentsHandoff,
    UniverseProvider,
)


class InvestmentAgentsSkeletonTest(unittest.TestCase):
    def test_models_are_non_directional_and_importable(self) -> None:
        candidate = Candidate(ticker="EXAMPLE")
        signal = AgentSignal(agent_id="placeholder", candidate=candidate)

        self.assertEqual(signal.status, AgentSignalStatus.PENDING)
        self.assertNotIn(signal.status.value, {"buy", "sell", "watch"})

    def test_boundary_protocols_are_importable(self) -> None:
        self.assertEqual(
            [
                UniverseProvider.__name__,
                CandidateScanner.__name__,
                InvestorAgent.__name__,
                InvestmentCommittee.__name__,
                PortfolioBoundary.__name__,
                RiskBoundary.__name__,
                TradingAgentsHandoff.__name__,
            ],
            [
                "UniverseProvider",
                "CandidateScanner",
                "InvestorAgent",
                "InvestmentCommittee",
                "PortfolioBoundary",
                "RiskBoundary",
                "TradingAgentsHandoff",
            ],
        )


if __name__ == "__main__":
    unittest.main()
