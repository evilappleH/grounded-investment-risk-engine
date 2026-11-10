"""Local append-only broker state machine; no external connectivity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
from math import isfinite
from typing import Any, Mapping

from investment_agents.path_safety import validate_run_id
from investment_agents.portfolio import InstrumentId, InstrumentType
from investment_agents.risk import RiskCheckedTrade, RiskDecision, RiskStatus, verify_risk_decision_hash


class SimulatorOrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNCERTAIN_ACKNOWLEDGEMENT = "UNCERTAIN_ACKNOWLEDGEMENT"


class DuplicateSubmissionError(RuntimeError):
    pass


class SimulatedDisconnect(RuntimeError):
    pass


@dataclass(frozen=True)
class SimulatedOrderRequest:
    client_order_id: str
    decision_id: str
    decision_hash: str
    source_manifest_hash: str
    instrument: InstrumentId
    side: str
    quantity: float
    quote_price: float
    quote_timestamp: datetime
    created_at: datetime
    risk_result: str = RiskStatus.APPROVED.value

    def __post_init__(self) -> None:
        validate_run_id(self.client_order_id)
        validate_run_id(self.decision_id)
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("simulator side must be BUY or SELL")
        if not isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("simulator quantity must be positive and finite")
        if not isfinite(self.quote_price) or self.quote_price <= 0:
            raise ValueError("simulator quote must be positive and finite")
        if self.risk_result != RiskStatus.APPROVED.value:
            raise ValueError("only an approved deterministic risk result may be simulated")

    @property
    def request_hash(self) -> str:
        return _hash(_jsonable(self))

    @classmethod
    def from_risk_trade(
        cls,
        trade: RiskCheckedTrade,
        decision: RiskDecision,
        *,
        client_order_id: str,
        instrument_type: InstrumentType = InstrumentType.EQUITY,
        created_at: datetime | None = None,
    ) -> "SimulatedOrderRequest":
        if decision.status is not RiskStatus.APPROVED:
            raise ValueError("risk decision is not approved")
        return cls(
            client_order_id=client_order_id,
            decision_id=decision.decision_id,
            decision_hash=decision.decision_hash,
            source_manifest_hash=decision.source_manifest_hash,
            instrument=InstrumentId(trade.symbol, instrument_type),
            side=trade.side,
            quantity=trade.quantity,
            quote_price=trade.quote_price,
            quote_timestamp=trade.quote_timestamp,
            created_at=created_at or decision.checked_at,
        )


@dataclass(frozen=True)
class SimulatedOrder:
    request: SimulatedOrderRequest
    status: SimulatorOrderStatus
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    rejection_code: str | None = None
    last_event_at: datetime | None = None

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.request.quantity - self.filled_quantity)


def _jsonable(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class LocalBrokerSimulator:
    """Idempotent order lifecycle with hash-chained append-only audit events."""

    def __init__(self, audit_path: Path, *, recover: bool = True) -> None:
        self.audit_path = audit_path.resolve()
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.orders: dict[str, SimulatedOrder] = {}
        self._last_hash: str | None = None
        self._sequence = 0
        if self.audit_path.exists():
            if not recover:
                raise FileExistsError("simulator audit trail already exists")
            self._recover()

    def _event_body(self, event_type: str, request: SimulatedOrderRequest, payload: Mapping[str, Any], at: datetime) -> dict[str, Any]:
        return {
            "sequence": self._sequence + 1,
            "event_at": at.isoformat(),
            "event_type": event_type,
            "client_order_id": request.client_order_id,
            "source_manifest_hash": request.source_manifest_hash,
            "decision_hash": request.decision_hash,
            "risk_result": request.risk_result,
            "quote": {"price": request.quote_price, "timestamp": request.quote_timestamp.isoformat()},
            "request_hash": request.request_hash,
            "request": _jsonable(request),
            "payload": _jsonable(payload),
            "previous_event_hash": self._last_hash,
        }

    def _append(self, event_type: str, request: SimulatedOrderRequest, payload: Mapping[str, Any], *, at: datetime | None = None) -> dict[str, Any]:
        at = at or datetime.now(timezone.utc)
        body = self._event_body(event_type, request, payload, at)
        event = {**body, "event_hash": _hash(body)}
        with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence = int(event["sequence"])
        self._last_hash = str(event["event_hash"])
        self._apply(event)
        return event

    @staticmethod
    def _request(payload: Mapping[str, Any]) -> SimulatedOrderRequest:
        item = payload["request"]
        instrument = item["instrument"]
        return SimulatedOrderRequest(
            client_order_id=item["client_order_id"], decision_id=item["decision_id"],
            decision_hash=item["decision_hash"], source_manifest_hash=item["source_manifest_hash"],
            instrument=InstrumentId(instrument["symbol"], InstrumentType(instrument["instrument_type"]), instrument["currency"]),
            side=item["side"], quantity=float(item["quantity"]), quote_price=float(item["quote_price"]),
            quote_timestamp=datetime.fromisoformat(item["quote_timestamp"]),
            created_at=datetime.fromisoformat(item["created_at"]), risk_result=item["risk_result"],
        )

    def _apply(self, event: Mapping[str, Any]) -> None:
        event_type = str(event["event_type"])
        order_id = str(event["client_order_id"])
        at = datetime.fromisoformat(str(event["event_at"]))
        payload = event.get("payload", {})
        if event_type == "SUBMITTED":
            self.orders[order_id] = SimulatedOrder(self._request(event), SimulatorOrderStatus.SUBMITTED, last_event_at=at)
            return
        if event_type in {"DUPLICATE_SUBMISSION", "DUPLICATE_CONFLICT", "CONNECTION_LOST", "RECONNECTED"}:
            return
        order = self.orders[order_id]
        status = SimulatorOrderStatus(str(payload["status"]))
        self.orders[order_id] = replace(
            order,
            status=status,
            filled_quantity=float(payload.get("filled_quantity", order.filled_quantity)),
            average_fill_price=float(payload["average_fill_price"]) if payload.get("average_fill_price") is not None else order.average_fill_price,
            rejection_code=payload.get("rejection_code"),
            last_event_at=at,
        )

    def _recover(self) -> None:
        previous = None
        expected_sequence = 1
        with self.audit_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                event_hash = event.pop("event_hash", None)
                if event.get("sequence") != expected_sequence or event.get("previous_event_hash") != previous or event_hash != _hash(event):
                    raise ValueError("simulator audit trail integrity failure")
                event["event_hash"] = event_hash
                self._sequence = expected_sequence
                self._last_hash = event_hash
                self._apply(event)
                previous = event_hash
                expected_sequence += 1

    @staticmethod
    def _matches_approved_trade(request: SimulatedOrderRequest, decision: RiskDecision) -> bool:
        return any(
            trade.canonical_instrument_id == request.instrument.canonical_id
            and trade.side == request.side
            and abs(trade.quantity - request.quantity) <= 1e-9
            and abs(trade.quote_price - request.quote_price) <= 1e-9
            and trade.quote_timestamp == request.quote_timestamp
            for trade in decision.trades
        )

    def submit(self, request: SimulatedOrderRequest, decision: RiskDecision, *, fault: str | None = None) -> SimulatedOrder:
        if fault not in {None, "UNCERTAIN_ACKNOWLEDGEMENT", "REJECT", "DISCONNECT_AFTER_SUBMIT"}:
            raise ValueError("unsupported simulator fault")
        if (
            decision.status is not RiskStatus.APPROVED
            or not verify_risk_decision_hash(decision)
            or request.decision_id != decision.decision_id
            or request.decision_hash != decision.decision_hash
            or request.source_manifest_hash != decision.source_manifest_hash
            or not self._matches_approved_trade(request, decision)
        ):
            raise ValueError("simulator request is not bound to an approved risk trade")
        existing = self.orders.get(request.client_order_id)
        if existing is not None:
            if existing.request.request_hash == request.request_hash:
                self._append("DUPLICATE_SUBMISSION", request, {"status": existing.status.value})
                return self.orders[request.client_order_id]
            self._append("DUPLICATE_CONFLICT", existing.request, {"status": existing.status.value})
            raise DuplicateSubmissionError("client_order_id already binds a different request")
        self._append("SUBMITTED", request, {"status": SimulatorOrderStatus.SUBMITTED.value})
        if fault == "UNCERTAIN_ACKNOWLEDGEMENT":
            self._transition(request.client_order_id, "UNCERTAIN_ACKNOWLEDGEMENT", SimulatorOrderStatus.UNCERTAIN_ACKNOWLEDGEMENT)
        elif fault == "REJECT":
            self.reject(request.client_order_id, "SIMULATED_REJECTION")
        elif fault == "DISCONNECT_AFTER_SUBMIT":
            self._append("CONNECTION_LOST", request, {"status": SimulatorOrderStatus.SUBMITTED.value})
            raise SimulatedDisconnect("simulated disconnect after durable submit")
        return self.orders[request.client_order_id]

    def _transition(self, order_id: str, event_type: str, status: SimulatorOrderStatus, **payload: Any) -> SimulatedOrder:
        order = self.orders[order_id]
        self._append(event_type, order.request, {"status": status.value, **payload})
        return self.orders[order_id]

    def acknowledge(self, order_id: str) -> SimulatedOrder:
        if self.orders[order_id].status not in {SimulatorOrderStatus.SUBMITTED, SimulatorOrderStatus.UNCERTAIN_ACKNOWLEDGEMENT}:
            raise ValueError("order cannot be acknowledged from current state")
        return self._transition(order_id, "ACKNOWLEDGED", SimulatorOrderStatus.ACKNOWLEDGED)

    def partial_fill(self, order_id: str, quantity: float, price: float) -> SimulatedOrder:
        order = self.orders[order_id]
        if order.status not in {SimulatorOrderStatus.ACKNOWLEDGED, SimulatorOrderStatus.PARTIALLY_FILLED}:
            raise ValueError("order cannot fill from current state")
        if not isfinite(quantity) or quantity <= 0 or quantity >= order.remaining_quantity:
            raise ValueError("partial fill must be positive and less than remaining quantity")
        if not isfinite(price) or price <= 0:
            raise ValueError("fill price must be positive and finite")
        cumulative = order.filled_quantity + quantity
        average = ((order.average_fill_price or 0) * order.filled_quantity + price * quantity) / cumulative
        return self._transition(order_id, "PARTIAL_FILL", SimulatorOrderStatus.PARTIALLY_FILLED, filled_quantity=cumulative, average_fill_price=average, fill_quantity=quantity, fill_price=price)

    def fill(self, order_id: str, price: float) -> SimulatedOrder:
        order = self.orders[order_id]
        if order.status not in {SimulatorOrderStatus.ACKNOWLEDGED, SimulatorOrderStatus.PARTIALLY_FILLED}:
            raise ValueError("order cannot fill from current state")
        if not isfinite(price) or price <= 0:
            raise ValueError("fill price must be positive and finite")
        quantity = order.remaining_quantity
        cumulative = order.request.quantity
        average = ((order.average_fill_price or 0) * order.filled_quantity + price * quantity) / cumulative
        return self._transition(order_id, "FILLED", SimulatorOrderStatus.FILLED, filled_quantity=cumulative, average_fill_price=average, fill_quantity=quantity, fill_price=price)

    def cancel(self, order_id: str) -> SimulatedOrder:
        order = self.orders[order_id]
        if order.status not in {SimulatorOrderStatus.SUBMITTED, SimulatorOrderStatus.ACKNOWLEDGED, SimulatorOrderStatus.PARTIALLY_FILLED, SimulatorOrderStatus.UNCERTAIN_ACKNOWLEDGEMENT}:
            raise ValueError("order cannot be cancelled from current state")
        return self._transition(order_id, "CANCELLED", SimulatorOrderStatus.CANCELLED)

    def reject(self, order_id: str, code: str) -> SimulatedOrder:
        order = self.orders[order_id]
        if order.status not in {SimulatorOrderStatus.SUBMITTED, SimulatorOrderStatus.UNCERTAIN_ACKNOWLEDGEMENT}:
            raise ValueError("order cannot be rejected from current state")
        safe_code = "".join(character for character in code.upper() if character.isalnum() or character == "_")[:64]
        return self._transition(order_id, "REJECTED", SimulatorOrderStatus.REJECTED, rejection_code=safe_code or "SIMULATED_REJECTION")

    def reconnect(self, remote_states: Mapping[str, str]) -> tuple[SimulatedOrder, ...]:
        """Reconcile submitted/uncertain local state against an explicit snapshot."""
        reconciled: list[SimulatedOrder] = []
        for order_id, remote_status in remote_states.items():
            if order_id not in self.orders:
                continue
            order = self.orders[order_id]
            self._append("RECONNECTED", order.request, {"status": order.status.value, "remote_status": remote_status})
            if remote_status == SimulatorOrderStatus.ACKNOWLEDGED.value:
                reconciled.append(self.acknowledge(order_id))
            elif remote_status == SimulatorOrderStatus.REJECTED.value:
                reconciled.append(self.reject(order_id, "REMOTE_REJECTED"))
            elif remote_status == SimulatorOrderStatus.CANCELLED.value:
                reconciled.append(self.cancel(order_id))
            else:
                raise ValueError("remote reconciliation status requires explicit fill details or is unsupported")
        return tuple(reconciled)
