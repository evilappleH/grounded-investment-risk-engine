"""Minimal vendor-neutral provider protocols; concrete vendors stay outside agents."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from .models import FundamentalsAnalysisMode, FundamentalsResult, PriceHistory
from .universe import StockUniverse


@runtime_checkable
class UniverseProvider(Protocol):
    """Answers only which US-equities symbols are permitted for an analysis cycle."""

    def get_universe(self) -> StockUniverse: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Supplies daily market data; callers request a five-year window by default."""

    def get_daily_prices(self, ticker: str, start: date, end: date) -> PriceHistory: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    """Supplies filing-aware fundamental records available at a requested time."""

    def get_fundamentals(
        self, ticker: str, as_of: datetime, mode: FundamentalsAnalysisMode | str
    ) -> FundamentalsResult: ...
