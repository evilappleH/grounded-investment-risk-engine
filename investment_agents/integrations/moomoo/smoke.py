"""Hard-limited Moomoo SIMULATE broker-plumbing smoke harness.

This is deliberately not a portfolio execution route. Its intent contains no
research, committee, proposal, Risk Gate, or source-manifest fields. It can
exercise exactly one non-marketable AAPL Paper order followed by cancellation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import floor, isclose, isfinite
from time import monotonic, sleep

from investment_agents.integrations.ibkr.audit import jsonable
from investment_agents.integrations.ibkr.models import IntentState, PaperKillSwitchState
from investment_agents.path_safety import sanitized_provider_error, validate_run_id

from .adapter import MoomooPaperAdapter, MoomooSafetyError, _intent_state
from .models import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN,
    MoomooAcknowledgement,
    MoomooOrder,
    MoomooOrderSide,
    MoomooQuote,
    MoomooReconciliation,
    MoomooSecurityKind,
    MoomooSessionState,
)
from .session import PAPER_BROKER_SMOKE_TEST


SMOKE_CODE = "US.AAPL"
SMOKE_TICK_SIZE = 0.01
SIMULATE_FILL_TEST_MIN_WAIT_SECONDS = 10.0
SIMULATE_FILL_TEST_MAX_WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class MoomooPaperSmokeIntent:
    """A non-generalizable operator authorization for the Phase-1 smoke."""

    authorization_id: str
    order_intent_id: str
    quote: MoomooQuote
    limit_price: float
    created_at: datetime
    expires_at: datetime
    explicitly_human_authorized: bool
    acc_id: str
    expected_acc_id: str
    purpose: str = PAPER_BROKER_SMOKE_TEST
    broker: str = "MOOMOO"
    trd_env: str = "SIMULATE"
    market: str = "US"
    ticker: str = "AAPL"
    code: str = SMOKE_CODE
    security_kind: MoomooSecurityKind = MoomooSecurityKind.STOCK
    side: MoomooOrderSide = MoomooOrderSide.BUY
    quantity: float = 1.0
    currency: str = "USD"
    order_type: str = "LIMIT"
    time_in_force: str = "DAY"
    auto_cancel: bool = True
    tick_size: float = SMOKE_TICK_SIZE

    def __post_init__(self) -> None:
        validate_run_id(self.authorization_id)
        validate_run_id(self.order_intent_id)
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("smoke timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("smoke intent must expire after creation")
        exact = (
            self.purpose == PAPER_BROKER_SMOKE_TEST
            and self.explicitly_human_authorized
            and self.broker == "MOOMOO"
            and bool(self.acc_id.strip())
            and self.acc_id == self.expected_acc_id
            and self.trd_env == "SIMULATE"
            and self.market == "US"
            and self.ticker == "AAPL"
            and self.code == SMOKE_CODE
            and self.security_kind is MoomooSecurityKind.STOCK
            and self.side is MoomooOrderSide.BUY
            and self.quantity == 1.0
            and self.currency == "USD"
            and self.order_type == "LIMIT"
            and self.time_in_force == "DAY"
            and self.auto_cancel
            and self.tick_size == SMOKE_TICK_SIZE
            and isfinite(self.limit_price)
            and self.limit_price > 0
        )
        if not exact:
            raise ValueError("PAPER_BROKER_SMOKE_TEST_BOUNDARY_VIOLATION")
        if self.quote.code != self.code:
            raise ValueError("smoke quote code mismatch")


@dataclass(frozen=True)
class MoomooSmokePreflight:
    checked_at: datetime
    quote: MoomooQuote
    balance_currency: str
    normalized_available_cash: float
    required_notional: float
    cash_reserve: float
    reconciliation: MoomooReconciliation


@dataclass(frozen=True)
class MoomooSmokeResult:
    acknowledgement: MoomooAcknowledgement
    order_after_submission: MoomooOrder | None
    cancellation: MoomooAcknowledgement | None
    final_reconciliation: MoomooReconciliation
    unexpected_fill: bool


def non_marketable_smoke_limit(quote: MoomooQuote) -> float:
    """Return exactly one valid US tick below the current bid."""
    if quote.code != SMOKE_CODE or quote.bid is None:
        raise MoomooSafetyError("QUOTE_UNAVAILABLE")
    return floor((quote.bid - SMOKE_TICK_SIZE + 1e-12) / SMOKE_TICK_SIZE) * SMOKE_TICK_SIZE


class MoomooPaperSmokeHarness:
    """Separate Paper-only plumbing route with fixed product/account limits."""

    def __init__(self, adapter: MoomooPaperAdapter) -> None:
        self.adapter = adapter

    def build_intent(
        self,
        *,
        authorization_id: str,
        order_intent_id: str,
        quote: MoomooQuote,
        created_at: datetime,
        expires_at: datetime,
        explicitly_human_authorized: bool,
    ) -> MoomooPaperSmokeIntent:
        account_id = str(self.adapter.config.expected_acc_id or "").strip()
        if not account_id:
            raise MoomooSafetyError("MOOMOO_PAPER_ACCOUNT_NOT_CONFIGURED")
        return MoomooPaperSmokeIntent(
            authorization_id=authorization_id,
            order_intent_id=order_intent_id,
            quote=quote,
            limit_price=non_marketable_smoke_limit(quote),
            created_at=created_at,
            expires_at=expires_at,
            explicitly_human_authorized=explicitly_human_authorized,
            acc_id=account_id,
            expected_acc_id=account_id,
        )

    def _require_account_boundary(self, intent: MoomooPaperSmokeIntent) -> None:
        config = self.adapter.config
        account = self.adapter.guard.selected_account
        expected_account_id = str(config.expected_acc_id or "").strip()
        if (
            not expected_account_id
            or set(map(str, config.simulate_account_allowlist)) != {expected_account_id}
            or intent.acc_id != expected_account_id
            or intent.trd_env != "SIMULATE"
            or account is None
            or account.acc_id != expected_account_id
            or account.trd_env != "SIMULATE"
            or not account.is_compatible_us_simulation
        ):
            self.adapter.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)

    def preflight(
        self,
        intent: MoomooPaperSmokeIntent,
        *,
        kill_switch_state: PaperKillSwitchState,
        now: datetime | None = None,
        prepared: bool = False,
        require_non_marketable: bool = True,
    ) -> MoomooSmokePreflight:
        now = now or datetime.now(timezone.utc)
        if not isinstance(intent, MoomooPaperSmokeIntent):
            raise MoomooSafetyError("PAPER_BROKER_SMOKE_INTENT_REQUIRED")
        reasons: list[str] = []
        if self.adapter.guard.state not in {
            MoomooSessionState.SIMULATE_VERIFIED,
            MoomooSessionState.SIMULATE_EXECUTION_ARMED,
        }:
            reasons.append("SIMULATE_ACCOUNT_NOT_EXPLICITLY_VERIFIED")
        self._require_account_boundary(intent)
        if kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER:
            reasons.append("KILL_SWITCH_NOT_ARMED")
        utc_time = now.astimezone(timezone.utc).time().replace(tzinfo=None)
        policy = self.adapter.policy
        if now.weekday() not in policy.allowed_weekdays or not (
            policy.allowed_session_start_utc <= utc_time <= policy.allowed_session_end_utc
        ):
            reasons.append("OUTSIDE_ALLOWED_TRADING_SESSION")
        if intent.created_at > now or intent.expires_at <= now:
            reasons.append("SMOKE_INTENT_EXPIRED_OR_FUTURE")

        quote = self.adapter.quote_snapshot(SMOKE_CODE)
        if quote.status != "AVAILABLE" or quote.quoted_at is None or quote.bid is None or quote.ask is None:
            reasons.append("QUOTE_UNAVAILABLE")
        if not quote.v1_eligible_security:
            reasons.append("UNSUPPORTED_SECURITY")
        if quote.quoted_at is not None and (
            quote.quoted_at > now or now - quote.quoted_at > policy.max_quote_age
        ):
            reasons.append("STALE_OR_FUTURE_QUOTE")
        spread = quote.spread_fraction
        if spread is None or spread > policy.max_quote_spread:
            reasons.append("QUOTE_SPREAD_UNAVAILABLE_OR_TOO_WIDE")
        if require_non_marketable and quote.bid is not None and not intent.limit_price < quote.bid:
            reasons.append("SMOKE_LIMIT_MUST_BE_NON_MARKETABLE")
        if not require_non_marketable and (quote.ask is None or intent.limit_price < quote.ask):
            reasons.append("SMOKE_FILL_LIMIT_MUST_BE_MARKETABLE")
        reference = quote.ask or quote.last
        if reference is not None and abs(intent.limit_price - reference) / reference > policy.maximum_limit_price_deviation:
            reasons.append("LIMIT_PRICE_DEVIATION_EXCEEDED")
        ticks = intent.limit_price / intent.tick_size
        if not isclose(ticks, round(ticks), abs_tol=1e-9):
            reasons.append("INVALID_TICK_SIZE")

        snapshot = self.adapter.reconcile()
        if snapshot.unresolved_intent_ids:
            reasons.append("UNKNOWN_BROKER_STATE")
        other_active = tuple(item for item in self.adapter.journal.active_intent_ids if item != intent.order_intent_id)
        if other_active:
            reasons.append("OUTSTANDING_ORDER_BLOCKS_NEW_SUBMISSION")
        state = self.adapter.journal.state(intent.order_intent_id)
        allowed_states = {IntentState.CREATED, IntentState.PREFLIGHT_PASSED} if prepared else set()
        if state is not None and state not in allowed_states:
            reasons.append("DUPLICATE_SMOKE_INTENT")
        if self.adapter.journal.decision_used(intent.authorization_id, except_intent_id=intent.order_intent_id):
            reasons.append("DUPLICATE_SMOKE_AUTHORIZATION")
        active_aapl = tuple(
            row for row in snapshot.current_orders
            if row.acc_id == intent.acc_id
            and row.trd_env == "SIMULATE"
            and row.code == SMOKE_CODE
            and _intent_state(row) not in {IntentState.CANCELLED, IntentState.REJECTED}
        )
        if active_aapl:
            reasons.append("CONFLICTING_BROKER_ORDER")

        balance = snapshot.balance
        available = None
        if (
            balance.acc_id != intent.acc_id
            or balance.trd_env != "SIMULATE"
            or balance.currency != "USD"
            or balance.cash is None
            or balance.available_cash is None
            or not isfinite(balance.cash)
            or not isfinite(balance.available_cash)
        ):
            reasons.append("USD_AVAILABLE_CASH_UNAVAILABLE")
        else:
            available = min(balance.cash, balance.available_cash)
            if available < 0:
                reasons.append("USD_AVAILABLE_CASH_UNAVAILABLE")
            elif intent.quantity * intent.limit_price > available + 1e-9:
                reasons.append("AVAILABLE_CASH_EXCEEDED")
        if reasons:
            raise MoomooSafetyError(*dict.fromkeys(reasons))
        assert available is not None
        return MoomooSmokePreflight(
            now, quote, balance.currency, available,
            intent.quantity * intent.limit_price, 0.0, snapshot,
        )

    def _links(self, intent: MoomooPaperSmokeIntent, preflight: MoomooSmokePreflight):
        return {
            "execution_purpose": PAPER_BROKER_SMOKE_TEST,
            "operator_authorization": {
                "authorization_id": intent.authorization_id,
                "explicitly_human_authorized": intent.explicitly_human_authorized,
            },
            "session_identity": self.adapter.session_identity,
            "quote": jsonable(preflight.quote),
            "order_request": jsonable(intent),
            "cash_check": {
                "currency": preflight.balance_currency,
                "normalized_available_cash": preflight.normalized_available_cash,
                "required_notional": preflight.required_notional,
                "cash_reserve": preflight.cash_reserve,
            },
            "research_provenance": "NOT_APPLICABLE_TO_BROKER_PLUMBING_SMOKE",
        }

    def run_and_auto_cancel(
        self,
        intent: MoomooPaperSmokeIntent,
        *,
        kill_switch_state: PaperKillSwitchState,
        now: datetime | None = None,
        marketable_fill: bool = False,
        fill_wait_seconds: float = 15.0,
        fill_poll_interval_seconds: float = 1.0,
    ) -> MoomooSmokeResult:
        """Submit once and cancel once; callers must explicitly invoke this method."""
        if marketable_fill and not (
            SIMULATE_FILL_TEST_MIN_WAIT_SECONDS <= fill_wait_seconds <= SIMULATE_FILL_TEST_MAX_WAIT_SECONDS
        ):
            raise MoomooSafetyError("SIMULATE_FILL_TEST_WAIT_OUT_OF_BOUNDS")
        if marketable_fill and (not isfinite(fill_poll_interval_seconds) or fill_poll_interval_seconds <= 0):
            raise MoomooSafetyError("SIMULATE_FILL_TEST_POLL_INTERVAL_INVALID")
        now = now or datetime.now(timezone.utc)
        preflight = self.preflight(intent, kill_switch_state=kill_switch_state, now=now, require_non_marketable=not marketable_fill)
        links = self._links(intent, preflight)
        journal = self.adapter.journal
        journal.append("MOOMOO_SMOKE_CREATED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.CREATED, links=links)
        journal.append("MOOMOO_SMOKE_PREFLIGHT_PASSED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.PREFLIGHT_PASSED, links=links, payload={"preflight": jsonable(preflight)})
        try:
            self.adapter.guard.arm_smoke(
                authorization_id=intent.authorization_id,
                exact_account_ok=True,
                safety_preflight_ok=True,
                unused=not journal.decision_used(intent.authorization_id, except_intent_id=intent.order_intent_id),
                reconciled=not preflight.reconciliation.unresolved_intent_ids,
                kill_switch_state=kill_switch_state,
            )
        except PermissionError as exc:
            raise MoomooSafetyError("MOOMOO_SMOKE_ARMING_REJECTED") from exc

        latest = self.preflight(intent, kill_switch_state=kill_switch_state, now=now, prepared=True, require_non_marketable=not marketable_fill)
        accounts = self.adapter._refresh()
        try:
            self.adapter.guard.assert_submission_safe(
                decision_id=intent.authorization_id,
                accounts=accounts,
                required_purpose=PAPER_BROKER_SMOKE_TEST,
            )
        except PermissionError as exc:
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN) from exc
        links = self._links(intent, latest)
        journal.append("MOOMOO_SMOKE_SUBMITTING", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.SUBMITTING, links=links)
        try:
            ack = self.adapter.transport.place_limit_order(intent, intent.acc_id, trd_env="SIMULATE")
        except Exception as exc:
            journal.append("MOOMOO_SMOKE_ACK_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"error": sanitized_provider_error(exc)})
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE") from exc
        if ack.trd_env is not None and str(ack.trd_env).upper() != "SIMULATE":
            journal.append("MOOMOO_SMOKE_REAL_RESPONSE_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"code": MOOMOO_REAL_EXECUTION_FORBIDDEN})
            self.adapter.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        if ack.ambiguous or ack.order_id is None:
            journal.append("MOOMOO_SMOKE_ACK_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"broker_acknowledgement": jsonable(ack)})
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        ack_state = IntentState.ACKNOWLEDGED if ack.accepted else IntentState.REJECTED
        journal.append("MOOMOO_SMOKE_ACKNOWLEDGED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=ack_state, broker_order_id=ack.order_id, links=links, payload={"broker_acknowledgement": jsonable(ack)})
        if not ack.accepted:
            self.adapter.guard.disarm("BROKER_REJECTED")
            final = self.adapter.reconcile()
            return MoomooSmokeResult(ack, None, None, final, False)

        order = self.adapter.transport.order_status(ack.order_id, intent.acc_id, trd_env="SIMULATE")
        if order is not None:
            self.adapter._ensure_simulate(order)
        after_submit = self.adapter.reconcile()
        order = next((row for row in (*after_submit.current_orders, *after_submit.historical_orders) if row.order_id == ack.order_id), order)
        if order is None or after_submit.unresolved_intent_ids:
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        unexpected_fill = bool(order and (order.dealt_quantity > 0 or _intent_state(order) in {IntentState.PARTIALLY_FILLED, IntentState.FILLED}))
        if marketable_fill and not unexpected_fill:
            deadline = monotonic() + fill_wait_seconds
            while monotonic() < deadline:
                sleep(min(fill_poll_interval_seconds, max(0.0, deadline - monotonic())))
                polled = self.adapter.transport.order_status(ack.order_id, intent.acc_id, trd_env="SIMULATE")
                if polled is not None:
                    self.adapter._ensure_simulate(polled)
                    order = polled
                after_submit = self.adapter.reconcile()
                order = next((row for row in (*after_submit.current_orders, *after_submit.historical_orders) if row.order_id == ack.order_id), order)
                if order is None or after_submit.unresolved_intent_ids:
                    self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
                    raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
                state = _intent_state(order)
                if state in {IntentState.PARTIALLY_FILLED, IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE}:
                    unexpected_fill = bool(order.dealt_quantity > 0 or state in {IntentState.PARTIALLY_FILLED, IntentState.FILLED})
                    if unexpected_fill or state in {IntentState.CANCELLED, IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE}:
                        if unexpected_fill:
                            self.adapter.guard.disarm("UNEXPECTED_SMOKE_FILL")
                        elif state is IntentState.UNKNOWN_BROKER_STATE:
                            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
                        return MoomooSmokeResult(ack, order, None, after_submit, not marketable_fill)
        if unexpected_fill:
            self.adapter.guard.disarm("UNEXPECTED_SMOKE_FILL")
            return MoomooSmokeResult(ack, order, None, after_submit, not marketable_fill)

        accounts = self.adapter._refresh()
        try:
            self.adapter.guard.assert_submission_safe(
                decision_id=intent.authorization_id,
                accounts=accounts,
                required_purpose=PAPER_BROKER_SMOKE_TEST,
            )
        except PermissionError as exc:
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN) from exc
        journal.append("MOOMOO_SMOKE_CANCEL_PENDING", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.CANCEL_PENDING, broker_order_id=ack.order_id, links=links)
        try:
            cancellation = self.adapter.transport.cancel_order(ack.order_id, intent.acc_id, trd_env="SIMULATE")
        except Exception as exc:
            journal.append("MOOMOO_SMOKE_CANCEL_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"error": sanitized_provider_error(exc)})
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE") from exc
        if cancellation.trd_env is not None and str(cancellation.trd_env).upper() != "SIMULATE":
            journal.append("MOOMOO_SMOKE_REAL_CANCEL_RESPONSE_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"code": MOOMOO_REAL_EXECUTION_FORBIDDEN})
            self.adapter.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        if cancellation.ambiguous or not cancellation.accepted:
            journal.append("MOOMOO_SMOKE_CANCEL_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"broker_acknowledgement": jsonable(cancellation)})
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        journal.append("MOOMOO_SMOKE_CANCEL_REQUEST_ACKNOWLEDGED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, broker_order_id=ack.order_id, links=links, payload={"broker_acknowledgement": jsonable(cancellation)})
        final = self.adapter.reconcile()
        broker_confirmed_cancel = (
            journal.state(intent.order_intent_id) is IntentState.CANCELLED
            and not final.unresolved_intent_ids
            # Some OpenD builds retain a terminal CANCELLED_ALL row in the
            # current-order response. A working row is unsafe; a terminal
            # cancelled row is valid broker confirmation.
            and not any(
                row.order_id == ack.order_id
                and _intent_state(row) not in {IntentState.CANCELLED, IntentState.REJECTED}
                for row in final.current_orders
            )
        )
        if not broker_confirmed_cancel:
            journal.append("MOOMOO_SMOKE_CANCEL_NOT_CONFIRMED", order_intent_id=intent.order_intent_id, decision_id=intent.authorization_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"code": "BROKER_CANCELLATION_NOT_CONFIRMED"})
            self.adapter.guard.disarm("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        self.adapter.guard.disarm("SMOKE_WORKFLOW_COMPLETE")
        return MoomooSmokeResult(ack, order, cancellation, final, False)


__all__ = [
    "PAPER_BROKER_SMOKE_TEST", "MoomooPaperSmokeHarness",
    "MoomooPaperSmokeIntent", "MoomooSmokePreflight", "MoomooSmokeResult",
    "non_marketable_smoke_limit",
]
