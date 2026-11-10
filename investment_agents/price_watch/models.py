from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ReviewSide(str, Enum):
    BUY_REVIEW = "BUY_REVIEW"
    SELL_REVIEW = "SELL_REVIEW"


class RuleState(str, Enum):
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"


@dataclass(frozen=True)
class PriceQuote:
    ticker: str
    price: float | None
    quoted_at: datetime | None
    source: str
    status: str = "AVAILABLE"

    def usable(self, *, now: datetime, max_age_seconds: int) -> bool:
        if self.status != "AVAILABLE" or self.price is None or self.price <= 0 or self.quoted_at is None:
            return False
        age = (now - self.quoted_at).total_seconds()
        return 0 <= age <= max_age_seconds


@dataclass(frozen=True)
class TriggerRule:
    rule_id: str
    ticker: str
    side: ReviewSide
    low: float | None = None
    high: float | None = None
    at_or_below: float | None = None
    at_or_above: float | None = None
    cooldown_seconds: int = 43_200
    rearm_above: float | None = None
    rearm_below: float | None = None
    user_rationale: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        modes = sum((self.low is not None or self.high is not None, self.at_or_below is not None, self.at_or_above is not None))
        if modes != 1:
            raise ValueError("rule must define exactly one zone or threshold mode")
        if (self.low is None) != (self.high is None):
            raise ValueError("zone requires both low and high")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("zone low cannot exceed high")
        for value in (self.low, self.high, self.at_or_below, self.at_or_above, self.rearm_above, self.rearm_below):
            if value is not None and value <= 0:
                raise ValueError("prices must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")

    def matches(self, price: float) -> bool:
        if self.low is not None and self.high is not None:
            return self.low <= price <= self.high
        if self.at_or_below is not None:
            return price <= self.at_or_below
        return self.at_or_above is not None and price >= self.at_or_above

    def is_rearmed(self, price: float) -> bool:
        if self.rearm_above is not None:
            return price >= self.rearm_above
        if self.rearm_below is not None:
            return price <= self.rearm_below
        if self.low is not None and self.high is not None:
            return price > self.high if self.side is ReviewSide.BUY_REVIEW else price < self.low
        if self.at_or_below is not None:
            return price > self.at_or_below
        return self.at_or_above is not None and price < self.at_or_above


@dataclass(frozen=True)
class WatchState:
    rule_id: str
    state: RuleState = RuleState.ARMED
    last_triggered_at: datetime | None = None
    last_price: float | None = None


@dataclass(frozen=True)
class WatchEvent:
    event_id: str
    rule_id: str
    ticker: str
    side: ReviewSide
    price: float
    quote_source: str
    quoted_at: datetime
    triggered_at: datetime
    user_rationale: str
    action: str = "RESEARCH_REVIEW_REQUIRED"
    execution: str = "NO_ORDER"
