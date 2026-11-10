"""Optional official ``ibapi`` TWS socket transport.

The dependency is loaded lazily because IBKR distributes the Python client with
the TWS API download. Importing ``investment_agents`` never requires it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import threading
from typing import Any

from investment_agents.path_safety import sanitized_provider_error

from .models import (
    AccountSummary, BrokerAcknowledgement, BrokerConnectionInfo, BrokerExecution,
    BrokerOrder, BrokerPosition, IBKRQuote, OrderSide, PaperOrderIntent,
    ResolvedContract,
)


class OfficialIBAPIUnavailable(RuntimeError):
    pass


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OfficialIBKRTransport:
    """Synchronous facade over official EClient/EWrapper request callbacks."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        try:
            from ibapi.client import EClient
            from ibapi.contract import Contract
            from ibapi.execution import ExecutionFilter
            from ibapi.order import Order
            from ibapi.wrapper import EWrapper
        except ImportError as exc:
            raise OfficialIBAPIUnavailable(
                "Official IBKR TWS Python API (ibapi) is not installed"
            ) from exc
        self._Contract = Contract
        self._ExecutionFilter = ExecutionFilter
        self._Order = Order
        try:
            from ibapi.order_cancel import OrderCancel
            self._OrderCancel = OrderCancel
        except ImportError:
            self._OrderCancel = None
        self.timeout_seconds = timeout_seconds
        self._request_lock = threading.Lock()
        self._next_request_id = 9000
        self._thread: threading.Thread | None = None
        self._info: BrokerConnectionInfo | None = None
        class App(EWrapper, EClient):
            def __init__(self) -> None:
                EWrapper.__init__(self)
                EClient.__init__(self, self)
                self.ready = threading.Event()
                self.accounts_ready = threading.Event()
                self.next_id_ready = threading.Event()
                self.accounts: tuple[str, ...] = ()
                self.next_order_id: int | None = None
                self.events: dict[tuple[str, int], threading.Event] = {}
                self.rows: dict[tuple[str, int], list[Any]] = {}
                self.account_values: dict[int, list[tuple[str, str, str, str]]] = {}
                self.positions_rows: list[Any] = []
                self.positions_end = threading.Event()
                self.open_rows: dict[int, Any] = {}
                self.open_end = threading.Event()
                self.completed_rows: dict[int, Any] = {}
                self.completed_end = threading.Event()
                self.order_status_rows: dict[int, tuple[Any, ...]] = {}
                self.order_events: dict[int, threading.Event] = {}
                self.quote_rows: dict[int, dict[str, float]] = {}
                self.execution_rows: dict[int, list[Any]] = {}
                self.commissions: dict[str, tuple[float | None, str | None]] = {}
                self.errors: list[dict[str, str]] = []
                self.current_time_event = threading.Event()

            def nextValidId(self, orderId: int) -> None:
                self.next_order_id = int(orderId)
                self.next_id_ready.set()
                if self.accounts_ready.is_set():
                    self.ready.set()

            def managedAccounts(self, accountsList: str) -> None:
                self.accounts = tuple(item.strip() for item in accountsList.split(",") if item.strip())
                self.accounts_ready.set()
                if self.next_id_ready.is_set():
                    self.ready.set()

            def currentTime(self, time_value: int) -> None:
                self.current_time_event.set()

            def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:
                self.account_values.setdefault(reqId, []).append((account, tag, value, currency))

            def accountSummaryEnd(self, reqId: int) -> None:
                self.events.setdefault(("account", reqId), threading.Event()).set()

            def position(self, account: str, contract: Any, pos: Any, avgCost: float) -> None:
                self.positions_rows.append((account, contract, pos, avgCost))

            def positionEnd(self) -> None:
                self.positions_end.set()

            def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:
                self.open_rows[int(orderId)] = (contract, order, orderState)
                self.order_events.setdefault(int(orderId), threading.Event()).set()

            def openOrderEnd(self) -> None:
                self.open_end.set()

            def orderStatus(self, orderId: int, status: str, filled: Any, remaining: Any, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float = 0.0) -> None:
                self.order_status_rows[int(orderId)] = (status, filled, remaining, avgFillPrice, permId)
                self.order_events.setdefault(int(orderId), threading.Event()).set()

            def completedOrder(self, contract: Any, order: Any, orderState: Any) -> None:
                self.completed_rows[int(getattr(order, "orderId", 0))] = (contract, order, orderState)

            def completedOrdersEnd(self) -> None:
                self.completed_end.set()

            def contractDetails(self, reqId: int, contractDetails: Any) -> None:
                self.rows.setdefault(("contract", reqId), []).append(contractDetails)

            def contractDetailsEnd(self, reqId: int) -> None:
                self.events.setdefault(("contract", reqId), threading.Event()).set()

            def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:
                name = {1: "bid", 2: "ask", 4: "last"}.get(int(tickType))
                if name and price and price > 0:
                    self.quote_rows.setdefault(reqId, {})[name] = float(price)

            def tickSnapshotEnd(self, reqId: int) -> None:
                self.events.setdefault(("quote", reqId), threading.Event()).set()

            def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:
                self.execution_rows.setdefault(reqId, []).append((contract, execution))

            def execDetailsEnd(self, reqId: int) -> None:
                self.events.setdefault(("execution", reqId), threading.Event()).set()

            def _store_commission(self, report: Any) -> None:
                self.commissions[str(getattr(report, "execId", ""))] = (
                    _number(getattr(report, "commission", None), default=float("nan")),
                    getattr(report, "currency", None),
                )

            def commissionReport(self, report: Any) -> None:
                self._store_commission(report)

            def commissionAndFeesReport(self, report: Any) -> None:
                self._store_commission(report)

            def connectionClosed(self) -> None:
                self.ready.clear()

            def error(self, *args: Any) -> None:
                if args and isinstance(args[0], BaseException):
                    self.errors.append(sanitized_provider_error(args[0]))
                else:
                    # Older clients pass (reqId, code, message, advancedJson);
                    # newer clients may also include errorTime after reqId.
                    if len(args) >= 5:
                        raw_code = args[2]
                    elif len(args) >= 2:
                        raw_code = args[1]
                    else:
                        raw_code = "UNKNOWN"
                    code = str(raw_code)
                    self.errors.append({"code": f"IBKR_{code}"[:64], "message": "IBKR API operation reported an error"})

        self._app = App()

    def _request_id(self) -> int:
        with self._request_lock:
            value = self._next_request_id
            self._next_request_id += 1
            return value

    def _wait(self, event: threading.Event, operation: str) -> None:
        if not event.wait(self.timeout_seconds):
            raise TimeoutError(f"IBKR {operation} callback timeout")

    def connect(self, host: str, port: int, client_id: int, *, read_only: bool) -> BrokerConnectionInfo:
        if not read_only:
            raise PermissionError("initial IBKR connection must be READ_ONLY_PROBE")
        self._app.connect(host, port, clientId=client_id)
        if not self._app.isConnected():
            raise ConnectionError("IBKR socket connection failed")
        self._thread = threading.Thread(target=self._app.run, name="ibkr-official-api", daemon=True)
        self._thread.start()
        self._wait(self._app.ready, "connection handshake")
        self._info = BrokerConnectionInfo(
            host, port, client_id, datetime.now(timezone.utc),
            int(self._app.serverVersion()) if self._app.serverVersion() is not None else None,
            tuple(sorted(set(self._app.accounts))),
        )
        return self._info

    def disconnect(self) -> None:
        if self._app.isConnected():
            self._app.disconnect()
        self._info = None

    def is_connected(self) -> bool:
        return bool(self._app.isConnected())

    def heartbeat(self) -> bool:
        if not self.is_connected():
            return False
        self._app.current_time_event.clear()
        self._app.reqCurrentTime()
        return self._app.current_time_event.wait(self.timeout_seconds)

    def discover_accounts(self) -> tuple[str, ...]:
        if not self._app.accounts:
            self._app.reqManagedAccts()
            self._wait(self._app.accounts_ready, "managed accounts")
        return tuple(sorted(set(self._app.accounts)))

    def account_summary(self, account_id: str) -> AccountSummary:
        req_id = self._request_id()
        event = threading.Event()
        self._app.events[("account", req_id)] = event
        self._app.account_values[req_id] = []
        self._app.reqAccountSummary(req_id, "All", "AccountType,TotalCashValue,SettledCash,AvailableFunds")
        self._wait(event, "account summary")
        self._app.cancelAccountSummary(req_id)
        values: dict[str, str] = {}
        account_type: str | None = None
        for account, tag, value, row_currency in self._app.account_values.pop(req_id, []):
            if account != account_id:
                continue
            if tag == "AccountType":
                account_type = value
            elif row_currency == "USD":
                values[tag] = value
        def optional(tag: str) -> float | None:
            try:
                return float(values[tag])
            except (KeyError, TypeError, ValueError):
                return None
        return AccountSummary(account_id, account_type, optional("TotalCashValue"), optional("SettledCash"), optional("AvailableFunds"), "USD", values)

    def positions(self) -> tuple[BrokerPosition, ...]:
        self._app.positions_rows = []
        self._app.positions_end.clear()
        self._app.reqPositions()
        self._wait(self._app.positions_end, "positions")
        self._app.cancelPositions()
        result = []
        for account, contract, quantity, average_cost in self._app.positions_rows:
            result.append(BrokerPosition(
                account, int(getattr(contract, "conId", 0)), str(getattr(contract, "symbol", "")),
                str(getattr(contract, "secType", "")), str(getattr(contract, "currency", "")),
                str(getattr(contract, "exchange", "")), _number(quantity), _number(average_cost),
            ))
        return tuple(result)

    def _order(self, order_id: int, row: Any, *, completed: bool = False) -> BrokerOrder:
        contract, order, order_state = row
        status_row = self._app.order_status_rows.get(order_id)
        status = str(status_row[0]) if status_row else str(getattr(order_state, "status", "Unknown"))
        filled = _number(status_row[1]) if status_row else _number(getattr(order, "filledQuantity", 0))
        remaining = _number(status_row[2]) if status_row else max(0.0, _number(getattr(order, "totalQuantity", 0)) - filled)
        return BrokerOrder(
            order_id, int(getattr(order, "permId", 0)) or None, getattr(order, "orderRef", None) or None,
            getattr(order, "account", None) or None, int(getattr(contract, "conId", 0)),
            str(getattr(contract, "symbol", "")), str(getattr(contract, "secType", "")),
            str(getattr(contract, "currency", "")), str(getattr(order, "action", "")),
            str(getattr(order, "orderType", "")), _number(getattr(order, "totalQuantity", 0)),
            _number(getattr(order, "lmtPrice", 0)) or None, getattr(order, "tif", None) or None,
            status, filled, remaining,
        )

    def open_orders(self) -> tuple[BrokerOrder, ...]:
        self._app.open_rows = {}
        self._app.open_end.clear()
        self._app.reqOpenOrders()
        self._wait(self._app.open_end, "open orders")
        return tuple(self._order(order_id, row) for order_id, row in self._app.open_rows.items())

    def completed_orders(self) -> tuple[BrokerOrder, ...]:
        self._app.completed_rows = {}
        self._app.completed_end.clear()
        self._app.reqCompletedOrders(True)
        self._wait(self._app.completed_end, "completed orders")
        return tuple(self._order(order_id, row, completed=True) for order_id, row in self._app.completed_rows.items())

    def executions(self) -> tuple[BrokerExecution, ...]:
        req_id = self._request_id()
        event = threading.Event()
        self._app.events[("execution", req_id)] = event
        self._app.execution_rows[req_id] = []
        self._app.reqExecutions(req_id, self._ExecutionFilter())
        self._wait(event, "executions")
        result = []
        for contract, execution in self._app.execution_rows.pop(req_id, []):
            execution_id = str(getattr(execution, "execId", ""))
            commission, commission_currency = self._app.commissions.get(execution_id, (None, None))
            if commission != commission:  # NaN sentinel
                commission = None
            result.append(BrokerExecution(
                execution_id, int(getattr(execution, "orderId", 0)), int(getattr(execution, "permId", 0)) or None,
                getattr(execution, "orderRef", None) or None, str(getattr(execution, "acctNumber", "")),
                int(getattr(contract, "conId", 0)), str(getattr(execution, "side", "")),
                _number(getattr(execution, "shares", 0)), _number(getattr(execution, "price", 0)),
                None, commission, commission_currency,
            ))
        return tuple(result)

    def contract_details(self, *, symbol: str, security_type: str, currency: str, exchange: str) -> tuple[ResolvedContract, ...]:
        contract = self._Contract()
        contract.symbol = symbol
        contract.secType = security_type
        contract.currency = currency
        contract.exchange = exchange
        req_id = self._request_id()
        event = threading.Event()
        self._app.events[("contract", req_id)] = event
        self._app.rows[("contract", req_id)] = []
        self._app.reqContractDetails(req_id, contract)
        self._wait(event, "contract details")
        result = []
        for details in self._app.rows.pop(("contract", req_id), []):
            item = details.contract
            result.append(ResolvedContract(
                int(item.conId), str(item.symbol), str(item.secType), str(item.currency),
                str(item.exchange), getattr(item, "primaryExchange", None) or None,
                getattr(item, "localSymbol", None) or None, False,
            ))
        return tuple(result)

    def _contract(self, resolved: ResolvedContract):
        contract = self._Contract()
        contract.conId = resolved.con_id
        contract.symbol = resolved.symbol
        contract.secType = resolved.security_type
        contract.currency = resolved.currency
        contract.exchange = resolved.exchange
        if resolved.primary_exchange:
            contract.primaryExchange = resolved.primary_exchange
        return contract

    def quote_snapshot(self, contract: ResolvedContract) -> IBKRQuote:
        req_id = self._request_id()
        event = threading.Event()
        self._app.events[("quote", req_id)] = event
        self._app.quote_rows[req_id] = {}
        self._app.reqMktData(req_id, self._contract(contract), "", True, False, [])
        self._wait(event, "market data snapshot")
        values = self._app.quote_rows.pop(req_id, {})
        status = "AVAILABLE" if values.get("bid") or values.get("ask") or values.get("last") else "UNAVAILABLE"
        return IBKRQuote(contract.con_id, values.get("bid"), values.get("ask"), values.get("last"), datetime.now(timezone.utc), status)

    def place_limit_order(self, intent: PaperOrderIntent, contract: ResolvedContract, account_id: str) -> BrokerAcknowledgement:
        if self._app.next_order_id is None:
            raise RuntimeError("IBKR next valid order ID unavailable")
        order_id = self._app.next_order_id
        self._app.next_order_id += 1
        order = self._Order()
        order.account = account_id
        order.action = "BUY" if intent.side is OrderSide.BUY else "SELL"
        order.orderType = "LMT"
        order.totalQuantity = Decimal(str(intent.quantity))
        order.lmtPrice = intent.limit_price
        order.tif = intent.time_in_force
        order.orderRef = intent.order_intent_id
        order.transmit = True
        order.outsideRth = False
        event = threading.Event()
        self._app.order_events[order_id] = event
        self._app.placeOrder(order_id, self._contract(contract), order)
        if not event.wait(self.timeout_seconds):
            return BrokerAcknowledgement(False, None, None, "Unknown", datetime.now(timezone.utc), ambiguous=True)
        row = self.order_status(order_id)
        if row is None:
            return BrokerAcknowledgement(False, order_id, None, "Unknown", datetime.now(timezone.utc), ambiguous=True)
        rejected = row.status.replace(" ", "").upper() in {"REJECTED", "INACTIVE"}
        return BrokerAcknowledgement(not rejected, order_id, row.permanent_id, row.status, datetime.now(timezone.utc), "IBKR_REJECTED" if rejected else None)

    def cancel_order(self, broker_order_id: int) -> BrokerAcknowledgement:
        event = threading.Event()
        self._app.order_events[broker_order_id] = event
        cancel = self._OrderCancel() if self._OrderCancel is not None else ""
        self._app.cancelOrder(broker_order_id, cancel)
        if not event.wait(self.timeout_seconds):
            return BrokerAcknowledgement(False, broker_order_id, None, "Unknown", datetime.now(timezone.utc), ambiguous=True)
        row = self.order_status(broker_order_id)
        status = row.status if row else "Unknown"
        ambiguous = row is None
        return BrokerAcknowledgement(not ambiguous, broker_order_id, row.permanent_id if row else None, status, datetime.now(timezone.utc), ambiguous=ambiguous)

    def order_status(self, broker_order_id: int) -> BrokerOrder | None:
        row = self._app.open_rows.get(broker_order_id) or self._app.completed_rows.get(broker_order_id)
        return self._order(broker_order_id, row) if row is not None else None
