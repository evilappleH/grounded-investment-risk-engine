"""Future approved-data boundaries; no provider is connected in Step 2."""
"""Vendor-neutral data contracts; this package has no active data client."""

from .context import FundamentalsContext
from .models import (
    DEFAULT_FUNDAMENTALS_HISTORY_YEARS,
    DEFAULT_MARKET_HISTORY_YEARS,
    DailyPriceBar,
    FieldMetadata,
    FinancialPeriod,
    FundamentalsAnalysisMode,
    FundamentalRecord,
    FundamentalsHistory,
    FundamentalsResult,
    FundamentalsStatus,
    HistoryCoverage,
    PriceHistory,
    PercentageUnit,
    normalize_analysis_mode,
    validate_fundamentals_usage,
    CurrentFundamentalsInput,
    CurrentFundamentalsMetrics,
    classify_history_coverage,
)
from .providers import FundamentalsProvider, MarketDataProvider, UniverseProvider
from .universe import (
    InvalidTickerError,
    StaticUniverseProvider,
    StockUniverse,
    UniverseKind,
    UniverseConstituent,
    normalize_us_ticker,
)
from .tradingagents_adapter import TradingAgentsFundamentalsAdapter, TradingAgentsMarketDataAdapter
from .sec_provider import SECFirstFundamentalsProvider, SECProviderConfig, SECProviderError
from .roe import ROEAverageStatus, ROEHistorySummary, summarize_historical_roe

__all__ = [
    "DEFAULT_FUNDAMENTALS_HISTORY_YEARS",
    "DEFAULT_MARKET_HISTORY_YEARS",
    "DailyPriceBar",
    "FieldMetadata",
    "FinancialPeriod",
    "FundamentalsAnalysisMode",
    "FundamentalRecord",
    "FundamentalsContext",
    "FundamentalsHistory",
    "FundamentalsProvider",
    "FundamentalsResult",
    "FundamentalsStatus",
    "HistoryCoverage",
    "InvalidTickerError",
    "MarketDataProvider",
    "PriceHistory",
    "PercentageUnit",
    "normalize_analysis_mode",
    "validate_fundamentals_usage",
    "CurrentFundamentalsInput",
    "CurrentFundamentalsMetrics",
    "StaticUniverseProvider",
    "StockUniverse",
    "UniverseKind",
    "UniverseConstituent",
    "UniverseProvider",
    "TradingAgentsFundamentalsAdapter",
    "TradingAgentsMarketDataAdapter",
    "SECFirstFundamentalsProvider",
    "SECProviderConfig",
    "SECProviderError",
    "classify_history_coverage",
    "normalize_us_ticker",
    "ROEAverageStatus",
    "ROEHistorySummary",
    "summarize_historical_roe",
]
