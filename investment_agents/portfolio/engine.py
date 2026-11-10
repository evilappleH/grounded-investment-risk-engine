"""Structured allocation proposals; never broker orders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from math import isfinite
from typing import Mapping, Sequence

from investment_agents.data.universe import normalize_us_ticker
from investment_agents.path_safety import validate_run_id
from investment_agents.research.integrity import VerifiedRunManifest


POLICY_CLASSIFICATION = "EXPERIMENTAL_PROJECT_POLICY"


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"


class ProposalStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class InstrumentId:
    symbol: str
    instrument_type: InstrumentType
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_us_ticker(self.symbol))
        object.__setattr__(self, "instrument_type", InstrumentType(self.instrument_type))
        if self.currency != "USD":
            raise ValueError("only USD cash equities/ETFs are supported")

    @property
    def canonical_id(self) -> str:
        return f"US:{self.instrument_type.value}:{self.symbol}"


@dataclass(frozen=True)
class Holding:
    instrument: InstrumentId
    quantity: float
    last_price: float
    sector: str
    marked_at: datetime

    def __post_init__(self) -> None:
        if not isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("holdings must be long with positive finite quantity")
        if not isfinite(self.last_price) or self.last_price <= 0:
            raise ValueError("holding price must be positive and finite")

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price


@dataclass(frozen=True)
class PortfolioState:
    as_of: datetime
    cash_balance: float
    settled_cash: float
    positions: tuple[Holding, ...] = ()

    def __post_init__(self) -> None:
        if not isfinite(self.cash_balance) or self.cash_balance < 0:
            raise ValueError("cash_balance must be non-negative and finite")
        if not isfinite(self.settled_cash) or self.settled_cash < 0 or self.settled_cash > self.cash_balance:
            raise ValueError("settled_cash must be between zero and cash_balance")
        ids = [position.instrument.canonical_id for position in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate holding canonical instrument ID")

    @property
    def total_value(self) -> float:
        return self.cash_balance + sum(position.market_value for position in self.positions)

    @property
    def positions_by_id(self) -> Mapping[str, Holding]:
        return {position.instrument.canonical_id: position for position in self.positions}


@dataclass(frozen=True)
class ResearchEligibility:
    instrument_id: str
    source_run_id: str
    source_manifest_hash: str
    methodology_version: str
    generated_at: datetime
    data_effective_at: datetime
    qualification_status: str
    piotroski_status: str
    evidence_status: str
    provider_failures: tuple[str, ...] = ()
    unverified_model_claims: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    valuation_evidence_status: str = "COMPLETE"
    committee_status: str = "AGREEMENT"
    committee_evidence_sufficiency: str = "SUFFICIENT"
    committee_claim_ids: tuple[str, ...] = ()
    valuation_classification: str = "VALUATION_REASONABLE"
    research_lane: str = "STRICT"


@dataclass(frozen=True)
class AllocationTarget:
    instrument: InstrumentId
    target_weight: float
    sector: str
    research: ResearchEligibility


@dataclass(frozen=True)
class AllocationLine:
    canonical_instrument_id: str
    symbol: str
    instrument_type: str
    sector: str
    target_weight: float
    target_notional: float
    source_run_id: str
    evidence_status: str


@dataclass(frozen=True)
class PortfolioPolicy:
    max_research_age: timedelta = timedelta(days=1)
    supported_instrument_types: tuple[InstrumentType, ...] = (InstrumentType.EQUITY, InstrumentType.ETF)
    policy_classification: str = POLICY_CLASSIFICATION

    def __post_init__(self) -> None:
        if self.max_research_age.total_seconds() <= 0:
            raise ValueError("max_research_age must be positive")
        if self.policy_classification != POLICY_CLASSIFICATION:
            raise ValueError("portfolio numeric policy must be marked EXPERIMENTAL_PROJECT_POLICY")


@dataclass(frozen=True)
class ProposedAllocation:
    proposal_id: str
    created_at: datetime
    source_manifest_hash: str
    methodology_version: str
    base_portfolio_value: float
    allocations: tuple[AllocationLine, ...]
    cash_weight: float
    cash_notional: float
    sector_exposures: Mapping[str, float]
    status: ProposalStatus
    rejection_reasons: tuple[str, ...]
    proposal_hash: str
    policy_parameters: Mapping[str, object]
    policy_classification: str = POLICY_CLASSIFICATION


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def proposed_allocation_hash(proposal: ProposedAllocation) -> str:
    """Recompute the immutable proposal hash for downstream verification."""
    return _hash_payload({
        "proposal_id": proposal.proposal_id,
        "created_at": proposal.created_at.isoformat(),
        "source_manifest_hash": proposal.source_manifest_hash,
        "methodology_version": proposal.methodology_version,
        "base_portfolio_value": proposal.base_portfolio_value,
        "allocations": [asdict(line) for line in proposal.allocations],
        "cash_weight": proposal.cash_weight,
        "cash_notional": proposal.cash_notional,
        "sector_exposures": proposal.sector_exposures,
        "rejection_reasons": proposal.rejection_reasons,
        "status": proposal.status.value,
        "policy_parameters": proposal.policy_parameters,
        "policy_classification": proposal.policy_classification,
    })


def verify_proposed_allocation_hash(proposal: ProposedAllocation) -> bool:
    return proposal.proposal_hash == proposed_allocation_hash(proposal)


class PortfolioEngine:
    """Validate caller-supplied target weights against eligible research."""

    def __init__(self, policy: PortfolioPolicy | None = None) -> None:
        self.policy = policy or PortfolioPolicy()

    def propose(
        self,
        state: PortfolioState,
        targets: Sequence[AllocationTarget],
        *,
        verified_manifest: VerifiedRunManifest,
        proposal_id: str,
        decision_time: datetime | None = None,
    ) -> ProposedAllocation:
        proposal_id = validate_run_id(proposal_id)
        decision_time = decision_time or datetime.now(timezone.utc)
        reasons: list[str] = []
        ids = [target.instrument.canonical_id for target in targets]
        if len(ids) != len(set(ids)):
            reasons.append("DUPLICATE_TARGET_INSTRUMENT")
        total_weight = 0.0
        sector_exposures: dict[str, float] = {}
        lines: list[AllocationLine] = []
        for target in targets:
            weight = float(target.target_weight)
            if not isfinite(weight) or weight <= 0:
                reasons.append(f"INVALID_LONG_ONLY_WEIGHT:{target.instrument.canonical_id}")
                continue
            total_weight += weight
            if target.instrument.instrument_type not in self.policy.supported_instrument_types:
                reasons.append(f"UNSUPPORTED_INSTRUMENT:{target.instrument.canonical_id}")
            research = target.research
            if research.instrument_id != target.instrument.canonical_id:
                reasons.append(f"RESEARCH_INSTRUMENT_MISMATCH:{target.instrument.canonical_id}")
            if research.source_run_id != verified_manifest.run_id or research.source_manifest_hash != verified_manifest.manifest_hash:
                reasons.append(f"RESEARCH_MANIFEST_MISMATCH:{target.instrument.canonical_id}")
            if research.methodology_version != verified_manifest.methodology_version:
                reasons.append(f"RESEARCH_METHODOLOGY_MISMATCH:{target.instrument.canonical_id}")
            if research.qualification_status not in {"QUALIFIED", "QUALIFIED_WITH_WARNINGS"}:
                reasons.append(f"RESEARCH_NOT_QUALIFIED:{target.instrument.canonical_id}")
            piotroski_ok = research.piotroski_status == "COMPLETE" or (research.research_lane == "BALANCED" and research.piotroski_status == "PARTIAL")
            if not piotroski_ok or research.evidence_status != "COMPLETE":
                reasons.append(f"INCOMPLETE_RESEARCH_EVIDENCE:{target.instrument.canonical_id}")
            if research.valuation_evidence_status not in {"COMPLETE", "VALUATION_EVIDENCE_ADEQUATE", "VALUATION_EVIDENCE_STRONG"}:
                reasons.append(f"VALUATION_EVIDENCE_INSUFFICIENT:{target.instrument.canonical_id}")
            elif research.valuation_evidence_status in {"VALUATION_EVIDENCE_ADEQUATE", "VALUATION_EVIDENCE_STRONG"} and research.valuation_classification == "VALUATION_EXPENSIVE":
                reasons.append(f"VALUATION_TOO_EXPENSIVE_FOR_NEW_POSITION_V1:{target.instrument.canonical_id}")
            elif research.valuation_evidence_status in {"VALUATION_EVIDENCE_ADEQUATE", "VALUATION_EVIDENCE_STRONG"} and research.valuation_classification not in {"VALUATION_ATTRACTIVE", "VALUATION_REASONABLE"}:
                reasons.append(f"VALUATION_EVIDENCE_INSUFFICIENT:{target.instrument.canonical_id}")
            if research.committee_status == "INSUFFICIENT_EVIDENCE" or research.committee_evidence_sufficiency != "SUFFICIENT":
                reasons.append(f"COMMITTEE_EVIDENCE_INSUFFICIENT:{target.instrument.canonical_id}")
            if research.provider_failures:
                reasons.append(f"RESEARCH_PROVIDER_FAILURE:{target.instrument.canonical_id}")
            if research.unverified_model_claims or "UNVERIFIED_MODEL_CLAIM" in research.warnings:
                reasons.append(f"UNVERIFIED_MODEL_CLAIM:{target.instrument.canonical_id}")
            if research.data_effective_at > decision_time or decision_time - research.data_effective_at > self.policy.max_research_age:
                reasons.append(f"STALE_RESEARCH_DATA:{target.instrument.canonical_id}")
            if research.generated_at > decision_time:
                reasons.append(f"FUTURE_RESEARCH_TIMESTAMP:{target.instrument.canonical_id}")
            sector_exposures[target.sector] = sector_exposures.get(target.sector, 0.0) + weight
            lines.append(AllocationLine(
                target.instrument.canonical_id,
                target.instrument.symbol,
                target.instrument.instrument_type.value,
                target.sector,
                weight,
                state.total_value * weight,
                research.source_run_id,
                research.evidence_status,
            ))
        if total_weight > 1.0 + 1e-12:
            reasons.append("TARGET_WEIGHTS_EXCEED_CASH_ONLY_CAPITAL")
        if state.total_value <= 0:
            reasons.append("PORTFOLIO_VALUE_NOT_POSITIVE")
        cash_weight = max(0.0, 1.0 - total_weight)
        unique_reasons = tuple(dict.fromkeys(reasons))
        policy_parameters = {
            "max_research_age_seconds": self.policy.max_research_age.total_seconds(),
            "supported_instrument_types": tuple(item.value for item in self.policy.supported_instrument_types),
        }
        body = {
            "proposal_id": proposal_id,
            "created_at": decision_time.isoformat(),
            "source_manifest_hash": verified_manifest.manifest_hash,
            "methodology_version": verified_manifest.methodology_version,
            "base_portfolio_value": state.total_value,
            "allocations": [asdict(line) for line in lines],
            "cash_weight": cash_weight,
            "cash_notional": state.total_value * cash_weight,
            "sector_exposures": sector_exposures,
            "rejection_reasons": unique_reasons,
            "status": (ProposalStatus.REJECTED if reasons else ProposalStatus.ACCEPTED).value,
            "policy_parameters": policy_parameters,
            "policy_classification": self.policy.policy_classification,
        }
        return ProposedAllocation(
            proposal_id=proposal_id,
            created_at=decision_time,
            source_manifest_hash=verified_manifest.manifest_hash,
            methodology_version=verified_manifest.methodology_version,
            base_portfolio_value=state.total_value,
            allocations=tuple(lines),
            cash_weight=cash_weight,
            cash_notional=state.total_value * cash_weight,
            sector_exposures=sector_exposures,
            status=ProposalStatus.REJECTED if reasons else ProposalStatus.ACCEPTED,
            rejection_reasons=unique_reasons,
            proposal_hash=_hash_payload(body),
            policy_parameters=policy_parameters,
            policy_classification=self.policy.policy_classification,
        )
