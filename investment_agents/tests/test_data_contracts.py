"""Offline contract tests for Step 4A data and universe boundaries."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

import pandas as pd

from investment_agents.data import (
    FinancialPeriod,
    FundamentalsAnalysisMode,
    FundamentalRecord,
    FundamentalsHistory,
    FundamentalsProvider,
    FundamentalsStatus,
    HistoryCoverage,
    InvalidTickerError,
    MarketDataProvider,
    PriceHistory,
    PercentageUnit,
    CurrentFundamentalsInput,
    StaticUniverseProvider,
    UniverseKind,
    UniverseProvider,
    classify_history_coverage,
    normalize_analysis_mode,
    validate_fundamentals_usage,
)
from investment_agents.data.tradingagents_adapter import TradingAgentsFundamentalsAdapter, TradingAgentsMarketDataAdapter
from investment_agents.valuation import calculate_pr_metrics


class OfflineFixtureProvider:
    """Test double proving the provider contracts need no network dependency."""

    def get_daily_prices(self, ticker: str, start: date, end: date) -> PriceHistory:
        return PriceHistory(
            ticker=ticker,
            requested_start=start,
            requested_end=end,
            first_observation=None,
            last_observation=None,
            coverage=HistoryCoverage.INSUFFICIENT_HISTORY,
            source="fixture",
        )

    def get_fundamentals(self, ticker: str, as_of: datetime) -> FundamentalsHistory:
        return FundamentalsHistory(ticker=ticker, as_of=as_of, source="fixture")


class DataContractTests(unittest.TestCase):
    def test_static_universe_returns_normalized_tickers(self) -> None:
        provider = StaticUniverseProvider([" aapl ", "msft"], source="fixture")
        universe = provider.get_universe()
        self.assertEqual(universe.kind, UniverseKind.CUSTOM)
        self.assertEqual(universe.tickers, ("AAPL", "MSFT"))
        self.assertIsInstance(provider, UniverseProvider)

    def test_static_universe_removes_normalized_duplicates(self) -> None:
        universe = StaticUniverseProvider(["aapl", " AAPL ", "MSFT", "msft"]).get_universe()
        self.assertEqual(universe.tickers, ("AAPL", "MSFT"))

    def test_invalid_ticker_is_explicit(self) -> None:
        with self.assertRaisesRegex(InvalidTickerError, "invalid US-equities ticker"):
            StaticUniverseProvider(["AAPL/../../bad"])

    def test_history_coverage_classification(self) -> None:
        self.assertEqual(
            classify_history_coverage(date(2020, 1, 1), date(2025, 1, 1)),
            HistoryCoverage.FULL_HISTORY,
        )
        self.assertEqual(
            classify_history_coverage(date(2021, 1, 1), date(2025, 1, 1)),
            HistoryCoverage.LIMITED_HISTORY,
        )
        self.assertEqual(
            classify_history_coverage(date(2023, 1, 1), date(2025, 1, 1)),
            HistoryCoverage.INSUFFICIENT_HISTORY,
        )

    def test_fundamental_record_preserves_pit_fields_and_percent_units(self) -> None:
        available_at = datetime(2025, 2, 20, tzinfo=timezone.utc)
        record = FundamentalRecord(
            ticker="TEST",
            period=FinancialPeriod.ANNUAL,
            report_period=date(2024, 12, 31),
            filing_date=date(2025, 2, 20),
            published_at=available_at,
            available_at=available_at,
            source="fixture",
            roe_percent=15.0,
            dividend_payout_percent=25.0,
        )
        self.assertEqual(record.report_period, date(2024, 12, 31))
        self.assertEqual(record.filing_date, date(2025, 2, 20))
        self.assertEqual(record.published_at, available_at)
        self.assertEqual(record.available_at, available_at)
        self.assertEqual(record.source, "fixture")
        self.assertEqual(record.roe_percent, 15.0)
        self.assertEqual(record.dividend_payout_percent, 25.0)
        self.assertFalse(record.is_available_on(datetime(2024, 12, 31, tzinfo=timezone.utc)))
        self.assertTrue(record.is_available_on(available_at))

    def test_provider_contracts_and_adapter_skeleton_are_network_free(self) -> None:
        fixture = OfflineFixtureProvider()
        self.assertIsInstance(fixture, MarketDataProvider)
        self.assertIsInstance(fixture, FundamentalsProvider)
        adapter = TradingAgentsFundamentalsAdapter(
            current_loader=lambda _ticker: CurrentFundamentalsInput(
                source="fixture", retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc)
            )
        )
        result = adapter.get_fundamentals(
            "AAPL", datetime(2025, 1, 1, tzinfo=timezone.utc), FundamentalsAnalysisMode.CURRENT_ANALYSIS
        )
        self.assertEqual(result.status, FundamentalsStatus.AVAILABLE)

    def test_analysis_mode_inputs_are_normalized_and_invalid_fails_closed(self) -> None:
        adapter = TradingAgentsFundamentalsAdapter(current_loader=lambda _ticker: CurrentFundamentalsInput(
            source="fixture", retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ))
        as_of = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(adapter.get_fundamentals("AAPL", as_of, FundamentalsAnalysisMode.BACKTEST_PIT).status,
                         FundamentalsStatus.PIT_DATA_UNAVAILABLE)
        self.assertEqual(adapter.get_fundamentals("AAPL", as_of, "BACKTEST_PIT").status,
                         FundamentalsStatus.PIT_DATA_UNAVAILABLE)
        self.assertEqual(adapter.get_fundamentals("AAPL", as_of, "CURRENT_ANALYSIS").status,
                         FundamentalsStatus.AVAILABLE)
        with self.assertRaises(ValueError):
            adapter.get_fundamentals("AAPL", as_of, "not-a-mode")
        with self.assertRaises(ValueError):
            adapter.get_fundamentals("AAPL", as_of, FundamentalsAnalysisMode.UNSPECIFIED)

    def test_current_snapshot_cannot_be_used_as_pit_and_default_is_unspecified(self) -> None:
        history = FundamentalsHistory(ticker="AAPL", as_of=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(history.analysis_mode, FundamentalsAnalysisMode.UNSPECIFIED)
        with self.assertRaises(ValueError):
            validate_fundamentals_usage(history, FundamentalsAnalysisMode.BACKTEST_PIT)
        current_record = FundamentalRecord(
            ticker="AAPL", period=FinancialPeriod.ANNUAL, report_period=date(2024, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
        )
        with self.assertRaises(ValueError):
            validate_fundamentals_usage(current_record, "BACKTEST_PIT")

    def test_non_finite_data_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FundamentalRecord(
                ticker="AAPL", period=FinancialPeriod.ANNUAL, report_period=date(2024, 12, 31),
                filing_date=None, published_at=None, available_at=None, source="fixture", equity=float("inf"),
            )
        with self.assertRaises(ValueError):
            CurrentFundamentalsInput(source="fixture", retrieved_at=datetime.now(timezone.utc), pe=float("nan"))
        bar = TradingAgentsMarketDataAdapter(lambda _ticker, _end: pd.DataFrame({
            "Date": ["2025-01-02"], "Open": [float("inf")], "High": [1], "Low": [1], "Close": [1], "Volume": [1]
        })).get_daily_prices("AAPL", date(2025, 1, 1), date(2025, 1, 3)).bars[0]
        self.assertIsNone(bar.open)

    def test_percentage_unit_string_must_be_explicitly_valid(self) -> None:
        adapter = TradingAgentsFundamentalsAdapter(current_loader=lambda _ticker: CurrentFundamentalsInput(
            source="fixture", retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc), roe_value=0.15, roe_unit="bad"
        ))
        with self.assertRaises(ValueError):
            adapter.get_fundamentals("AAPL", datetime(2025, 1, 1, tzinfo=timezone.utc), "CURRENT_ANALYSIS")

    def test_tradingagents_ohlcv_mapping_sorts_and_excludes_future_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "Date": ["2025-01-03", "2025-01-02", "2025-01-04"],
                "Open": [103, 102, 104], "High": [104, 103, 105],
                "Low": [102, 101, 103], "Close": [103.5, 102.5, 104.5],
                "Volume": [300, 200, 400],
            }
        )
        adapter = TradingAgentsMarketDataAdapter(lambda _ticker, _end: frame)
        history = adapter.get_daily_prices("aapl", date(2025, 1, 1), date(2025, 1, 3))
        self.assertEqual([bar.session_date for bar in history.bars], [date(2025, 1, 2), date(2025, 1, 3)])
        self.assertEqual(history.bars[0].ticker, "AAPL")
        self.assertEqual(history.bars[0].close, 102.5)
        self.assertEqual(history.bars[0].source, "tradingagents:yfinance")
        self.assertEqual(history.last_observation, date(2025, 1, 3))

    def test_tradingagents_ohlcv_duplicate_dates_are_rejected(self) -> None:
        frame = pd.DataFrame(
            {"Date": ["2025-01-02", "2025-01-02"], "Open": [1, 1], "High": [1, 1],
             "Low": [1, 1], "Close": [1, 1], "Volume": [1, 1]}
        )
        with self.assertRaisesRegex(ValueError, "duplicate session dates"):
            TradingAgentsMarketDataAdapter(lambda _ticker, _end: frame).get_daily_prices(
                "AAPL", date(2025, 1, 1), date(2025, 1, 3)
            )

    def test_fundamentals_modes_are_isolated_and_adapter_converts_decimal_units(self) -> None:
        retrieved = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
        raw = CurrentFundamentalsInput(
            source="fixture", retrieved_at=retrieved, pe=15, pb=2.25,
            roe_value=0.15, roe_unit=PercentageUnit.DECIMAL,
            dividend_payout_value=0.25, dividend_payout_unit=PercentageUnit.DECIMAL,
        )
        adapter = TradingAgentsFundamentalsAdapter(lambda _ticker: raw)
        current = adapter.get_fundamentals(
            "aapl", datetime(2026, 9, 8, tzinfo=timezone.utc), FundamentalsAnalysisMode.CURRENT_ANALYSIS
        )
        self.assertEqual(current.status, FundamentalsStatus.AVAILABLE)
        self.assertEqual(current.analysis_mode, FundamentalsAnalysisMode.CURRENT_ANALYSIS)
        self.assertEqual(current.retrieved_at, retrieved)
        self.assertEqual(current.current_metrics.roe_percent, 15.0)
        self.assertEqual(current.current_metrics.dividend_payout_percent, 25.0)

        pit = adapter.get_fundamentals(
            "AAPL", datetime(2020, 1, 1, tzinfo=timezone.utc), FundamentalsAnalysisMode.BACKTEST_PIT
        )
        self.assertEqual(pit.status, FundamentalsStatus.PIT_DATA_UNAVAILABLE)
        self.assertIsNone(pit.current_metrics)
        self.assertIn("never a fallback", pit.reason)

    def test_native_structured_mapping_preserves_record_metadata_and_missing_values(self) -> None:
        class FakeTicker:
            info = {
                "trailingPE": 15.0, "priceToBook": 2.25, "returnOnEquity": 0.15,
                "payoutRatio": 0.25, "trailingEps": 6.0,
            }
            financials = pd.DataFrame(
                {pd.Timestamp("2025-09-30"): [1000.0, 200.0, 6.0]},
                index=["Total Revenue", "Net Income", "Diluted EPS"],
            )
            cashflow = pd.DataFrame(
                {pd.Timestamp("2025-09-30"): [150.0]}, index=["Free Cash Flow"]
            )
            balance_sheet = pd.DataFrame(
                {pd.Timestamp("2025-09-30"): [500.0, 100.0, 50.0]},
                index=["Stockholders Equity", "Total Debt", "Cash And Cash Equivalents"],
            )

        adapter = TradingAgentsFundamentalsAdapter(ticker_factory=lambda _ticker: FakeTicker())
        requested = datetime.now(timezone.utc)
        result = adapter.get_fundamentals(
            "AAPL", requested, FundamentalsAnalysisMode.CURRENT_ANALYSIS
        )
        self.assertEqual(result.status, FundamentalsStatus.AVAILABLE)
        self.assertEqual(result.requested_as_of, requested)
        self.assertEqual(result.as_of, result.data_effective_at)
        self.assertEqual(result.current_metrics.roe_percent, 15.0)
        self.assertEqual(result.current_metrics.dividend_payout_percent, 25.0)
        self.assertEqual(result.current_metrics.pe_basis, "trailing")
        self.assertEqual(result.current_metrics.roe_basis, "vendor_defined")
        record = result.current_history.annual[0]
        self.assertEqual(record.report_period, date(2025, 9, 30))
        self.assertEqual(record.analysis_mode, FundamentalsAnalysisMode.CURRENT_ANALYSIS)
        self.assertIsNone(record.filing_date)
        self.assertIsNone(record.published_at)
        self.assertIsNone(record.available_at)
        self.assertFalse(record.is_available_on(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        self.assertEqual(record.cash, 50.0)
        self.assertIsNone(record.pe_basis)
        self.assertIsNone(record.pe)
        self.assertIsNone(record.pb)
        self.assertIsNone(record.roe_percent)
        self.assertIsNone(record.roe_basis)
        self.assertEqual(record.field_sources["roe_percent"], "calculated:average_equity")
        pr = calculate_pr_metrics(
            ticker="AAPL", pe=result.current_metrics.pe, roe_percent=result.current_metrics.roe_percent,
            roe_mode="ttm", pb=result.current_metrics.pb,
            dividend_payout_percent=result.current_metrics.dividend_payout_percent,
            data_as_of="2026-01-01", pe_basis=result.current_metrics.pe_basis,
        )
        self.assertAlmostEqual(pr.pr, 1.0)


if __name__ == "__main__":
    unittest.main()
