"""Boolean provider capability/preflight report; never exposes secrets."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import os
from typing import Any

@dataclass(frozen=True)
class EventProviderCapability:
    provider: str; configured: bool; reachable: bool | None; structured_output: bool; raw_or_derived: str; supported_evidence_types: tuple[str, ...]; limitations: tuple[str, ...]

def event_provider_capabilities() -> tuple[EventProviderCapability, ...]:
    return (
        EventProviderCapability("sec", bool(os.getenv("SEC_USER_AGENT")), None, True, "raw", ("10-K","10-Q","8-K","6-K"), ("User-Agent required", "CURRENT_ANALYSIS only")),
        EventProviderCapability("yfinance_structured_news", True, None, True, "raw", ("NEWS_ARTICLE",), ("direct investment_agents adapter",)),
        EventProviderCapability("alpha_vantage", bool(os.getenv("ALPHAVANTAGE_API_KEY")), None, True, "raw", ("NEWS_ARTICLE",), ("API key optional",)),
        EventProviderCapability("fred_structured", bool(os.getenv("FRED_API_KEY")), None, True, "raw", ("official observations",), ("CURRENT_ANALYSIS only",)),
        EventProviderCapability("tradingagents_news_text", True, None, False, "derived_or_unstructured", (), ("formatted text only",)),
        EventProviderCapability("reddit_stocktwits", True, None, False, "derived_or_unstructured", (), ("public functions return text",)),
    )
