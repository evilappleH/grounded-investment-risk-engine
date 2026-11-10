"""SEC EDGAR/XBRL first fundamentals provider.

This module deliberately contains no investment judgement.  It converts
official SEC companyfacts into the existing annual fundamentals contract and
keeps filing provenance on every field.  Network access is rate limited and
cacheable; callers may inject a transport for deterministic tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping

import requests

from .models import (
    CurrentFundamentalsMetrics, FieldMetadata, FinancialPeriod, FundamentalRecord,
    FundamentalsAnalysisMode, FundamentalsHistory, FundamentalsResult,
    FundamentalsStatus,
)
from .universe import normalize_us_ticker


class SECProviderError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}{(': ' + message) if message else ''}")
        self.code = code


@dataclass(frozen=True)
class SECProviderConfig:
    user_agent: str
    cache_dir: Path = Path("data_cache/sec_companyfacts")
    min_request_interval: float = 0.25
    timeout_seconds: float = 20.0
    retries: int = 2
    current_cache_max_age_seconds: float = 86400.0


_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "total_assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "gross_profit": ("GrossProfit",),
    "long_term_debt": ("LongTermDebtNoncurrent",),
    # Do not label a current-only debt concept as total debt; a complete
    # liability roll-up requires issuer-specific reconciliation.
    "total_debt": ("LongTermDebtNoncurrent",),
    "diluted_average_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}


class SECFirstFundamentalsProvider:
    """Official SEC companyfacts provider for CURRENT_ANALYSIS.

    A CIK map may be supplied by the caller (the production universe provider
    persists one); otherwise the official SEC ticker map is fetched once and
    cached.  No SEC response is ever labelled BACKTEST_PIT.
    """
    source = "SEC:companyfacts"
    ticker_map_url = "https://www.sec.gov/files/company_tickers.json"
    facts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    def __init__(self, *, config: SECProviderConfig, get: Callable[..., Any] | None = None,
                 cik_map: Mapping[str, str] | None = None) -> None:
        if not config.user_agent or "@" not in config.user_agent:
            raise ValueError("SEC provider requires an identifying User-Agent")
        self.config = config
        self._get = get or requests.get
        self._cik_map = {normalize_us_ticker(k): str(v).zfill(10) for k, v in (cik_map or {}).items()}
        self._last_request = 0.0
        self._lock = threading.Lock()
        Path(config.cache_dir).mkdir(parents=True, exist_ok=True)

    @property
    def cik_map(self) -> Mapping[str, str]:
        if not self._cik_map:
            map_path = Path(self.config.cache_dir) / "ticker_cik_map.json"
            if map_path.exists():
                try:
                    cached = json.loads(map_path.read_text(encoding="utf-8"))
                    if isinstance(cached, Mapping) and isinstance(cached.get("mapping"), Mapping):
                        self._cik_map = {normalize_us_ticker(str(k)): str(v).zfill(10)
                                         for k, v in cached["mapping"].items()}
                except (OSError, ValueError, TypeError):
                    self._cik_map = {}
            if not self._cik_map:
                payload = self._request_json(self.ticker_map_url)
                self._cik_map = {normalize_us_ticker(str(v["ticker"])): str(v["cik_str"]).zfill(10)
                                 for v in payload.values() if v.get("ticker") and v.get("cik_str") is not None}
                envelope = {"source_url": self.ticker_map_url,
                            "retrieved_at": datetime.now(timezone.utc).isoformat(),
                            "mapping": dict(self._cik_map), "schema_version": "sec-ticker-cik-v1"}
                tmp = map_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
                tmp.replace(map_path)
        return dict(self._cik_map)

    def _request_json(self, url: str) -> Mapping[str, Any]:
        with self._lock:
            delay = self.config.min_request_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
        last: Any = None
        for attempt in range(self.config.retries + 1):
            try:
                response = self._get(url, headers={"User-Agent": self.config.user_agent}, timeout=self.config.timeout_seconds)
                status = getattr(response, "status_code", None)
                last = status
                if status == 429:
                    if attempt < self.config.retries:
                        time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                        continue
                    raise SECProviderError("SEC_RATE_LIMIT")
                if isinstance(status, int) and status >= 500:
                    if attempt < self.config.retries:
                        time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                        continue
                    raise SECProviderError("SEC_TIMEOUT")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise SECProviderError("SEC_EMPTY")
                return payload
            except SECProviderError:
                raise
            except (requests.Timeout, TimeoutError):
                if attempt >= self.config.retries:
                    raise SECProviderError("SEC_TIMEOUT")
            except requests.RequestException as exc:
                if attempt >= self.config.retries:
                    raise SECProviderError("SEC_EMPTY", type(exc).__name__)
        raise SECProviderError("SEC_EMPTY", str(last))

    def _facts(self, cik: str) -> Mapping[str, Any]:
        path = Path(self.config.cache_dir) / f"CIK{cik}.json"
        if path.exists() and (time.time() - path.stat().st_mtime) <= self.config.current_cache_max_age_seconds:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, Mapping) and "facts" in payload:
                    return payload
            except (OSError, ValueError):
                pass
        payload = self._request_json(self.facts_url.format(cik=int(cik)))
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        meta = {"source_url": self.facts_url.format(cik=int(cik)), "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(raw).hexdigest(), "extraction_version": "sec-provider-v1"}
        path.with_suffix(".meta.json").write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
        return payload

    @staticmethod
    def _annual_units(facts: Mapping[str, Any], concept: str) -> list[Mapping[str, Any]]:
        entry = facts.get("facts", {}).get("us-gaap", {}).get(concept, {})
        units = entry.get("units", {}) if isinstance(entry, Mapping) else {}
        unit_name = "USD" if "USD" in units else ("shares" if "shares" in units else next(iter(units), None))
        rows = units.get(unit_name, []) if unit_name else []
        return [r for r in rows if isinstance(r, Mapping) and str(r.get("form", "")).startswith("10-K")
                and (r.get("fp") in ("FY", None)) and r.get("fy") is not None and r.get("val") is not None]

    def _build(self, ticker: str, cik: str, facts: Mapping[str, Any], retrieved: datetime, requested_as_of: datetime) -> FundamentalsResult:
        by_year: dict[int, dict[str, tuple[float, Mapping[str, Any]]]] = {}
        warnings: list[str] = []
        for field, concepts in _CONCEPTS.items():
            selected: dict[int, tuple[float, Mapping[str, Any]]] = {}
            for concept in concepts:
                for row in self._annual_units(facts, concept):
                    year = int(row["fy"])
                    # Prefer 10-K/A only as an amendment to the same fiscal year;
                    # otherwise first concept precedence wins deterministically.
                    if year not in selected or (str(row.get("form")) == "10-K/A" and str(selected[year][1].get("form")) != "10-K/A"):
                        selected[year] = (float(row["val"]), {**row, "concept": concept})
                if selected:
                    break
            for year, value in selected.items():
                by_year.setdefault(year, {})[field] = value
        if not by_year:
            return FundamentalsResult(ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE, source=self.source,
                retrieved_at=retrieved, as_of=retrieved, requested_as_of=requested_as_of, reason="FILING_HISTORY_INSUFFICIENT")
        records: list[FundamentalRecord] = []
        for year in sorted(by_year, reverse=True):
            values = by_year[year]; period_date = date(year, 12, 31)
            field_sources: dict[str, str] = {}; metadata: dict[str, FieldMetadata] = {}
            kwargs: dict[str, Any] = {}
            for field, (value, row) in values.items():
                kwargs[field] = value; field_sources[field] = f"SEC:{row['concept']}"
                filing = row.get("filed"); filed = date.fromisoformat(filing) if isinstance(filing, str) and len(filing) >= 10 else None
                metadata[field] = FieldMetadata(source=f"SEC:{row['concept']}", basis="issuer:XBRL", period=period_date,
                    as_of=retrieved, retrieved_at=retrieved)
            filing_values = [r.get("filed") for _, r in values.values() if r.get("filed")]
            filing_date = date.fromisoformat(str(filing_values[0])[:10]) if filing_values else None
            records.append(FundamentalRecord(ticker=ticker, period=FinancialPeriod.ANNUAL, report_period=period_date,
                filing_date=filing_date,
                published_at=retrieved, available_at=None, source=self.source, retrieved_at=retrieved,
                analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS, field_sources=field_sources,
                field_metadata=metadata, warnings=tuple(warnings), **kwargs))
        history = FundamentalsHistory(ticker=ticker, as_of=retrieved, annual=tuple(records), source=self.source,
            retrieved_at=retrieved, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
            raw_annual_periods=tuple(r.report_period for r in records))
        latest = records[0]
        metrics = CurrentFundamentalsMetrics(revenue=latest.revenue, net_income=latest.net_income, equity=latest.equity,
            total_debt=latest.total_debt, cash=latest.cash, field_metadata=latest.field_metadata,
            warnings=("SEC_ISSUER_BASIS_SHARE_CLASS_DISTINCT",))
        return FundamentalsResult(ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
            status=FundamentalsStatus.AVAILABLE, source=self.source, retrieved_at=retrieved, as_of=retrieved,
            current_metrics=metrics, current_history=history, requested_as_of=requested_as_of, data_effective_at=retrieved)

    def get_fundamentals(self, ticker: str, as_of: datetime, mode: FundamentalsAnalysisMode | str = FundamentalsAnalysisMode.CURRENT_ANALYSIS) -> FundamentalsResult:
        mode = FundamentalsAnalysisMode(mode)
        if mode is not FundamentalsAnalysisMode.CURRENT_ANALYSIS:
            return FundamentalsResult(ticker=ticker, analysis_mode=mode, status=FundamentalsStatus.PIT_DATA_UNAVAILABLE,
                source=self.source, retrieved_at=None, as_of=as_of, reason="SEC_CURRENT_ANALYSIS_ONLY")
        canonical = normalize_us_ticker(ticker)
        cik = self.cik_map.get(canonical)
        if cik is None:
            return FundamentalsResult(ticker=canonical, analysis_mode=mode, status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE,
                source=self.source, retrieved_at=None, as_of=as_of, reason="CIK_MAPPING_FAILED")
        retrieved = datetime.now(timezone.utc)
        try:
            return self._build(canonical, cik, self._facts(cik), retrieved, as_of)
        except SECProviderError as exc:
            return FundamentalsResult(ticker=canonical, analysis_mode=mode, status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE,
                source=self.source, retrieved_at=retrieved, as_of=retrieved, reason=exc.code)


__all__ = ["SECFirstFundamentalsProvider", "SECProviderConfig", "SECProviderError"]
