"""US-equities universe contracts, with a static offline implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
import re


class UniverseKind(StrEnum):
    SP_500 = "sp_500"
    NASDAQ_100 = "nasdaq_100"
    RUSSELL_1000 = "russell_1000"
    CUSTOM = "custom"


class InvalidTickerError(ValueError):
    """Raised when a supplied ticker is not a normalized US-equities symbol."""


_US_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def normalize_us_ticker(ticker: str) -> str:
    """Normalize case/whitespace and reject invalid ticker input explicitly."""
    if not isinstance(ticker, str):
        raise InvalidTickerError("ticker must be a string")
    normalized = ticker.strip().upper()
    if not _US_TICKER.fullmatch(normalized):
        raise InvalidTickerError(f"invalid US-equities ticker: {ticker!r}")
    return normalized


@dataclass(frozen=True)
class UniverseConstituent:
    canonical_ticker: str
    vendor_ticker: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class StockUniverse:
    """The permitted instruments for a future analysis cycle; no ranking exists here."""

    kind: UniverseKind
    tickers: tuple[str, ...]
    source: str
    source_date: date | None = None
    name: str | None = None
    fetched_at: datetime | None = None
    constituents: tuple[UniverseConstituent, ...] = ()


class StaticUniverseProvider:
    """Offline provider for a caller-supplied ticker collection.

    It normalizes duplicates while retaining first-seen order. It neither
    downloads index constituents nor decides which ticker is investable.
    """

    def __init__(
        self,
        tickers: tuple[str, ...] | list[str],
        *,
        kind: UniverseKind = UniverseKind.CUSTOM,
        source: str = "static",
        source_date: date | None = None,
        name: str | None = None,
    ) -> None:
        unique: list[str] = []
        seen: set[str] = set()
        for ticker in tickers:
            normalized = normalize_us_ticker(ticker)
            if normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        constituents = tuple(UniverseConstituent(t, t) for t in unique)
        self._universe = StockUniverse(kind, tuple(unique), source, source_date, name, constituents=constituents)

    def get_universe(self) -> StockUniverse:
        """Return the configured offline universe without network activity."""
        return self._universe
