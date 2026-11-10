"""Typed Moomoo OpenD models with no REAL execution state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Mapping

from investment_agents.data.universe import normalize_us_ticker
from investment_agents.path_safety import validate_run_id
from investment_agents.integrations.ibkr.models import PaperKillSwitchState


MOOMOO_REAL_EXECUTION_FORBIDDEN = "MOOMOO_REAL_EXECUTION_FORBIDDEN"


@dataclass(frozen=True)
class MoomooCapabilityProfile:
    supports_current_order_query: bool
    supports_historical_order_query: bool
    supports_position_query: bool
    supports_current_deal_query: bool
    supports_historical_deal_query: bool
    supports_deal_push: bool


MOOMOO_SIMULATE_CAPABILITIES = MoomooCapabilityProfile(
    supports_current_order_query=True,
    supports_historical_order_query=True,
    supports_position_query=True,
    supports_current_deal_query=False,
    supports_historical_deal_query=False,
    supports_deal_push=False,
)


class MoomooSessionState(StrEnum):
    READ_ONLY_PROBE = "READ_ONLY_PROBE"
    SIMULATE_VERIFIED = "SIMULATE_VERIFIED"
    SIMULATE_EXECUTION_ARMED = "SIMULATE_EXECUTION_ARMED"
    DISARMED = "DISARMED"


class MoomooOrderSide(StrEnum):
    BUY = "BUY"
    SELL_TO_REDUCE_EXISTING_LONG = "SELL_TO_REDUCE_EXISTING_LONG"


class MoomooSecurityKind(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"


def _aware(value: datetime | None, name: str) -> None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return number


@dataclass(frozen=True)
class MoomooConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 11111
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_ports: tuple[int, ...] = (11111,)
    simulate_account_allowlist: tuple[str, ...] = ()
    expected_acc_id: str | None = None
    request_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.host not in self.allowed_hosts:
            raise ValueError("Moomoo OpenD host is not explicitly allowed")
        if self.port not in self.allowed_ports:
            raise ValueError("Moomoo OpenD port is not explicitly allowed")
        if not isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        if any(not str(item).strip() for item in self.simulate_account_allowlist):
            raise ValueError("simulate account allowlist contains a blank ID")
        if self.expected_acc_id is not None and not str(self.expected_acc_id).strip():
            raise ValueError("expected_acc_id cannot be blank")


@dataclass(frozen=True)
class MoomooAccount:
    acc_id: str
    trd_env: str
    acc_type: str
    sim_acc_type: str
    trdmarket_auth: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "acc_id", str(self.acc_id))
        object.__setattr__(self, "trd_env", str(self.trd_env).upper())
        object.__setattr__(self, "acc_type", str(self.acc_type).upper())
        object.__setattr__(self, "sim_acc_type", str(self.sim_acc_type).upper())
        object.__setattr__(self, "trdmarket_auth", tuple(str(item).upper() for item in self.trdmarket_auth))

    @property
    def is_compatible_us_simulation(self) -> bool:
        return (
            self.trd_env == "SIMULATE"
            and "US" in self.trdmarket_auth
            and self.sim_acc_type in {"STOCK", "STOCK_AND_OPTION"}
        )


@dataclass(frozen=True)
class MoomooConnectionInfo:
    host: str
    port: int
    connected_at: datetime
    open_d_version: str | None
    accounts: tuple[MoomooAccount, ...]

    def __post_init__(self) -> None:
        _aware(self.connected_at, "connected_at")


@dataclass(frozen=True)
class MoomooBalance:
    acc_id: str
    trd_env: str
    currency: str
    cash: float | None
    available_cash: float | None
    values: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MoomooPosition:
    acc_id: str
    trd_env: str
    code: str
    quantity: float
    can_sell_quantity: float
    market_value: float | None = None


@dataclass(frozen=True)
class MoomooOrder:
    order_id: str
    acc_id: str
    trd_env: str
    code: str
    side: str
    order_type: str
    status: str
    quantity: float
    price: float
    dealt_quantity: float = 0.0
    dealt_average_price: float | None = None
    remark: str | None = None
    time_in_force: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class MoomooDeal:
    deal_id: str
    order_id: str
    acc_id: str
    trd_env: str
    code: str
    side: str
    quantity: float
    price: float
    dealt_at: datetime | None = None
    fee: float | None = None


@dataclass(frozen=True)
class MoomooQuote:
    code: str
    bid: float | None
    ask: float | None
    last: float | None
    quoted_at: datetime | None
    v1_eligible_security: bool
    status: str = "AVAILABLE"

    def __post_init__(self) -> None:
        if not self.code.startswith("US."):
            raise ValueError("only US quote codes are supported")
        if self.quoted_at is not None:
            _aware(self.quoted_at, "quoted_at")
        for name in ("bid", "ask", "last"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _positive(value, name))

    @property
    def spread_fraction(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask < self.bid:
            return None
        midpoint = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / midpoint if midpoint > 0 else None

    def executable_reference(self, side: MoomooOrderSide) -> float | None:
        return (self.ask or self.last) if side is MoomooOrderSide.BUY else (self.bid or self.last)


@dataclass(frozen=True)
class MoomooOrderIntent:
    decision_id: str
    order_intent_id: str
    source_run_id: str
    source_manifest_hash: str
    methodology_version: str
    portfolio_proposal_hash: str
    risk_result_hash: str
    ticker: str
    code: str
    security_kind: MoomooSecurityKind
    side: MoomooOrderSide
    quantity: float
    currency: str
    decision_price: float
    quote: MoomooQuote
    limit_price: float
    created_at: datetime
    expires_at: datetime
    order_type: str = "LIMIT"
    time_in_force: str = "DAY"

    def __post_init__(self) -> None:
        for value in (self.decision_id, self.order_intent_id, self.source_run_id):
            validate_run_id(value)
        object.__setattr__(self, "ticker", normalize_us_ticker(self.ticker))
        object.__setattr__(self, "security_kind", MoomooSecurityKind(self.security_kind))
        object.__setattr__(self, "side", MoomooOrderSide(self.side))
        if self.code != f"US.{self.ticker}":
            raise ValueError("Moomoo code must exactly match canonical US ticker")
        for name in ("quantity", "decision_price", "limit_price"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("order intent must expire after creation")
        if self.currency != "USD" or self.order_type != "LIMIT" or self.time_in_force != "DAY":
            raise ValueError("Moomoo V1 supports only USD LIMIT DAY orders")
        if self.quote.code != self.code:
            raise ValueError("quote code does not match order intent")


@dataclass(frozen=True)
class MoomooAcknowledgement:
    accepted: bool
    order_id: str | None
    trd_env: str | None
    status: str
    acknowledged_at: datetime
    ambiguous: bool = False
    rejection_code: str | None = None

    def __post_init__(self) -> None:
        _aware(self.acknowledged_at, "acknowledged_at")


@dataclass(frozen=True)
class MoomooReconciliation:
    reconciled_at: datetime
    balance: MoomooBalance
    positions: tuple[MoomooPosition, ...]
    current_orders: tuple[MoomooOrder, ...]
    historical_orders: tuple[MoomooOrder, ...]
    deals: tuple[MoomooDeal, ...]
    unresolved_intent_ids: tuple[str, ...] = ()


__all__ = [
    "MOOMOO_REAL_EXECUTION_FORBIDDEN", "MOOMOO_SIMULATE_CAPABILITIES",
    "MoomooCapabilityProfile", "MoomooAccount", "MoomooAcknowledgement",
    "MoomooBalance", "MoomooConnectionConfig", "MoomooConnectionInfo",
    "MoomooDeal", "MoomooOrder", "MoomooOrderIntent", "MoomooOrderSide",
    "MoomooPosition", "MoomooQuote", "MoomooReconciliation",
    "MoomooSecurityKind", "MoomooSessionState", "PaperKillSwitchState",
]
