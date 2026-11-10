"""Deterministic fake OpenD transport for local Moomoo safety tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import (
    MOOMOO_SIMULATE_CAPABILITIES, MoomooAccount, MoomooAcknowledgement, MoomooBalance, MoomooCapabilityProfile, MoomooConnectionInfo,
    MoomooDeal, MoomooOrder, MoomooOrderIntent, MoomooPosition, MoomooQuote,
)

if TYPE_CHECKING:
    from .smoke import MoomooPaperSmokeIntent


class InMemoryMoomooTransport:
    capabilities = MOOMOO_SIMULATE_CAPABILITIES
    def __init__(
        self,
        *,
        accounts: tuple[MoomooAccount, ...],
        balance: MoomooBalance | None = None,
        positions: tuple[MoomooPosition, ...] = (),
        quote: MoomooQuote | None = None,
    ) -> None:
        self.accounts = accounts
        acc_id = accounts[0].acc_id if len(accounts) == 1 else ""
        self.balance_row = balance or MoomooBalance(acc_id, "SIMULATE", "USD", 100_000, 100_000)
        self.position_rows = list(positions)
        self.quote = quote
        self.current_order_rows: dict[str, MoomooOrder] = {}
        self.history_order_rows: dict[str, MoomooOrder] = {}
        self.deal_rows: list[MoomooDeal] = []
        self.connected_info: MoomooConnectionInfo | None = None
        self.next_order_id = 7000
        self.place_behavior = "ACKNOWLEDGE"
        self.cancel_behavior = "CANCEL"
        self.heartbeat_available = True
        self.calls: list[tuple[str, str | None]] = []

    @staticmethod
    def _require_simulate(trd_env: str) -> None:
        if trd_env != "SIMULATE":
            raise PermissionError("MOOMOO_REAL_EXECUTION_FORBIDDEN")

    def connect(self, host: str, port: int) -> MoomooConnectionInfo:
        self.calls.append(("connect", None))
        self.connected_info = MoomooConnectionInfo(host, port, datetime.now(timezone.utc), "FAKE-OPEND", self.accounts)
        return self.connected_info

    def disconnect(self) -> None:
        self.calls.append(("disconnect", None))
        self.connected_info = None

    def is_connected(self) -> bool:
        return self.connected_info is not None

    def heartbeat(self) -> bool:
        self.calls.append(("heartbeat", None))
        return self.is_connected() and self.heartbeat_available

    def discover_accounts(self) -> tuple[MoomooAccount, ...]:
        self.calls.append(("discover_accounts", None))
        return self.accounts

    def account_balance(self, acc_id: str, *, trd_env: str) -> MoomooBalance:
        self._require_simulate(trd_env); self.calls.append(("account_balance", trd_env))
        return replace(self.balance_row, acc_id=acc_id)

    def positions(self, acc_id: str, *, trd_env: str) -> tuple[MoomooPosition, ...]:
        self._require_simulate(trd_env); self.calls.append(("positions", trd_env))
        return tuple(self.position_rows)

    def current_orders(self, acc_id: str, *, trd_env: str) -> tuple[MoomooOrder, ...]:
        self._require_simulate(trd_env); self.calls.append(("current_orders", trd_env))
        return tuple(self.current_order_rows.values())

    def historical_orders(self, acc_id: str, *, trd_env: str) -> tuple[MoomooOrder, ...]:
        self._require_simulate(trd_env); self.calls.append(("historical_orders", trd_env))
        return tuple(self.history_order_rows.values())

    def deals(self, acc_id: str, *, trd_env: str) -> tuple[MoomooDeal, ...]:
        self._require_simulate(trd_env); self.calls.append(("deals", trd_env))
        return tuple(self.deal_rows)

    def quote_snapshot(self, code: str) -> MoomooQuote:
        self.calls.append(("quote_snapshot", None))
        if self.quote is None or self.quote.code != code:
            return MoomooQuote(code, None, None, None, None, False, "UNAVAILABLE")
        return self.quote

    def place_limit_order(self, intent: MoomooOrderIntent | MoomooPaperSmokeIntent, acc_id: str, *, trd_env: str) -> MoomooAcknowledgement:
        self._require_simulate(trd_env); self.calls.append(("place_limit_order", trd_env))
        now = datetime.now(timezone.utc)
        order_id = str(self.next_order_id); self.next_order_id += 1
        if self.place_behavior == "AMBIGUOUS":
            return MoomooAcknowledgement(False, None, None, "UNKNOWN", now, True)
        if self.place_behavior == "REAL_RESPONSE":
            return MoomooAcknowledgement(False, order_id, "real", "SUBMITTED", now)
        if self.place_behavior == "REJECT":
            return MoomooAcknowledgement(False, order_id, "SIMULATE", "FAILED", now, False, "SIMULATED_REJECT")
        self._store_order(order_id, intent, acc_id)
        if self.place_behavior == "RAISE_AFTER_ACCEPT":
            raise TimeoutError("simulated OpenD acknowledgement loss")
        return MoomooAcknowledgement(True, order_id, "SIMULATE", "SUBMITTED", now)

    def _store_order(self, order_id: str, intent: MoomooOrderIntent | MoomooPaperSmokeIntent, acc_id: str) -> None:
        side = "BUY" if intent.side.value == "BUY" else "SELL"
        self.current_order_rows[order_id] = MoomooOrder(
            order_id, acc_id, "SIMULATE", intent.code, side, "NORMAL", "SUBMITTED",
            intent.quantity, intent.limit_price, remark=intent.order_intent_id,
            time_in_force="DAY", updated_at=datetime.now(timezone.utc),
        )

    def cancel_order(self, order_id: str, acc_id: str, *, trd_env: str) -> MoomooAcknowledgement:
        self._require_simulate(trd_env); self.calls.append(("cancel_order", trd_env))
        now = datetime.now(timezone.utc)
        if self.cancel_behavior == "AMBIGUOUS":
            return MoomooAcknowledgement(False, order_id, None, "UNKNOWN", now, True)
        if self.cancel_behavior == "REAL_RESPONSE":
            return MoomooAcknowledgement(False, order_id, "real", "UNKNOWN", now)
        order = self.current_order_rows.pop(order_id)
        self.history_order_rows[order_id] = replace(order, status="CANCELLED_ALL", updated_at=now)
        return MoomooAcknowledgement(True, order_id, "SIMULATE", "CANCELLED_ALL", now)

    def order_status(self, order_id: str, acc_id: str, *, trd_env: str) -> MoomooOrder | None:
        self._require_simulate(trd_env); self.calls.append(("order_status", trd_env))
        return self.current_order_rows.get(order_id) or self.history_order_rows.get(order_id)

    def fill(self, order_id: str, quantity: float, price: float, *, fee: float | None = None) -> None:
        order = self.current_order_rows[order_id]
        dealt = order.dealt_quantity + quantity
        status = "FILLED_ALL" if dealt >= order.quantity else "FILLED_PART"
        updated = replace(order, status=status, dealt_quantity=dealt, dealt_average_price=price, updated_at=datetime.now(timezone.utc))
        self.deal_rows.append(MoomooDeal(
            f"deal-{order_id}-{len(self.deal_rows) + 1}", order_id, order.acc_id,
            "SIMULATE", order.code, order.side, quantity, price,
            datetime.now(timezone.utc), fee,
        ))
        if order.side == "BUY":
            existing = next((row for row in self.position_rows if row.code == order.code), None)
            if existing is None:
                self.position_rows.append(MoomooPosition(order.acc_id, "SIMULATE", order.code, quantity, quantity))
            else:
                index = self.position_rows.index(existing)
                self.position_rows[index] = replace(existing, quantity=existing.quantity + quantity, can_sell_quantity=existing.can_sell_quantity + quantity)
        elif order.side == "SELL":
            existing = next((row for row in self.position_rows if row.code == order.code), None)
            if existing is not None:
                index = self.position_rows.index(existing)
                remaining = max(0.0, existing.quantity - quantity)
                self.position_rows[index] = replace(existing, quantity=remaining, can_sell_quantity=max(0.0, existing.can_sell_quantity - quantity))
        if status == "FILLED_ALL":
            self.current_order_rows.pop(order_id)
            self.history_order_rows[order_id] = updated
        else:
            self.current_order_rows[order_id] = updated
