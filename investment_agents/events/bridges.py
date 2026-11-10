"""Thin bridges over TradingAgents' existing text/structured data boundaries."""

from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping
import uuid
import os
import json
import requests
from pathlib import Path

from .models import EventEvidence, EventEvidencePackage, EvidenceSourceClass, SentimentEvidence


def _published_time(value: Any) -> datetime | date | None:
    """Normalize only explicit provider timestamps; never infer a missing time."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _within_window(value: datetime | date | None, start: date, end: date) -> bool:
    observed = value.date() if isinstance(value, datetime) else value
    return observed is None or start <= observed <= end


def _dedupe(evidence: list[EventEvidence]) -> tuple[EventEvidence, ...]:
    seen: set[str] = set(); output: list[EventEvidence] = []
    for item in evidence:
        key = item.reference or item.evidence_id
        if key in seen:
            output.append(EventEvidence(**{**item.__dict__, "warnings": tuple(dict.fromkeys((*item.warnings, "POSSIBLE_DUPLICATE"))) }))
        else:
            seen.add(key); output.append(item)
    return tuple(output)


class TradingAgentsNewsAdapter:
    source = "tradingagents:news"
    def __init__(self, loader: Callable[[str, str, str], Any] | None = None) -> None:
        self.loader = loader
    def fetch(self, ticker: str, *, start: date, end: date) -> tuple[tuple[EventEvidence, ...], str | None]:
        if self.loader is None:
            from tradingagents.dataflows.yfinance_news import get_news_yfinance
            self.loader = get_news_yfinance
        raw = self.loader(ticker, start.isoformat(), end.isoformat())
        if isinstance(raw, str):
            return (), "NEWS_SCHEMA_UNSTRUCTURED"
        if not isinstance(raw, (list, tuple)):
            return (), "NEWS_SCHEMA_UNSUPPORTED"
        output: list[EventEvidence] = []
        now = datetime.now(timezone.utc)
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            title, reference = item.get("title"), item.get("link") or item.get("url")
            if not title or not reference:
                continue
            published = _published_time(item.get("pub_date") or item.get("providerPublishTime"))
            if not _within_window(published, start, end):
                continue
            output.append(EventEvidence(f"news:{reference}", ticker, "NEWS_ARTICLE", EvidenceSourceClass.NEWS, str(item.get("publisher", self.source)), str(title), published_at=published, fetched_at=now, reference=str(reference), summary=item.get("summary"), provenance={"adapter": self.source}, warnings=()))
        return _dedupe(output), None


class StructuredYFinanceNewsAdapter:
    """Direct yfinance structured-news boundary; not a TradingAgents API."""
    source = "yfinance"
    integration = "investment_agents_direct_structured_adapter"
    def __init__(self, ticker_factory: Callable[[str], Any] | None = None) -> None:
        self.ticker_factory = ticker_factory
        self._cache_configured = False

    def _configure_cache(self) -> None:
        if self._cache_configured or self.ticker_factory is not None:
            return
        import yfinance.cache as yf_cache
        cache_dir = Path(__file__).resolve().parents[2] / "data_cache" / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf_cache.set_cache_location(str(cache_dir))
        self._cache_configured = True
    def fetch(self, ticker: str, *, start: date, end: date) -> tuple[tuple[EventEvidence, ...], str | None]:
        if self.ticker_factory is None:
            self._configure_cache()
            import yfinance as yf
            self.ticker_factory = yf.Ticker
        raw = self.ticker_factory(ticker).get_news()
        if not isinstance(raw, (list, tuple)): return (), "YFINANCE_NEWS_SCHEMA_UNSUPPORTED"
        now = datetime.now(timezone.utc); output = []
        for item in raw:
            if not isinstance(item, Mapping): continue
            content = item.get("content") if isinstance(item.get("content"), Mapping) else item
            title = content.get("title")
            reference = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), Mapping) else content.get("link") or content.get("url")
            published = _published_time(content.get("pubDate") or content.get("providerPublishTime"))
            if not title or not reference: continue
            if not _within_window(published, start, end): continue
            output.append(EventEvidence(f"yfinance:{reference}", ticker.upper(), "NEWS_ARTICLE", EvidenceSourceClass.NEWS, self.source, str(title), published_at=published, fetched_at=now, reference=str(reference), summary=content.get("summary") if isinstance(content.get("summary"), str) else None, provenance={"source": self.source, "integration": self.integration}))
        return _dedupe(output), None


class AlphaVantageNewsAdapter:
    source = "alpha_vantage"
    def __init__(self, loader: Callable[..., Any] | None = None, api_key: str | None = None) -> None: self.loader = loader; self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
    def fetch(self, ticker: str, *, start: date, end: date) -> tuple[tuple[EventEvidence, ...], str | None]:
        if not self.api_key and self.loader is None: return (), "ALPHAVANTAGE_PROVIDER_NOT_CONFIGURED"
        if self.loader is None:
            from tradingagents.dataflows.alpha_vantage_news import get_news
            self.loader = get_news
        raw = self.loader(ticker, start.isoformat(), end.isoformat())
        if not isinstance(raw, Mapping): return (), "ALPHAVANTAGE_NEWS_SCHEMA_UNSUPPORTED"
        feed = raw.get("feed")
        if not isinstance(feed, list): return (), "ALPHAVANTAGE_NEWS_SCHEMA_UNSUPPORTED"
        now = datetime.now(timezone.utc); output=[]
        for item in feed:
            if not isinstance(item, Mapping) or not item.get("title") or not item.get("url"): continue
            published = _published_time(item.get("time_published"))
            if not _within_window(published, start, end): continue
            output.append(EventEvidence(f"alpha_vantage:{item['url']}", ticker.upper(), "NEWS_ARTICLE", EvidenceSourceClass.NEWS, self.source, str(item["title"]), published_at=published, fetched_at=now, reference=str(item["url"]), summary=item.get("summary") if isinstance(item.get("summary"), str) else None, raw_metadata={"time_published": item.get("time_published")}, provenance={"source": self.source}))
        return _dedupe(output), None


class MacroEvidenceAdapter:
    source = "tradingagents:fred"
    def __init__(self, loader: Callable[..., Any] | None = None) -> None: self.loader = loader
    def fetch(self, *, indicator: str = "10y_treasury", end: date, lookback_days: int) -> tuple[tuple[EventEvidence, ...], str | None]:
        if self.loader is None:
            from tradingagents.dataflows.fred import get_macro_data
            self.loader = get_macro_data
        raw = self.loader(indicator, end.isoformat(), lookback_days)
        if not isinstance(raw, Mapping) and isinstance(raw, str):
            return (), "MACRO_SCHEMA_UNSTRUCTURED"
        if not isinstance(raw, Mapping): return (), "MACRO_SCHEMA_UNSUPPORTED"
        return (EventEvidence(f"macro:{indicator}:{end}", "", "MACRO_OBSERVATION", EvidenceSourceClass.MACRO, self.source, indicator, observed_at=end, fetched_at=datetime.now(timezone.utc), raw_metadata=dict(raw), provenance={"series_identifier": indicator}),), None


class SentimentEvidenceAdapter:
    source = "tradingagents:sentiment"
    def __init__(self, loader: Callable[[str], Any] | None = None) -> None: self.loader = loader
    def fetch(self, ticker: str, *, start: date, end: date) -> tuple[tuple[SentimentEvidence, ...], str | None]:
        if self.loader is None:
            from tradingagents.dataflows.reddit import fetch_reddit_posts
            self.loader = lambda symbol: fetch_reddit_posts(symbol, start_date=start.isoformat(), end_date=end.isoformat())
        raw = self.loader(ticker)
        if isinstance(raw, str):
            lowered = raw.lower()
            if "429" in lowered or "too many requests" in lowered or "rate limit" in lowered:
                return (), "PROVIDER_RATE_LIMITED"
            return (), "SENTIMENT_SCHEMA_UNSTRUCTURED"
        if not isinstance(raw, (list, tuple)): return (), "SENTIMENT_SCHEMA_UNSUPPORTED"
        now = datetime.now(timezone.utc)
        return tuple(SentimentEvidence(ticker, self.source, None, now, str(item), {}, {"adapter": self.source}) for item in raw), None
