"""Minimal shared data models with no strategy, score, or trading action."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class AgentSignalStatus(StrEnum):
    """Non-trading lifecycle state for a future agent review."""

    PENDING = "pending"
    ABSTAIN = "abstain"
    COMPLETE = "complete"


class InvestorOpinion(StrEnum):
    """Non-executable investor opinion vocabulary used by the upstream personas."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Candidate:
    """An instrument identity supplied by a future universe boundary."""

    ticker: str
    source: str | None = None
    as_of: datetime | str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSignal:
    """A non-executable investor view; it is never an order or trade instruction."""

    agent_id: str
    candidate: Candidate
    status: AgentSignalStatus = AgentSignalStatus.PENDING
    opinion: InvestorOpinion | None = None
    confidence: float | None = None
    reasoning: str | None = None
    valuation_factors: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    as_of: datetime | None = None
