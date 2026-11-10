"""Typed, Paper-only IBKR boundary models.

No model contains credentials and there is deliberately no live-execution state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from math import isfinite
from typing import Mapping

from investment_agents.data.universe import normalize_us_ticker
from investment_agents.path_safety import validate_run_id


EXECUTION_POLICY_CLASSIFICATION = "EXPERIMENTAL_EXECUTION_POLICY"


class SessionState(StrEnum):
    READ_ONLY_PROBE = "READ_ONLY_PROBE"
    PAPER_VERIFIED = "PAPER_VERIFIED"
    PAPER_EXECUTION_ARMED = "PAPER_EXECUTION_ARMED"
    DISARMED = "DISARMED"


class PaperKillSwitchState(StrEnum):
    BLOCKED = "BLOCKED"
    ARMED_FOR_PAPER = "ARMED_FOR_PAPER"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL_TO_REDUCE_EXISTING_LONG = "SELL_TO_REDUCE_EXISTING_LONG"


class IntentState(StrEnum):
    CREATED = "CREATED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN_BROKER_STATE = "UNKNOWN_BROKER_STATE"


TERMINAL_INTENT_STATES = frozenset({IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED})


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _positive(value: float, field_name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be positive and finite")


@dataclass(frozen=True)
class IBKRConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 71
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_paper_ports: tuple[int, ...] = (7497, 4002)
    paper_account_allowlist: tuple[str, ...] = ()
    live_account_denylist: tuple[str, ...] = ()
    expected_account_id: str | None = None
    fractional_quantities_supported: bool = False
    request_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.host or self.host not in self.allowed_hosts:
            raise ValueError("IBKR host is not explicitly allowed")
        if self.port not in self.allowed_paper_ports:
            raise ValueError("IBKR port is not an explicitly allowed Paper socket port")
        if not isinstance(self.client_id, int) or self.client_id < 0:
            raise ValueError("client_id must be a non-negative integer")
        if not isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        allow = set(self.paper_account_allowlist)
        deny = set(self.live_account_denylist)
        if "" in allow or "" in deny or allow & deny:
            raise ValueError("account allowlist/denylist is invalid or overlapping")
        if self.expected_account_id is not None and not self.expected_account_id:
            raise ValueError("expected_account_id cannot be blank")


@dataclass(frozen=True)
class ExecutionSafetyPolicy:
    max_decision_age: timedelta = timedelta(minutes=5)
    max_quote_age: timedelta = timedelta(seconds=20)
    max_quote_spread: float = 0.01
    maximum_limit_price_deviation: float = 0.01
    allowed_session_start_utc: time = time(13, 30)
    allowed_session_end_utc: time = time(20, 0)
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    allowed_time_in_force: tuple[str, ...] = ("DAY",)
    policy_classification: str = EXECUTION_POLICY_CLASSIFICATION

    def __post_init__(self) -> None:
        if self.max_decision_age.total_seconds() <= 0 or self.max_quote_age.total_seconds() <= 0:
            raise ValueError("execution freshness limits must be positive")
        for name in ("max_quote_spread", "maximum_limit_price_deviation"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0 or value > 1:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.allowed_session_start_utc >= self.allowed_session_end_utc:
            raise ValueError("allowed UTC session cannot wrap midnight")
        if not self.allowed_weekdays or any(day not in range(7) for day in self.allowed_weekdays):
            raise ValueError("allowed_weekdays is invalid")
        if self.policy_classification != EXECUTION_POLICY_CLASSIFICATION:
            raise ValueError("execution policy must be EXPERIMENTAL_EXECUTION_POLICY")


@dataclass(frozen=True)
class BrokerConnectionInfo:
    host: str
    port: int
    client_id: int
    connected_at: datetime
    server_version: int | None
    account_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _aware(self.connected_at, "connected_at")


@dataclass(frozen=True)
class AccountSummary:
    account_id: str
    account_type: str | None = None
    total_cash: float | None = None
    settled_cash: float | None = None
    available_funds: float | None = None
    currency: str = "USD"
    values: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerPosition:
    account_id: str
    con_id: int
    symbol: str
    security_type: str
    currency: str
    exchange: str
    quantity: float
    average_cost: float | None = None


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: int
    permanent_id: int | None
    order_ref: str | None
    account_id: str | None
    con_id: int
    symbol: str
    security_type: str
    currency: str
    action: str
    order_type: str
    total_quantity: float
    limit_price: float | None
    time_in_force: str | None
    status: str
    filled_quantity: float = 0.0
    remaining_quantity: float | None = None


@dataclass(frozen=True)
class BrokerExecution:
    execution_id: str
    broker_order_id: int
    permanent_id: int | None
    order_ref: str | None
    account_id: str
    con_id: int
    side: str
    quantity: float
    price: float
    executed_at: datetime | None = None
    commission: float | None = None
    currency: str | None = None


@dataclass(frozen=True)
class ResolvedContract:
    con_id: int
    symbol: str
    security_type: str
    currency: str
    exchange: str
    primary_exchange: str | None = None
    local_symbol: str | None = None
    fractional_quantities_supported: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.con_id, int) or self.con_id <= 0:
            raise ValueError("con_id must be a positive integer")


@dataclass(frozen=True)
class IBKRQuote:
    con_id: int
    bid: float | None
    ask: float | None
    last: float | None
    quoted_at: datetime
    status: str = "AVAILABLE"

    def __post_init__(self) -> None:
        _aware(self.quoted_at, "quoted_at")
        for name in ("bid", "ask", "last"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool):
                    raise ValueError(f"{name} must be numeric")
                value = float(value)
                _positive(value, name)
                object.__setattr__(self, name, value)

    @property
    def spread_fraction(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask < self.bid:
            return None
        midpoint = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / midpoint if midpoint > 0 else None

    def executable_reference(self, side: OrderSide) -> float | None:
        if side is OrderSide.BUY:
            return self.ask or self.last
        return self.bid or self.last


@dataclass(frozen=True)
class PaperOrderIntent:
    decision_id: str
    order_intent_id: str
    source_run_id: str
    source_manifest_hash: str
    methodology_version: str
    portfolio_proposal_hash: str
    risk_result_hash: str
    ticker: str
    con_id: int
    side: OrderSide
    quantity: float
    currency: str
    exchange: str
    decision_price: float
    quote: IBKRQuote
    limit_price: float
    time_in_force: str
    created_at: datetime
    expires_at: datetime
    security_type: str = "STK"
    order_type: str = "LMT"

    def __post_init__(self) -> None:
        validate_run_id(self.decision_id)
        validate_run_id(self.order_intent_id)
        validate_run_id(self.source_run_id)
        object.__setattr__(self, "side", OrderSide(self.side))
        object.__setattr__(self, "ticker", normalize_us_ticker(self.ticker))
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("order intent must expire after creation")
        for name in ("quantity", "decision_price", "limit_price"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            value = float(value)
            _positive(value, name)
            object.__setattr__(self, name, value)
        if isinstance(self.con_id, bool) or not isinstance(self.con_id, int) or self.con_id <= 0:
            raise ValueError("con_id must be positive")
        if self.security_type != "STK":
            raise ValueError("only STK contracts are supported")
        if self.currency != "USD":
            raise ValueError("only USD cash-funded instruments are supported")
        if self.order_type != "LMT":
            raise ValueError("only LIMIT/LMT orders are supported")
        if not self.exchange or self.exchange.upper() in {"BAG", "FX"}:
            raise ValueError("an explicit stock exchange/routing value is required")
        if self.quote.con_id != self.con_id:
            raise ValueError("quote con_id does not match intent contract")


@dataclass(frozen=True)
class BrokerAcknowledgement:
    accepted: bool
    broker_order_id: int | None
    permanent_id: int | None
    status: str
    acknowledged_at: datetime
    rejection_code: str | None = None
    ambiguous: bool = False

    def __post_init__(self) -> None:
        _aware(self.acknowledged_at, "acknowledged_at")


@dataclass(frozen=True)
class ReconciliationSnapshot:
    reconciled_at: datetime
    account_summary: AccountSummary
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOrder, ...]
    completed_orders: tuple[BrokerOrder, ...]
    executions: tuple[BrokerExecution, ...]
    unresolved_intent_ids: tuple[str, ...] = ()
