"""Minimal, non-executing adapter for upstream investor-persona prompts.

Derived-interface note: the upstream ``LLMAgent`` fetches a point-in-time
snapshot and invokes an LLM.  Step 3 deliberately does neither.  This adapter
only prepares the same persona prompt and a structured review packet for a
future, explicitly authorized executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from investment_agents.data import FundamentalsContext
from investment_agents.models import (
    AgentSignal,
    AgentSignalStatus,
    Candidate,
    InvestorOpinion,
)
from investment_agents.valuation import PRMetrics


@dataclass(frozen=True)
class PreparedInvestorReview:
    """Pure data prepared for review; it does not invoke an LLM or place a trade."""

    agent_id: str
    candidate: Candidate
    data_as_of: str
    system_prompt: str
    rendered_fundamentals: str
    valuation_factors: Mapping[str, Any]


class UpstreamPersonaAdapter(ABC):
    """Adapt the upstream persona contract without its data/LLM runtime."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable upstream persona identifier."""

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the migrated upstream persona prompt unchanged."""

    def prepare_review(
        self,
        candidate: Candidate,
        fundamentals: FundamentalsContext,
        *,
        pr_metrics: PRMetrics | None = None,
    ) -> PreparedInvestorReview:
        """Attach PR as structured context, without changing the persona prompt.

        The PR factor is custom project analysis, not a Buffett/Munger rule and
        not a standalone decision.  It remains separate from the upstream
        prompt so a later executor can disclose and reason about it explicitly.
        """
        if candidate.ticker != fundamentals.ticker:
            raise ValueError("candidate and fundamentals ticker must match")

        factors: dict[str, Any] = {}
        if pr_metrics is not None:
            factors["pr"] = asdict(pr_metrics)
        return PreparedInvestorReview(
            agent_id=self.name,
            candidate=candidate,
            data_as_of=fundamentals.data_as_of,
            system_prompt=self.get_system_prompt(),
            rendered_fundamentals=fundamentals.rendered_fundamentals,
            valuation_factors=factors,
        )

    def parse_response(
        self,
        candidate: Candidate,
        *,
        data_as_of: str,
        response: Mapping[str, object],
        valuation_factors: Mapping[str, Any] | None = None,
    ) -> AgentSignal:
        """Validate the original JSON response shape without calling an LLM.

        The result retains upstream's bullish/bearish/neutral vocabulary only;
        it is a non-executable opinion, not BUY, SELL, WATCH, ranking, or an
        order instruction.
        """
        raw_opinion = response.get("signal")
        try:
            opinion = InvestorOpinion(str(raw_opinion))
        except ValueError as exc:
            raise ValueError("response signal must be bullish, bearish, or neutral") from exc

        confidence = response.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("response confidence must be a number from 0 to 100")
        if not 0 <= float(confidence) <= 100:
            raise ValueError("response confidence must be a number from 0 to 100")

        reasoning = response.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("response reasoning must be a non-empty string")

        return AgentSignal(
            agent_id=self.name,
            candidate=candidate,
            status=AgentSignalStatus.COMPLETE,
            opinion=opinion,
            confidence=float(confidence),
            reasoning=reasoning,
            valuation_factors=valuation_factors or {},
            as_of=data_as_of,
        )

    def parse_prepared_response(
        self, prepared: PreparedInvestorReview, response: Mapping[str, object]
    ) -> AgentSignal:
        """Create a signal while preserving the prepared context's exact as-of value."""
        return self.parse_response(
            prepared.candidate,
            data_as_of=prepared.data_as_of,
            response=response,
            valuation_factors=prepared.valuation_factors,
        )
