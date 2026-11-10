"""Deterministic in-memory TWS transport for local safety/fault testing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .models import (
    AccountSummary, BrokerAcknowledgement, BrokerConnectionInfo, BrokerExecution,
    BrokerOrder, BrokerPosition, IBKRQuote, PaperOrderIntent, ResolvedContract,
)


class InMemoryIBKRTransport:
    def __init__(
        self,
        *,
        account_ids: tuple[str, ...] = ("PAPER-TEST",),
        server_version: int = 180,
        account_summary: AccountSummary | None = None,
        positions: tuple[BrokerPosition, ...] = (),
        contracts: tuple[ResolvedContract, ...] = (),
        quote: IBKRQuote | None = None,
    ) -> None:
        self.account_ids = account_ids
        self.server_version = server_version
        self.summary = account_summary or AccountSummary(
            account_ids[0] if len(account_ids) == 1 else "", "INDIVIDUAL", 100_000, 100_000, 100_000,
        )
        self.position_rows = list(positions)
        self.contract_rows = list(contracts)
        self.quote = quote
        self.order_rows: dict[int, BrokerOrder] = {}
        self.completed_rows: dict[int, BrokerOrder] = {}
        self.execution_rows: list[BrokerExecution] = []
        self.connected_info: BrokerConnectionInfo | None = None
        self.next_order_id = 1000
        self.place_behavior = "ACKNOWLEDGE"
        self.cancel_behavior = "CANCEL"
        self.heartbeat_available = True
        self.read_only_requested: bool | None = None
        self.calls: list[str] = []

    def connect(self, host: str, port: int, client_id: int, *, read_only: bool) -> BrokerConnectionInfo:
        self.calls.append("connect")
        self.read_only_requested = read_only
        self.connected_info = BrokerConnectionInfo(
            host, port, client_id, datetime.now(timezone.utc), self.server_version, self.account_ids,
        )
        return self.connected_info

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.connected_info = None

    def is_connected(self) -> bool:
        return self.connected_info is not None

    def heartbeat(self) -> bool:
        self.calls.append("heartbeat")
        return self.is_connected() and self.heartbeat_available

    def discover_accounts(self) -> tuple[str, ...]:
        self.calls.append("discover_accounts")
        return self.account_ids

    def account_summary(self, account_id: str) -> AccountSummary:
        self.calls.append("account_summary")
        if account_id not in self.account_ids:
            raise ValueError("account unavailable")
        return replace(self.summary, account_id=account_id)

    def positions(self) -> tuple[BrokerPosition, ...]:
        self.calls.append("positions")
        return tuple(self.position_rows)

    def open_orders(self) -> tuple[BrokerOrder, ...]:
        self.calls.append("open_orders")
        return tuple(self.order_rows.values())

    def completed_orders(self) -> tuple[BrokerOrder, ...]:
        self.calls.append("completed_orders")
        return tuple(self.completed_rows.values())

    def executions(self) -> tuple[BrokerExecution, ...]:
        self.calls.append("executions")
        return tuple(self.execution_rows)

    def contract_details(self, *, symbol: str, security_type: str, currency: str, exchange: str) -> tuple[ResolvedContract, ...]:
        self.calls.append("contract_details")
        return tuple(row for row in self.contract_rows if row.symbol == symbol and row.security_type == security_type and row.currency == currency)

    def quote_snapshot(self, contract: ResolvedContract) -> IBKRQuote:
        self.calls.append("quote_snapshot")
        if self.quote is None or self.quote.con_id != contract.con_id:
            return IBKRQuote(contract.con_id, None, None, None, datetime.now(timezone.utc), "UNAVAILABLE")
        return self.quote

    def place_limit_order(self, intent: PaperOrderIntent, contract: ResolvedContract, account_id: str) -> BrokerAcknowledgement:
        self.calls.append("place_limit_order")
        now = datetime.now(timezone.utc)
        order_id = self.next_order_id
        self.next_order_id += 1
        if self.place_behavior == "AMBIGUOUS":
            return BrokerAcknowledgement(False, None, None, "UNKNOWN", now, ambiguous=True)
        if self.place_behavior == "RAISE_AFTER_ACCEPT":
            self._store_order(order_id, intent, contract, account_id, "Submitted")
            raise TimeoutError("simulated acknowledgement loss")
        if self.place_behavior == "REJECT":
            return BrokerAcknowledgement(False, order_id, None, "Rejected", now, "SIMULATED_REJECT")
        self._store_order(order_id, intent, contract, account_id, "Submitted")
        return BrokerAcknowledgement(True, order_id, order_id + 10_000, "Submitted", now)

    def _store_order(self, order_id: int, intent: PaperOrderIntent, contract: ResolvedContract, account_id: str, status: str) -> None:
        action = "BUY" if intent.side.value == "BUY" else "SELL"
        self.order_rows[order_id] = BrokerOrder(
            order_id, order_id + 10_000, intent.order_intent_id, account_id,
            contract.con_id, contract.symbol, contract.security_type, contract.currency,
            action, "LMT", intent.quantity, intent.limit_price, intent.time_in_force,
            status, 0.0, intent.quantity,
        )

    def cancel_order(self, broker_order_id: int) -> BrokerAcknowledgement:
        self.calls.append("cancel_order")
        now = datetime.now(timezone.utc)
        if self.cancel_behavior == "AMBIGUOUS":
            return BrokerAcknowledgement(False, broker_order_id, None, "UNKNOWN", now, ambiguous=True)
        order = self.order_rows.pop(broker_order_id)
        cancelled = replace(order, status="Cancelled")
        self.completed_rows[broker_order_id] = cancelled
        return BrokerAcknowledgement(True, broker_order_id, order.permanent_id, "Cancelled", now)

    def order_status(self, broker_order_id: int) -> BrokerOrder | None:
        self.calls.append("order_status")
        return self.order_rows.get(broker_order_id) or self.completed_rows.get(broker_order_id)

    def fill(self, broker_order_id: int, quantity: float, price: float, *, commission: float | None = None) -> None:
        order = self.order_rows[broker_order_id]
        filled = order.filled_quantity + quantity
        remaining = max(0.0, order.total_quantity - filled)
        status = "Filled" if remaining == 0 else "PartiallyFilled"
        updated = replace(order, status=status, filled_quantity=filled, remaining_quantity=remaining)
        self.execution_rows.append(BrokerExecution(
            f"exec-{broker_order_id}-{len(self.execution_rows) + 1}", broker_order_id,
            order.permanent_id, order.order_ref, order.account_id or "", order.con_id,
            order.action, quantity, price, datetime.now(timezone.utc), commission, order.currency,
        ))
        if remaining == 0:
            self.order_rows.pop(broker_order_id)
            self.completed_rows[broker_order_id] = updated
        else:
            self.order_rows[broker_order_id] = updated

