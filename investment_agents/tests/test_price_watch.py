from datetime import datetime, timedelta, timezone
from pathlib import Path

from investment_agents.price_watch import JsonStateStore, PriceQuote, PriceWatchEngine, TriggerRule
from investment_agents.price_watch.models import ReviewSide, RuleState


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def quote(price, *, age=0, status="AVAILABLE"):
    return PriceQuote("AOS", price, NOW - timedelta(seconds=age), "fixture", status)


def test_threshold_triggers_once_then_requires_rearm():
    rule = TriggerRule("aos-buy-1", "AOS", ReviewSide.BUY_REVIEW, at_or_below=55, rearm_above=56)
    engine = PriceWatchEngine(max_quote_age_seconds=100_000)
    events, state = engine.evaluate((rule,), {"AOS": quote(54.9)}, {}, now=NOW)
    assert len(events) == 1 and events[0].execution == "NO_ORDER"
    events, state = engine.evaluate((rule,), {"AOS": quote(54.8)}, state, now=NOW + timedelta(hours=13))
    assert events == () and state[rule.rule_id].state is RuleState.TRIGGERED
    events, state = engine.evaluate((rule,), {"AOS": quote(56.1)}, state, now=NOW + timedelta(hours=14))
    assert events == () and state[rule.rule_id].state is RuleState.ARMED
    events, _ = engine.evaluate((rule,), {"AOS": quote(54.7)}, state, now=NOW + timedelta(hours=15))
    assert len(events) == 1


def test_zone_sell_rule_and_stale_quote_fail_closed():
    rule = TriggerRule("aos-sell", "AOS", ReviewSide.SELL_REVIEW, low=80, high=85)
    engine = PriceWatchEngine(max_quote_age_seconds=60)
    events, _ = engine.evaluate((rule,), {"AOS": quote(82, age=61)}, {}, now=NOW)
    assert events == ()
    events, _ = engine.evaluate((rule,), {"AOS": quote(82)}, {}, now=NOW)
    assert len(events) == 1


def test_state_round_trip_is_persistent(tmp_path: Path):
    rule = TriggerRule("aos-buy-1", "AOS", ReviewSide.BUY_REVIEW, at_or_below=55)
    _, states = PriceWatchEngine().evaluate((rule,), {"AOS": quote(54)}, {}, now=NOW)
    store = JsonStateStore(tmp_path / "watch-state.json")
    store.save(states)
    assert store.load() == states
