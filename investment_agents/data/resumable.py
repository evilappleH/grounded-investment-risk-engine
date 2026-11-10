"""Resumable, current-analysis-only fundamentals acquisition."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import threading
import time
from typing import Any, Mapping, Sequence

from .models import (
    CurrentFundamentalsMetrics, FieldMetadata, FinancialPeriod, FundamentalRecord,
    FundamentalsAnalysisMode, FundamentalsHistory, FundamentalsResult,
    FundamentalsStatus, PercentageUnit,
)
from .universe import normalize_us_ticker


ACQUISITION_STATES = {"IN_PROGRESS", "PARTIAL_PROVIDER_FAILURE", "COMPLETE", "COMMITTED"}
FAILURE_CODES = {"TIMEOUT", "RATE_LIMIT", "NETWORK_ERROR", "PROVIDER_EMPTY_RESPONSE", "SCHEMA_ERROR", "DATA_GENUINELY_UNAVAILABLE", "STALE_CACHE", "SUCCESS"}


def canonical_vendor_ticker(ticker: str) -> str:
    value = normalize_us_ticker(ticker)
    return value.replace("-", ".") if value in {"BRK-B", "BF-B"} else value


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _dt(value: object | None) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _date(value: object | None) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _metadata(payload: Mapping[str, object]) -> FieldMetadata:
    return FieldMetadata(str(payload.get("source", "")), payload.get("basis"), _date(payload.get("period")), _dt(payload.get("as_of")), _dt(payload.get("retrieved_at")))


def _record(payload: Mapping[str, object]) -> FundamentalRecord:
    values = dict(payload)
    values["period"] = FinancialPeriod(values["period"])
    values["report_period"] = _date(values["report_period"])
    for key in ("filing_date",):
        values[key] = _date(values.get(key))
    for key in ("published_at", "available_at", "retrieved_at"):
        values[key] = _dt(values.get(key))
    values["analysis_mode"] = FundamentalsAnalysisMode(values["analysis_mode"])
    values["field_metadata"] = {k: _metadata(v) for k, v in (values.get("field_metadata") or {}).items()}
    values["field_sources"] = dict(values.get("field_sources") or {})
    values["warnings"] = tuple(values.get("warnings") or ())
    values["metadata"] = dict(values.get("metadata") or {})
    return FundamentalRecord(**values)


def _history(payload: Mapping[str, object] | None) -> FundamentalsHistory | None:
    if payload is None:
        return None
    values = dict(payload)
    values["as_of"] = _dt(values["as_of"])
    values["annual"] = tuple(_record(item) for item in values.get("annual", ()))
    values["quarterly"] = tuple(_record(item) for item in values.get("quarterly", ()))
    values["analysis_mode"] = FundamentalsAnalysisMode(values["analysis_mode"])
    values["retrieved_at"] = _dt(values.get("retrieved_at"))
    values["warnings"] = tuple(values.get("warnings") or ())
    values["raw_annual_periods"] = tuple(_date(x) for x in values.get("raw_annual_periods", ()))
    values["analysis_years"] = tuple(_date(x) for x in values.get("analysis_years", ()))
    values["supporting_anchor_year"] = _date(values.get("supporting_anchor_year"))
    return FundamentalsHistory(**values)


def serialize_fundamentals(result: FundamentalsResult) -> dict[str, object]:
    return _jsonable(result)  # type: ignore[return-value]


def deserialize_fundamentals(payload: Mapping[str, object]) -> FundamentalsResult:
    values = dict(payload)
    values["analysis_mode"] = FundamentalsAnalysisMode(values["analysis_mode"])
    values["status"] = FundamentalsStatus(values["status"])
    values["retrieved_at"] = _dt(values.get("retrieved_at"))
    values["as_of"] = _dt(values["as_of"])
    values["requested_as_of"] = _dt(values.get("requested_as_of"))
    values["data_effective_at"] = _dt(values.get("data_effective_at"))
    metrics = values.get("current_metrics")
    if metrics is not None:
        metrics = dict(metrics)
        metrics["field_metadata"] = {k: _metadata(v) for k, v in (metrics.get("field_metadata") or {}).items()}
        metrics["warnings"] = tuple(metrics.get("warnings") or ())
        metrics["roe_basis"] = metrics.get("roe_basis")
        values["current_metrics"] = CurrentFundamentalsMetrics(**metrics)
    values["current_history"] = _history(values.get("current_history"))
    values["pit_history"] = _history(values.get("pit_history"))
    return FundamentalsResult(**values)


def classify_failure(exc: BaseException) -> str:
    text = str(exc).upper()
    name = type(exc).__name__.upper()
    if "TIMEOUT" in text or "TIMEOUT" in name:
        return "TIMEOUT"
    if "429" in text or "RATE" in text or "THROTTL" in text or "YFRATELIMITERROR" in text or "YFINANCE_FETCH_FAILED" in text:
        return "RATE_LIMIT"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "SCHEMA_ERROR"
    if isinstance(exc, (OSError, ConnectionError)):
        return "NETWORK_ERROR"
    return "NETWORK_ERROR"


class ResumableFundamentalsCache:
    def __init__(self, root: Path, *, validity: timedelta = timedelta(hours=6)) -> None:
        if validity.total_seconds() <= 0:
            raise ValueError("cache validity must be positive")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.validity = validity
        self._lock = threading.Lock()

    def path(self, ticker: str) -> Path:
        return self.root / f"{ticker.replace('.', '_')}.json"

    def load(self, ticker: str, *, now: datetime, reuse_failures: bool = True) -> FundamentalsResult | None:
        path = self.path(ticker)
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if envelope.get("state") not in {"COMPLETE", "PARTIAL_PROVIDER_FAILURE"} or envelope.get("analysis_mode") != "CURRENT_ANALYSIS":
                return None
            if envelope.get("state") == "PARTIAL_PROVIDER_FAILURE" and not reuse_failures:
                return None
            retrieved = _dt(envelope.get("retrieved_at"))
            if retrieved is None or now - retrieved > self.validity:
                return None
            payload = envelope["normalized"]
            expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if expected != envelope.get("normalized_hash"):
                return None
            return deserialize_fundamentals(payload)
        except Exception:
            return None

    def save(self, ticker: str, result: FundamentalsResult, *, requested_as_of: datetime, provider: str, raw_source_hash: str | None, latency_seconds: float, failure_code: str = "SUCCESS") -> None:
        payload = serialize_fundamentals(result)
        normalized_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        envelope = {"ticker": ticker, "requested_as_of": requested_as_of.isoformat(), "retrieved_at": result.retrieved_at.isoformat() if result.retrieved_at else None, "provider": provider, "provider_status": result.status.value, "raw_source_hash": raw_source_hash, "normalized_hash": normalized_hash, "completeness_status": "COMPLETE" if result.status is FundamentalsStatus.AVAILABLE else "PARTIAL", "error_reason": result.reason, "analysis_mode": result.analysis_mode.value, "state": "COMPLETE" if result.status is FundamentalsStatus.AVAILABLE else "PARTIAL_PROVIDER_FAILURE", "failure_code": failure_code, "latency_seconds": latency_seconds, "normalized": payload}
        path = self.path(ticker)
        temp = path.with_suffix(".tmp")
        with self._lock:
            temp.write_text(json.dumps(envelope, sort_keys=True, indent=2), encoding="utf-8")
            temp.replace(path)


class ResumableFundamentalsProvider:
    source = "tradingagents:yfinance"

    def __init__(self, inner: object, cache: ResumableFundamentalsCache, *, workers: int = 4, retries: int = 1, backoff_seconds: float = 0.5, jitter: float = 0.1, retry_cached_failures: bool = False) -> None:
        if not 1 <= workers <= 16 or not 0 <= retries <= 2:
            raise ValueError("worker/retry bounds invalid")
        self.inner, self.cache, self.workers, self.retries, self.backoff_seconds, self.jitter, self.retry_cached_failures = inner, cache, workers, retries, backoff_seconds, jitter, retry_cached_failures
        self.results: dict[str, FundamentalsResult] = {}
        self.metrics: dict[str, object] = {"cache_hits": 0, "cache_misses": 0, "retry_count": 0, "provider_failures": 0, "latencies": []}

    def _one(self, ticker: str, as_of: datetime) -> tuple[str, FundamentalsResult]:
        canonical = canonical_vendor_ticker(ticker)
        cached = self.cache.load(canonical, now=as_of, reuse_failures=not self.retry_cached_failures)
        if cached is not None:
            self.metrics["cache_hits"] = int(self.metrics["cache_hits"]) + 1
            return canonical, cached
        self.metrics["cache_misses"] = int(self.metrics["cache_misses"]) + 1
        last: FundamentalsResult | None = None
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            try:
                result = self.inner.get_fundamentals(canonical, as_of, FundamentalsAnalysisMode.CURRENT_ANALYSIS)
                latency = time.monotonic() - started
                self.metrics["latencies"].append(latency)
                code = "SUCCESS" if result.status is FundamentalsStatus.AVAILABLE else "DATA_GENUINELY_UNAVAILABLE"
                self.cache.save(canonical, result, requested_as_of=as_of, provider=self.source, raw_source_hash=None, latency_seconds=latency, failure_code=code)
                return canonical, result
            except Exception as exc:
                self.metrics["retry_count"] = int(self.metrics["retry_count"]) + (1 if attempt < self.retries else 0)
                last = FundamentalsResult(canonical, FundamentalsAnalysisMode.CURRENT_ANALYSIS, FundamentalsStatus.CURRENT_DATA_UNAVAILABLE, self.source, datetime.now(timezone.utc), as_of, reason=f"{classify_failure(exc)}:{type(exc).__name__}", requested_as_of=as_of)
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * (2 ** attempt) + random.random() * self.jitter)
        self.metrics["provider_failures"] = int(self.metrics["provider_failures"]) + 1
        assert last is not None
        self.cache.save(canonical, last, requested_as_of=as_of, provider=self.source, raw_source_hash=None, latency_seconds=0.0, failure_code=classify_failure(Exception(last.reason or "")))
        return canonical, last

    def prefetch(self, tickers: Sequence[str], as_of: datetime) -> Mapping[str, FundamentalsResult]:
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="fundamentals") as pool:
            futures = [pool.submit(self._one, ticker, as_of) for ticker in tickers]
            for future in as_completed(futures):
                ticker, result = future.result()
                self.results[ticker] = result
        return self.results

    def get_fundamentals(self, ticker: str, as_of: datetime, analysis_mode: FundamentalsAnalysisMode) -> FundamentalsResult:
        if analysis_mode is not FundamentalsAnalysisMode.CURRENT_ANALYSIS:
            return FundamentalsResult(ticker, analysis_mode, FundamentalsStatus.PIT_DATA_UNAVAILABLE, self.source, datetime.now(timezone.utc), as_of, reason="BACKTEST_PIT_FAIL_CLOSED")
        canonical = canonical_vendor_ticker(ticker)
        if canonical not in self.results:
            _, result = self._one(canonical, as_of)
            self.results[canonical] = result
        return self.results[canonical]
