from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import Mapping

from .models import PriceQuote, RuleState, TriggerRule, WatchEvent, WatchState


class PriceWatchEngine:
    """Pure state machine. It has no broker or order dependency."""

    def __init__(self, *, max_quote_age_seconds: int = 300) -> None:
        self.max_quote_age_seconds = max_quote_age_seconds

    def evaluate(
        self,
        rules: tuple[TriggerRule, ...],
        quotes: Mapping[str, PriceQuote],
        states: Mapping[str, WatchState],
        *,
        now: datetime,
    ) -> tuple[tuple[WatchEvent, ...], dict[str, WatchState]]:
        output = dict(states)
        events: list[WatchEvent] = []
        for rule in rules:
            prior = output.get(rule.rule_id, WatchState(rule.rule_id))
            quote = quotes.get(rule.ticker)
            if not rule.enabled or quote is None or not quote.usable(now=now, max_age_seconds=self.max_quote_age_seconds):
                continue
            price = float(quote.price)
            if prior.state is RuleState.TRIGGERED:
                if rule.is_rearmed(price):
                    output[rule.rule_id] = replace(prior, state=RuleState.ARMED, last_price=price)
                else:
                    output[rule.rule_id] = replace(prior, last_price=price)
                continue
            cooled = prior.last_triggered_at is None or (now - prior.last_triggered_at).total_seconds() >= rule.cooldown_seconds
            if cooled and rule.matches(price):
                raw = f"{rule.rule_id}|{quote.quoted_at.isoformat()}|{price:.8f}"
                event = WatchEvent(
                    event_id=sha256(raw.encode()).hexdigest()[:24],
                    rule_id=rule.rule_id,
                    ticker=rule.ticker,
                    side=rule.side,
                    price=price,
                    quote_source=quote.source,
                    quoted_at=quote.quoted_at,
                    triggered_at=now,
                    user_rationale=rule.user_rationale,
                )
                events.append(event)
                output[rule.rule_id] = WatchState(rule.rule_id, RuleState.TRIGGERED, now, price)
            else:
                output[rule.rule_id] = replace(prior, last_price=price)
        return tuple(events), output
