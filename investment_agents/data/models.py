"""Vendor-neutral, point-in-time data models for future investment analysis.

These types carry data only. They do not request data, rank securities, or
produce an investment view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from typing import Mapping


DEFAULT_MARKET_HISTORY_YEARS = 5
DEFAULT_FUNDAMENTALS_HISTORY_YEARS = 5


class HistoryCoverage(StrEnum):
    """Observed history coverage; it never implies admission or exclusion."""

    FULL_HISTORY = "FULL_HISTORY"
    LIMITED_HISTORY = "LIMITED_HISTORY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class FinancialPeriod(StrEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    TTM = "ttm"


class FundamentalsAnalysisMode(StrEnum):
    """Explicit boundary between today-visible data and backtest-safe data."""

    CURRENT_ANALYSIS = "CURRENT_ANALYSIS"
    BACKTEST_PIT = "BACKTEST_PIT"
    UNSPECIFIED = "UNSPECIFIED"


class FundamentalsStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    CURRENT_DATA_UNAVAILABLE = "CURRENT_DATA_UNAVAILABLE"
    CURRENT_AS_OF_UNSUPPORTED = "CURRENT_AS_OF_UNSUPPORTED"
    PIT_DATA_UNAVAILABLE = "PIT_DATA_UNAVAILABLE"


class PercentageUnit(StrEnum):
    PERCENT = "percent"
    DECIMAL = "decimal"


def normalize_analysis_mode(mode: FundamentalsAnalysisMode | str) -> FundamentalsAnalysisMode:
    """Normalize the public mode boundary; invalid strings fail closed."""
    if isinstance(mode, FundamentalsAnalysisMode):
        return mode
    if isinstance(mode, str):
        try:
            return FundamentalsAnalysisMode(mode)
        except ValueError as exc:
            raise ValueError(f"invalid fundamentals analysis mode: {mode!r}") from exc
    raise TypeError("fundamentals analysis mode must be FundamentalsAnalysisMode or a valid string")


def validate_fundamentals_usage(data: object, requested_mode: FundamentalsAnalysisMode | str) -> None:
    """Fail closed when current snapshots are presented as PIT-safe history."""
    requested = normalize_analysis_mode(requested_mode)
    actual = getattr(data, "analysis_mode", FundamentalsAnalysisMode.UNSPECIFIED)
    actual = normalize_analysis_mode(actual)
    if requested is FundamentalsAnalysisMode.BACKTEST_PIT:
        if actual is not FundamentalsAnalysisMode.BACKTEST_PIT:
            raise ValueError("CURRENT_ANALYSIS/UNSPECIFIED fundamentals cannot be used for BACKTEST_PIT")
        if isinstance(data, FundamentalRecord) and data.available_at is None:
            raise ValueError("BACKTEST_PIT record requires available_at")
    elif actual is FundamentalsAnalysisMode.BACKTEST_PIT:
        raise ValueError("BACKTEST_PIT fundamentals cannot be used as CURRENT_ANALYSIS data")
    elif actual is not FundamentalsAnalysisMode.CURRENT_ANALYSIS:
        raise ValueError("fundamentals object has no explicit analysis mode")


def _validate_finite_fields(instance: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(instance, name, None)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite numeric value or None")
        try:
            valid = isfinite(float(value))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"{name} must be finite or None")


@dataclass(frozen=True)
class FieldMetadata:
    """Provenance for one metric; period and as-of are intentionally separate."""

    source: str
    basis: str | None
    period: date | None
    as_of: datetime | None
    retrieved_at: datetime | None


@dataclass(frozen=True)
class DailyPriceBar:
    """One normalized daily OHLCV observation from a named source."""

    ticker: str
    session_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    source: str

    def __post_init__(self) -> None:
        _validate_finite_fields(self, ("open", "high", "low", "close", "volume"))


@dataclass(frozen=True)
class PriceHistory:
    """Daily observations and their honest coverage status."""

    ticker: str
    requested_start: date
    requested_end: date
    first_observation: date | None
    last_observation: date | None
    coverage: HistoryCoverage
    bars: tuple[DailyPriceBar, ...] = ()
    source: str | None = None


@dataclass(frozen=True)
class FundamentalRecord:
    """A financial record usable only on/after ``available_at``.

    ``report_period`` identifies what the statement measures, while
    ``filing_date``/``published_at`` and ``available_at`` determine when an
    historical analysis may use it.  Percentage inputs are normalized by a data
    adapter: 15% is represented as ``15.0`` in both percent fields.
    """

    ticker: str
    period: FinancialPeriod
    report_period: date
    filing_date: date | None
    published_at: datetime | None
    available_at: datetime | None
    source: str
    retrieved_at: datetime | None = None
    analysis_mode: FundamentalsAnalysisMode = FundamentalsAnalysisMode.UNSPECIFIED
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None
    free_cash_flow: float | None = None
    equity: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    total_assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    long_term_debt: float | None = None
    operating_cash_flow: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    diluted_average_shares: float | None = None
    shares_basis: str | None = None
    common_equity_issuance: float | None = None
    common_equity_issuance_basis: str | None = None
    common_equity_issuance_is_proxy: bool = False
    roe_percent: float | None = None
    pe: float | None = None
    pb: float | None = None
    dividend_per_share: float | None = None
    dividend_payout_percent: float | None = None
    pe_basis: str | None = None
    roe_basis: str | None = None
    field_sources: Mapping[str, str] = field(default_factory=dict)
    field_metadata: Mapping[str, FieldMetadata] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "analysis_mode", normalize_analysis_mode(self.analysis_mode))
        _validate_finite_fields(self, ("revenue", "net_income", "eps", "free_cash_flow", "equity", "total_debt", "cash", "total_assets", "current_assets", "current_liabilities", "long_term_debt", "operating_cash_flow", "gross_profit", "operating_income", "diluted_average_shares", "common_equity_issuance", "roe_percent", "pe", "pb", "dividend_per_share", "dividend_payout_percent"))

    def is_available_on(self, as_of: datetime) -> bool:
        """Whether a point-in-time analysis may use this record."""
        return self.available_at is not None and self.available_at <= as_of


@dataclass(frozen=True)
class FundamentalsHistory:
    """Annual and quarterly records supplied by a future fundamentals provider."""

    ticker: str
    as_of: datetime
    annual: tuple[FundamentalRecord, ...] = ()
    quarterly: tuple[FundamentalRecord, ...] = ()
    source: str | None = None
    retrieved_at: datetime | None = None
    analysis_mode: FundamentalsAnalysisMode = FundamentalsAnalysisMode.UNSPECIFIED
    warnings: tuple[str, ...] = ()
    raw_annual_periods: tuple[date, ...] = ()
    analysis_years: tuple[date, ...] = ()
    supporting_anchor_year: date | None = None
    valid_roe_years: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "analysis_mode", normalize_analysis_mode(self.analysis_mode))
        records = (*self.annual, *self.quarterly)
        for record in records:
            if record.ticker != self.ticker:
                raise ValueError("fundamentals history cannot mix tickers")
            if record.analysis_mode is not self.analysis_mode:
                raise ValueError("fundamentals history cannot mix analysis modes")
        if any(record.period is not FinancialPeriod.ANNUAL for record in self.annual):
            raise ValueError("annual history contains a non-annual record")
        if any(record.period is not FinancialPeriod.QUARTERLY for record in self.quarterly):
            raise ValueError("quarterly history contains a non-quarterly record")
        if self.analysis_mode is FundamentalsAnalysisMode.BACKTEST_PIT:
            for record in records:
                if record.available_at is None or record.available_at > self.as_of:
                    raise ValueError("BACKTEST_PIT history contains a record unavailable as of analysis time")


@dataclass(frozen=True)
class CurrentFundamentalsInput:
    """Structured current-source values supplied to a data adapter.

    The unit fields make vendor conversion explicit: no adapter guesses whether
    a numeric value is decimal or percent.
    """

    source: str
    retrieved_at: datetime
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None
    free_cash_flow: float | None = None
    equity: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    pe: float | None = None
    pb: float | None = None
    roe_value: float | None = None
    roe_unit: PercentageUnit | None = None
    dividend_payout_value: float | None = None
    dividend_payout_unit: PercentageUnit | None = None
    pe_basis: str | None = None
    pb_basis: str | None = None
    roe_basis: str | None = None

    def __post_init__(self) -> None:
        _validate_finite_fields(self, ("revenue", "net_income", "eps", "free_cash_flow", "equity", "total_debt", "cash", "pe", "pb", "roe_value", "dividend_payout_value"))


@dataclass(frozen=True)
class CurrentFundamentalsMetrics:
    """Normalized today-visible values; percent values use the internal standard."""

    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None
    free_cash_flow: float | None = None
    equity: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    pe: float | None = None
    pb: float | None = None
    roe_percent: float | None = None
    dividend_payout_percent: float | None = None
    pe_basis: str | None = None
    pb_basis: str | None = None
    roe_basis: str | None = None
    field_metadata: Mapping[str, FieldMetadata] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_finite_fields(self, ("revenue", "net_income", "eps", "free_cash_flow", "equity", "total_debt", "cash", "pe", "pb", "roe_percent", "dividend_payout_percent"))


@dataclass(frozen=True)
class FundamentalsResult:
    """Mode-labelled fundamentals result that cannot silently cross the PIT boundary."""

    ticker: str
    analysis_mode: FundamentalsAnalysisMode
    status: FundamentalsStatus
    source: str | None
    retrieved_at: datetime | None
    as_of: datetime
    current_metrics: CurrentFundamentalsMetrics | None = None
    current_history: FundamentalsHistory | None = None
    pit_history: FundamentalsHistory | None = None
    reason: str | None = None
    requested_as_of: datetime | None = None
    data_effective_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "analysis_mode", normalize_analysis_mode(self.analysis_mode))
        if self.requested_as_of is None:
            object.__setattr__(self, "requested_as_of", self.as_of)
        if self.data_effective_at is None and self.analysis_mode is FundamentalsAnalysisMode.CURRENT_ANALYSIS:
            object.__setattr__(self, "data_effective_at", self.retrieved_at)
        if self.analysis_mode is FundamentalsAnalysisMode.BACKTEST_PIT:
            if self.current_metrics is not None or self.current_history is not None:
                raise ValueError("BACKTEST_PIT result cannot contain current snapshot data")
            if self.pit_history is not None:
                validate_fundamentals_usage(self.pit_history, FundamentalsAnalysisMode.BACKTEST_PIT)
        elif self.analysis_mode is FundamentalsAnalysisMode.CURRENT_ANALYSIS:
            if self.pit_history is not None:
                raise ValueError("CURRENT_ANALYSIS result cannot contain PIT history")
            if self.current_history is not None:
                validate_fundamentals_usage(self.current_history, FundamentalsAnalysisMode.CURRENT_ANALYSIS)
            if self.status is FundamentalsStatus.AVAILABLE:
                if self.retrieved_at is None or self.data_effective_at is None:
                    raise ValueError("available current fundamentals require retrieval/effective timestamps")
                if self.as_of != self.data_effective_at:
                    raise ValueError("current result as_of must be the effective current-data timestamp")


def classify_history_coverage(first: date | None, last: date | None) -> HistoryCoverage:
    """Classify observed calendar coverage: >=5y full, >=3y limited, else insufficient."""
    if first is None or last is None or last < first:
        return HistoryCoverage.INSUFFICIENT_HISTORY
    years = (last - first).days / 365.2425
    if years >= 5:
        return HistoryCoverage.FULL_HISTORY
    if years >= 3:
        return HistoryCoverage.LIMITED_HISTORY
    return HistoryCoverage.INSUFFICIENT_HISTORY
