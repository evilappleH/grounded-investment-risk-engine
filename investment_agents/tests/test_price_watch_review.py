from datetime import datetime, timezone

from investment_agents.price_watch.models import ReviewSide, WatchEvent
from investment_agents.price_watch.review import TriggeredResearchReviewer
from investment_agents.price_watch.moomoo_quotes import MoomooQuoteProvider


def test_missing_research_package_fails_closed(tmp_path):
    now = datetime.now(timezone.utc)
    event = WatchEvent("e1", "r1", "AOS", ReviewSide.BUY_REVIEW, 55, "fixture", now, now, "rationale")
    result = TriggeredResearchReviewer(tmp_path).review(event)
    assert result["status"] == "INSUFFICIENT_CURRENT_EVIDENCE"
    assert result["execution"] == "NO_ORDER"


def test_quote_provider_creates_no_trade_context(monkeypatch):
    calls = []

    class QuoteContext:
        def close(self):
            calls.append("close")

    class SDK:
        def OpenQuoteContext(self, **_kwargs):
            calls.append("quote")
            return QuoteContext()

        def OpenSecTradeContext(self, **_kwargs):
            calls.append("trade")
            raise AssertionError("trade context must never be created")

    monkeypatch.setattr(
        "investment_agents.price_watch.moomoo_quotes.OfficialMoomooTransport._import_sdk_with_writable_log",
        lambda: SDK(),
    )
    with MoomooQuoteProvider():
        pass
    assert calls == ["quote", "close"]
