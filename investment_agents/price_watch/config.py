from __future__ import annotations

import json
from pathlib import Path

from .models import ReviewSide, TriggerRule


def load_rules(path: Path) -> tuple[TriggerRule, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rules")
    if not isinstance(rows, list):
        raise ValueError("watch config requires a rules list")
    rules = tuple(TriggerRule(
        rule_id=str(row["rule_id"]),
        ticker=str(row["ticker"]).upper(),
        side=ReviewSide(str(row["side"])),
        low=row.get("low"),
        high=row.get("high"),
        at_or_below=row.get("at_or_below"),
        at_or_above=row.get("at_or_above"),
        cooldown_seconds=int(row.get("cooldown_seconds", 43_200)),
        rearm_above=row.get("rearm_above"),
        rearm_below=row.get("rearm_below"),
        user_rationale=str(row.get("user_rationale", "")),
        enabled=bool(row.get("enabled", True)),
    ) for row in rows)
    ids = [rule.rule_id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("rule_id values must be unique")
    return rules
