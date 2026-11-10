from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
from typing import Mapping

from .models import RuleState, WatchEvent, WatchState


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, WatchState]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            key: WatchState(
                rule_id=value["rule_id"],
                state=RuleState(value["state"]),
                last_triggered_at=datetime.fromisoformat(value["last_triggered_at"]) if value.get("last_triggered_at") else None,
                last_price=value.get("last_price"),
            )
            for key, value in payload.items()
        }

    def save(self, states: Mapping[str, WatchState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps({key: asdict(value) for key, value in states.items()}, default=_jsonable, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.path)


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: WatchEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), default=_jsonable, sort_keys=True) + "\n")
