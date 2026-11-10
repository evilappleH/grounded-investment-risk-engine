"""Current S&P 500 constituent provider with explicit ticker mappings."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable
from io import StringIO

import pandas as pd
import requests

from investment_agents.data.universe import (
    InvalidTickerError, StockUniverse, UniverseConstituent, UniverseKind, normalize_us_ticker,
)


SP500_SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def canonicalize_sp500_ticker(raw: str) -> str:
    """Normalize known index/vendor share-class spellings to canonical form."""
    text = str(raw).strip().upper()
    if text in {"BRK-B", "BF-B"}:
        text = text.replace("-", ".")
    return normalize_us_ticker(text)


def vendor_ticker(canonical_ticker: str) -> str:
    """Convert canonical share-class notation only at the vendor boundary."""
    canonical = normalize_us_ticker(canonical_ticker)
    return canonical.replace(".", "-")


def parse_sp500_rows(rows: Iterable[dict[str, Any]]) -> tuple[UniverseConstituent, ...]:
    result: list[UniverseConstituent] = []
    seen: set[str] = set()
    for row in rows:
        raw = row.get("Symbol", row.get("symbol"))
        if raw is None or not str(raw).strip():
            continue
        canonical = canonicalize_sp500_ticker(str(raw))
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(UniverseConstituent(
            canonical_ticker=canonical,
            vendor_ticker=vendor_ticker(canonical),
            company_name=str(row.get("Security", row.get("Name", "")) or "") or None,
            sector=str(row.get("GICS Sector", row.get("Sector", "")) or "") or None,
            industry=str(row.get("GICS Sub-Industry", row.get("Industry", "")) or "") or None,
            metadata={"source_symbol": str(raw)},
        ))
    return tuple(result)


class SP500UniverseProvider:
    """Fetch the current public S&P 500 constituent table; never PIT history."""

    source = SP500_SOURCE_URL

    def __init__(self, *, html_loader: Any | None = None) -> None:
        self._html_loader = html_loader or self._read_source

    @staticmethod
    def _read_source(url: str) -> list[pd.DataFrame]:
        response = requests.get(url, headers={"User-Agent": "TradingAgentsLab/Step6A (public research)"}, timeout=20)
        response.raise_for_status()
        return list(pd.read_html(StringIO(response.text)))

    def get_universe(self) -> StockUniverse:
        fetched_at = datetime.now(timezone.utc)
        tables = self._html_loader(self.source)
        if not tables:
            raise ValueError("S&P 500 source returned no tables")
        table = next((item for item in tables if "Symbol" in item.columns), None)
        if table is None:
            raise ValueError("S&P 500 source has no Symbol column")
        constituents = parse_sp500_rows(table.to_dict("records"))
        if not constituents:
            raise ValueError("S&P 500 source returned an empty constituent set")
        return StockUniverse(
            kind=UniverseKind.SP_500,
            tickers=tuple(item.canonical_ticker for item in constituents),
            source=self.source,
            source_date=date.today(),
            name="S&P 500 current constituents",
            fetched_at=fetched_at,
            constituents=constituents,
        )
