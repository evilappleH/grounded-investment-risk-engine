"""Deterministic pre-trade gate for simulator-only allocation intents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from math import isfinite
from typing import Mapping

from investment_agents.portfolio import POLICY_CLASSIFICATION, PortfolioState, ProposalStatus, ProposedAllocation, verify_proposed_allocation_hash


class RiskStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Quote:
    canonical_instrument_id: str
    price: float
    quoted_at: datetime
    provider_status: str = "AVAILABLE"

    def __post_init__(self) -> None:
        if not isfinite(self.price) or self.price <= 0:
            raise ValueError("quote price must be positive and finite")


@dataclass(frozen=True)
class KillSwitch:
    active: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class RiskContext:
    environment: str = "SIMULATOR"
    account_identifier: str | None = None
    daily_turnover_fraction: float = 0.0
    seen_decision_ids: tuple[str, ...] = ()
    seen_decision_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskConfig:
    max_single_position_exposure: float = 0.10
    max_sector_exposure: float = 0.25
    max_daily_turnover: float = 0.20
    max_decision_age: timedelta = timedelta(minutes=5)
    max_quote_age: timedelta = timedelta(seconds=30)
    policy_classification: str = POLICY_CLASSIFICATION

    def __post_init__(self) -> None:
        for name in ("max_single_position_exposure", "max_sector_exposure", "max_daily_turnover"):
            value = getattr(self, name)
            if not isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be within (0, 1]")
        if self.max_decision_age.total_seconds() <= 0 or self.max_quote_age.total_seconds() <= 0:
            raise ValueError("freshness limits must be positive")
        if self.policy_classification != POLICY_CLASSIFICATION:
            raise ValueError("risk limits must be marked EXPERIMENTAL_PROJECT_POLICY")


@dataclass(frozen=True)
class RiskCheckedTrade:
    canonical_instrument_id: str
    symbol: str
    side: str
    quantity: float
    notional: float
    quote_price: float
    quote_timestamp: datetime
    sector: str


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    decision_hash: str
    checked_at: datetime
    status: RiskStatus
    rejection_reasons: tuple[str, ...]
    source_manifest_hash: str
    proposal_hash: str
    gross_purchase_notional: float
    turnover_fraction: float
    projected_cash: float
    trades: tuple[RiskCheckedTrade, ...]
    risk_limits: Mapping[str, object] = field(default_factory=dict)
    policy_classification: str = POLICY_CLASSIFICATION
    environment: str = "SIMULATOR"


def risk_decision_hash(decision: RiskDecision) -> str:
    """Hash every material decision output except the hash field itself."""
    return _canonical_hash({
        "decision_id": decision.decision_id,
        "checked_at": decision.checked_at.isoformat(),
        "status": decision.status.value,
        "rejection_reasons": decision.rejection_reasons,
        "source_manifest_hash": decision.source_manifest_hash,
        "proposal_hash": decision.proposal_hash,
        "gross_purchase_notional": decision.gross_purchase_notional,
        "turnover_fraction": decision.turnover_fraction,
        "projected_cash": decision.projected_cash,
        "trades": [asdict(trade) for trade in decision.trades],
        "risk_limits": decision.risk_limits,
        "policy_classification": decision.policy_classification,
        "environment": decision.environment,
    })


def verify_risk_decision_hash(decision: RiskDecision) -> bool:
    return decision.decision_hash == risk_decision_hash(decision)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class RiskGate:
    """Apply non-overridable cash/long-only simulator controls."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def evaluate(
        self,
        proposal: ProposedAllocation,
        state: PortfolioState,
        quotes: Mapping[str, Quote],
        *,
        context: RiskContext | None = None,
        kill_switch: KillSwitch | None = None,
        checked_at: datetime | None = None,
    ) -> RiskDecision:
        context = context or RiskContext()
        kill_switch = kill_switch or KillSwitch()
        checked_at = checked_at or datetime.now(timezone.utc)
        reasons: list[str] = []
        if context.environment != "SIMULATOR" or context.account_identifier is not None:
            reasons.append("LIVE_OR_EXTERNAL_ACCOUNT_FORBIDDEN")
        if kill_switch.active:
            reasons.append("KILL_SWITCH_ACTIVE")
        if proposal.status is not ProposalStatus.ACCEPTED:
            reasons.append("PROPOSAL_REJECTED")
        if not verify_proposed_allocation_hash(proposal):
            reasons.append("PROPOSAL_HASH_MISMATCH")
        if proposal.policy_classification != POLICY_CLASSIFICATION:
            reasons.append("UNAPPROVED_POLICY_CLASSIFICATION")
        duplicate_decision_id = proposal.proposal_id in context.seen_decision_ids
        if proposal.created_at > checked_at or checked_at - proposal.created_at > self.config.max_decision_age:
            reasons.append("STALE_OR_FUTURE_DECISION")
        if state.as_of > checked_at or checked_at - state.as_of > self.config.max_decision_age:
            reasons.append("STALE_OR_FUTURE_PORTFOLIO_STATE")

        target_by_id = {line.canonical_instrument_id: line for line in proposal.allocations}
        current_by_id = {holding.instrument.canonical_id: holding for holding in state.positions}
        all_ids = tuple(sorted(set(target_by_id) | set(current_by_id)))
        trades: list[RiskCheckedTrade] = []
        gross_purchases = 0.0
        turnover_notional = 0.0
        gross_sales = 0.0
        for instrument_id in all_ids:
            line = target_by_id.get(instrument_id)
            holding = current_by_id.get(instrument_id)
            quote = quotes.get(instrument_id)
            if quote is None or quote.canonical_instrument_id != instrument_id:
                reasons.append(f"QUOTE_MISSING:{instrument_id}")
                continue
            if quote.provider_status != "AVAILABLE":
                reasons.append(f"QUOTE_PROVIDER_FAILURE:{instrument_id}")
            if quote.quoted_at > checked_at or checked_at - quote.quoted_at > self.config.max_quote_age:
                reasons.append(f"STALE_OR_FUTURE_QUOTE:{instrument_id}")
            target_notional = line.target_notional if line else 0.0
            current_notional = holding.quantity * quote.price if holding else 0.0
            delta = target_notional - current_notional
            turnover_notional += abs(delta)
            if abs(delta) <= 1e-9:
                continue
            side = "BUY" if delta > 0 else "SELL"
            if side == "BUY":
                gross_purchases += delta
            else:
                gross_sales += -delta
                if holding is None or (-delta / quote.price) > holding.quantity + 1e-12:
                    reasons.append(f"SHORT_SALE_FORBIDDEN:{instrument_id}")
            trades.append(RiskCheckedTrade(
                instrument_id,
                line.symbol if line else holding.instrument.symbol,
                side,
                abs(delta) / quote.price,
                abs(delta),
                quote.price,
                quote.quoted_at,
                line.sector if line else holding.sector,
            ))

        for line in proposal.allocations:
            if line.instrument_type not in {"EQUITY", "ETF"}:
                reasons.append(f"UNSUPPORTED_INSTRUMENT:{line.canonical_instrument_id}")
            if line.target_weight < 0:
                reasons.append(f"SHORT_OR_NEGATIVE_WEIGHT:{line.canonical_instrument_id}")
            if line.target_weight > self.config.max_single_position_exposure + 1e-12:
                reasons.append(f"MAX_SINGLE_POSITION_EXCEEDED:{line.canonical_instrument_id}")
            if line.evidence_status != "COMPLETE":
                reasons.append(f"INCOMPLETE_EVIDENCE:{line.canonical_instrument_id}")
        for sector, exposure in proposal.sector_exposures.items():
            if exposure > self.config.max_sector_exposure + 1e-12:
                reasons.append(f"MAX_SECTOR_EXPOSURE_EXCEEDED:{sector}")
        if gross_purchases > state.settled_cash + 1e-9:
            reasons.append("SETTLED_CASH_EXCEEDED")
        turnover = turnover_notional / proposal.base_portfolio_value if proposal.base_portfolio_value > 0 else float("inf")
        if not isfinite(context.daily_turnover_fraction) or context.daily_turnover_fraction < 0:
            reasons.append("INVALID_EXISTING_DAILY_TURNOVER")
        elif context.daily_turnover_fraction + turnover > self.config.max_daily_turnover + 1e-12:
            reasons.append("MAX_DAILY_TURNOVER_EXCEEDED")
        projected_cash = state.cash_balance - gross_purchases + gross_sales
        if projected_cash < -1e-9:
            reasons.append("CASH_ONLY_BALANCE_VIOLATION")

        unique_reasons = tuple(dict.fromkeys(reasons))
        provisional = RiskDecision(
            decision_id=proposal.proposal_id,
            decision_hash="",
            checked_at=checked_at,
            status=RiskStatus.REJECTED if unique_reasons else RiskStatus.APPROVED,
            rejection_reasons=unique_reasons,
            source_manifest_hash=proposal.source_manifest_hash,
            proposal_hash=proposal.proposal_hash,
            gross_purchase_notional=gross_purchases,
            turnover_fraction=turnover,
            projected_cash=projected_cash,
            trades=tuple(trades),
            risk_limits={
                "max_single_position_exposure": self.config.max_single_position_exposure,
                "max_sector_exposure": self.config.max_sector_exposure,
                "max_daily_turnover": self.config.max_daily_turnover,
                "max_decision_age_seconds": self.config.max_decision_age.total_seconds(),
                "max_quote_age_seconds": self.config.max_quote_age.total_seconds(),
            },
            policy_classification=self.config.policy_classification,
            environment="SIMULATOR",
        )
        candidate_hash = risk_decision_hash(provisional)
        duplicate_reasons: list[str] = []
        if duplicate_decision_id:
            duplicate_reasons.append("DUPLICATE_DECISION_ID")
        if candidate_hash in context.seen_decision_hashes:
            duplicate_reasons.append("DUPLICATE_DECISION_HASH")
        if duplicate_reasons:
            unique_reasons = (*unique_reasons, *duplicate_reasons)
            provisional = RiskDecision(**{
                **provisional.__dict__,
                "status": RiskStatus.REJECTED,
                "rejection_reasons": unique_reasons,
            })
        return RiskDecision(**{**provisional.__dict__, "decision_hash": risk_decision_hash(provisional)})
