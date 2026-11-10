"""Boundary-only protocols for future work; no concrete implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .data import UniverseProvider
from .models import AgentSignal, Candidate


class CandidateScanner(Protocol):
    """Future boundary for transforming a universe into review candidates."""

    def scan(self, universe: Sequence[Candidate]) -> Sequence[Candidate]: ...


class InvestorAgent(Protocol):
    """Future boundary for a non-executable investor perspective."""

    def review(self, candidate: Candidate) -> AgentSignal: ...


class InvestmentCommittee(Protocol):
    """Future boundary for reviewing agent output without placing trades."""

    def deliberate(self, signals: Sequence[AgentSignal]) -> Sequence[AgentSignal]: ...


class PortfolioBoundary(Protocol):
    """Future boundary for portfolio drafting; deliberately unspecified."""

    def prepare(self, committee_output: Sequence[AgentSignal]) -> object: ...


class RiskBoundary(Protocol):
    """Future boundary for a risk review; deliberately unspecified."""

    def review(self, portfolio_draft: object) -> object: ...


class TradingAgentsHandoff(Protocol):
    """Future handoff only; no TradingAgents dependency exists in this package."""

    def handoff(self, reviewed_portfolio: object) -> None: ...
