"""Durable, append-only, hash-chained Paper execution journal."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from investment_agents.path_safety import redact_secrets, validate_run_id

from .models import IntentState, TERMINAL_INTENT_STATES


_TRANSITIONS: Mapping[IntentState | None, frozenset[IntentState]] = {
    None: frozenset({IntentState.CREATED}),
    IntentState.CREATED: frozenset({IntentState.PREFLIGHT_PASSED, IntentState.REJECTED}),
    IntentState.PREFLIGHT_PASSED: frozenset({IntentState.SUBMITTING, IntentState.REJECTED}),
    IntentState.SUBMITTING: frozenset({
        IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED, IntentState.FILLED,
        IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE,
    }),
    IntentState.ACKNOWLEDGED: frozenset({
        IntentState.PARTIALLY_FILLED, IntentState.FILLED, IntentState.CANCEL_PENDING,
        IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE,
    }),
    IntentState.PARTIALLY_FILLED: frozenset({
        IntentState.PARTIALLY_FILLED, IntentState.FILLED, IntentState.CANCEL_PENDING,
        IntentState.CANCELLED,
        IntentState.UNKNOWN_BROKER_STATE,
    }),
    IntentState.CANCEL_PENDING: frozenset({
        IntentState.PARTIALLY_FILLED, IntentState.FILLED, IntentState.CANCELLED,
        IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE,
    }),
    IntentState.UNKNOWN_BROKER_STATE: frozenset({
        IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED, IntentState.FILLED,
        IntentState.CANCELLED, IntentState.REJECTED, IntentState.UNKNOWN_BROKER_STATE,
    }),
    IntentState.FILLED: frozenset(),
    IntentState.CANCELLED: frozenset(),
    IntentState.REJECTED: frozenset(),
}


def jsonable(value: object) -> object:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [jsonable(item) for item in value]
    return value


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PaperExecutionJournal:
    """Append-only intent state plus audit evidence; existing files are verified."""

    def __init__(self, path: Path, *, journal_label: str = "IBKR Paper") -> None:
        self.path = path.resolve()
        self.journal_label = journal_label
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.last_event_hash: str | None = None
        self.intent_states: dict[str, IntentState] = {}
        self.intent_decisions: dict[str, str] = {}
        self.broker_order_ids: dict[str, int | str] = {}
        self.intent_links: dict[str, Mapping[str, object]] = {}
        if self.path.exists():
            self._recover()

    def _recover(self) -> None:
        previous: str | None = None
        expected = 1
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                event_hash = event.pop("event_hash", None)
                if (
                    event.get("sequence") != expected
                    or event.get("previous_event_hash") != previous
                    or event_hash != _hash(event)
                ):
                    raise ValueError(f"{self.journal_label} audit trail integrity failure")
                event["event_hash"] = event_hash
                self._apply(event, validate_transition=True)
                self.sequence = expected
                self.last_event_hash = event_hash
                previous = event_hash
                expected += 1

    def _apply(self, event: Mapping[str, object], *, validate_transition: bool) -> None:
        intent_id = event.get("order_intent_id")
        state_value = event.get("intent_state")
        if isinstance(intent_id, str) and isinstance(state_value, str):
            state = IntentState(state_value)
            prior = self.intent_states.get(intent_id)
            if validate_transition and state not in _TRANSITIONS[prior]:
                raise ValueError(f"invalid Paper intent transition: {prior} -> {state}")
            self.intent_states[intent_id] = state
            decision_id = event.get("decision_id")
            if isinstance(decision_id, str):
                self.intent_decisions[intent_id] = decision_id
            broker_order_id = event.get("broker_order_id")
            if isinstance(broker_order_id, (int, str)):
                self.broker_order_ids[intent_id] = broker_order_id
            links = event.get("links")
            if isinstance(links, dict) and links:
                self.intent_links[intent_id] = links

    def append(
        self,
        event_type: str,
        *,
        order_intent_id: str | None = None,
        decision_id: str | None = None,
        intent_state: IntentState | None = None,
        broker_order_id: int | str | None = None,
        links: Mapping[str, object] | None = None,
        payload: Mapping[str, object] | None = None,
        event_at: datetime | None = None,
    ) -> Mapping[str, object]:
        if order_intent_id is not None:
            validate_run_id(order_intent_id)
        if decision_id is not None:
            validate_run_id(decision_id)
        event_at = event_at or datetime.now(timezone.utc)
        body: dict[str, object] = {
            "sequence": self.sequence + 1,
            "event_at": event_at.isoformat(),
            "event_type": event_type,
            "order_intent_id": order_intent_id,
            "decision_id": decision_id,
            "intent_state": intent_state.value if intent_state is not None else None,
            "broker_order_id": broker_order_id,
            "links": redact_secrets(jsonable(links or {})),
            "payload": redact_secrets(jsonable(payload or {})),
            "previous_event_hash": self.last_event_hash,
        }
        event = {**body, "event_hash": _hash(body)}
        self._apply(event, validate_transition=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence += 1
        self.last_event_hash = str(event["event_hash"])
        return event

    def state(self, order_intent_id: str) -> IntentState | None:
        return self.intent_states.get(order_intent_id)

    def decision_used(self, decision_id: str, *, except_intent_id: str | None = None) -> bool:
        return any(
            bound_decision == decision_id and intent_id != except_intent_id
            for intent_id, bound_decision in self.intent_decisions.items()
        )

    @property
    def unresolved_intent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            intent_id for intent_id, state in self.intent_states.items()
            if state is IntentState.UNKNOWN_BROKER_STATE
        ))

    @property
    def active_intent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            intent_id for intent_id, state in self.intent_states.items()
            if state not in TERMINAL_INTENT_STATES
        ))
