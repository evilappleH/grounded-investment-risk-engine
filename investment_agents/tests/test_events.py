"""Offline tests for Step 8A event evidence adapters."""
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from investment_agents.events import *


class Resp:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class EventTests(unittest.TestCase):
    def test_sec_cik_and_filter_mapping(self):
        calls = []
        def get(url, **kwargs):
            calls.append((url, kwargs))
            if "company_tickers" in url: return Resp({"0": {"ticker": "KO", "cik_str": 21344}})
            return Resp({"filings": {"recent": {"form": ["10-K", "8-K", "S-1"], "filingDate": ["2026-01-10", "2026-01-20", "2026-01-21"], "reportDate": ["2025-12-31", "2026-01-20", "2026-01-21"], "accessionNumber": ["1-2", "3-4", "5-6"], "primaryDocument": ["a.htm", "b.htm", "c.htm"]}}})
        out = SECEdgarAdapter(user_agent="App contact@example.com", get=get).fetch_filings("KO", start=date(2026,1,1), end=date(2026,1,31))
        self.assertEqual([x.evidence_type for x in out], ["10-K", "8-K"])
        self.assertTrue(calls[0][1]["headers"]["User-Agent"])

    def test_sec_missing_user_agent_is_config_error(self):
        with patch.dict("os.environ", {"SEC_USER_AGENT": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "SEC_PROVIDER_NOT_CONFIGURED"):
                SECEdgarAdapter(user_agent=None).get_cik_map()

    def test_news_structured_mapping_and_unstructured(self):
        adapter = TradingAgentsNewsAdapter(loader=lambda *args: [{"title": "A", "link": "https://x", "publisher": "P"}, {"title": "A", "link": "https://x", "publisher": "P"}])
        items, warning = adapter.fetch("KO", start=date(2026,1,1), end=date(2026,1,2))
        self.assertEqual(len(items), 2); self.assertEqual(items[1].warnings, ("POSSIBLE_DUPLICATE",)); self.assertIsNone(warning)
        items, warning = TradingAgentsNewsAdapter(loader=lambda *args: "free text").fetch("KO", start=date(2026,1,1), end=date(2026,1,2))
        self.assertEqual(items, ()); self.assertEqual(warning, "NEWS_SCHEMA_UNSTRUCTURED")

    def test_direct_yfinance_and_alpha_structured_mapping(self):
        class Ticker:
            def get_news(self): return [{"content": {"title": "A", "canonicalUrl": {"url": "https://a"}, "pubDate": "2026-01-01T00:00:00Z"}}]
        items, warning = StructuredYFinanceNewsAdapter(ticker_factory=lambda _: Ticker()).fetch("KO", start=date(2025,1,1), end=date(2026,1,2))
        self.assertEqual(items[0].provenance["integration"], "investment_agents_direct_structured_adapter")
        items, warning = AlphaVantageNewsAdapter(loader=lambda *args: {"feed": [{"title": "A", "url": "https://a"}]}).fetch("KO", start=date(2025,1,1), end=date(2026,1,2))
        self.assertEqual(items[0].source, "alpha_vantage")

    def test_macro_and_sentiment_structured_mapping(self):
        macro, warning = MacroEvidenceAdapter(loader=lambda *args, **kwargs: {"series_id": "DGS10", "value": 4.2}).fetch(end=date(2026,1,1), lookback_days=30)
        self.assertEqual(macro[0].source_class, EvidenceSourceClass.MACRO); self.assertIsNone(warning)
        sentiment, warning = SentimentEvidenceAdapter(loader=lambda ticker: [{"id": "1"}]).fetch("KO", start=date(2026,1,1), end=date(2026,1,2))
        self.assertEqual(len(sentiment), 1); self.assertIsNone(warning)
        sentiment, warning = SentimentEvidenceAdapter(loader=lambda ticker: "HTTP 429 Too Many Requests").fetch("KO", start=date(2026,1,1), end=date(2026,1,2))
        self.assertEqual(warning, "PROVIDER_RATE_LIMITED")

    def test_fred_structured_context_and_missing_key(self):
        class R:
            def raise_for_status(self): pass
            def json(self): return {"observations": [{"date": "2026-01-01", "value": "4.2"}]}
        ctx = FREDStructuredMacroAdapter(api_key="fixture", get=lambda *a, **k: R()).fetch_context(run_id="r", end=date(2026,1,2), series=("10y_treasury",))
        self.assertEqual(len(ctx.observations), 1); self.assertEqual(ctx.observations[0].series_id, "DGS10")
        ctx = FREDStructuredMacroAdapter(api_key="", get=lambda *a, **k: R()).fetch_context(end=date(2026,1,2), series=("10y_treasury",))
        self.assertIn("FRED_PROVIDER_NOT_CONFIGURED", ctx.source_failures["fred"])

    def test_capability_registry_is_boolean_and_secret_safe(self):
        capabilities = event_provider_capabilities()
        self.assertTrue(all(isinstance(item.configured, bool) for item in capabilities))
        self.assertFalse(any("API_KEY" in repr(item) for item in capabilities))

    def test_provider_failure_isolated_and_no_evidence_distinct(self):
        class GoodNews:
            def fetch(self, *args, **kwargs): return (), None
        class Fail:
            def fetch(self, *args, **kwargs): raise RuntimeError("down")
        package = collect_event_evidence("KO", run_id="r", end=date(2026,1,2), sec=Fail(), news=GoodNews(), macro=GoodNews(), sentiment=GoodNews())
        self.assertIn("sec", package.source_failures); self.assertIn("NO_EVIDENCE_FOUND:news", package.source_warnings); self.assertEqual(package.provenance["pit_safe"], False)

    def test_lookback_and_persistence(self):
        package = collect_event_evidence("KO", run_id="r", lookback_days=30, end=date(2026,2,1), sec=type("S",(),{"fetch_filings":lambda *a,**k: ()})(), news=type("N",(),{"fetch":lambda *a,**k:((),None)})(), macro=type("M",(),{"fetch":lambda *a,**k:((),None)})(), sentiment=type("T",(),{"fetch":lambda *a,**k:((),None)})())
        self.assertEqual((package.lookback_end-package.lookback_start).days, 30)
        with TemporaryDirectory() as temp:
            path = persist_event_package(package, root=Path(temp)); self.assertTrue(path.exists())

    def test_macro_context_is_reused_and_referenced_per_ticker(self):
        class R:
            def raise_for_status(self): pass
            def json(self): return {"observations": [{"date": "2026-01-01", "value": "4.2"}]}
        calls = []
        def get(*args, **kwargs):
            calls.append(args[0]); return R()
        ctx = FREDStructuredMacroAdapter(api_key="fixture", get=get).fetch_context(run_id="run", end=date(2026,1,2), series=("10y_treasury",))
        self.assertEqual(len(calls), 1)
        kwargs = {"run_id": "run", "end": date(2026,1,2), "macro_context": ctx,
                  "sec": type("S",(),{"fetch_filings":lambda *a,**k: ()})(),
                  "news": type("N",(),{"fetch":lambda *a,**k:((),None)})(),
                  "sentiment": type("T",(),{"fetch":lambda *a,**k:((),None)})()}
        first = collect_event_evidence("KO", **kwargs)
        kwargs["ticker"] = "AAPL"
        second = collect_event_evidence(**kwargs)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.macro_context_reference["run_id"], second.macro_context_reference["run_id"])
        self.assertEqual(len(first.macro_evidence), 1)
        self.assertFalse(first.macro_context_reference["pit_safe"])


if __name__ == "__main__": unittest.main()
