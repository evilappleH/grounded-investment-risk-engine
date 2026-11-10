"""Offline tests for annual CURRENT_ANALYSIS history and explicit ROE windows."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

import pandas as pd

from investment_agents.data import (
    FinancialPeriod, FundamentalRecord, ROEAverageStatus,
    TradingAgentsFundamentalsAdapter, summarize_historical_roe,
)
from investment_agents.valuation import calculate_pr_cycle, PRCycleStatus


class HistoricalROETests(unittest.TestCase):
    def record(self, year: int, roe: float | None, equity: float | None = 100):
        return FundamentalRecord(
            ticker="TEST", period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            roe_percent=roe, equity=equity, analysis_mode="CURRENT_ANALYSIS",
        )

    def test_summary_requires_complete_windows_and_ignores_missing(self):
        summary = summarize_historical_roe([self.record(2025, 10), self.record(2024, 20), self.record(2023, 30)])
        self.assertEqual(summary.roe_avg_3y_percent, 20)
        self.assertIsNone(summary.roe_avg_5y_percent)
        self.assertEqual(summary.status_3y, ROEAverageStatus.AVAILABLE)
        self.assertEqual(summary.status_5y, ROEAverageStatus.ROE_AVG_5Y_UNAVAILABLE)
        summary5 = summarize_historical_roe([self.record(2025, 10), self.record(2024, 20), self.record(2023, 30), self.record(2022, 40), self.record(2021, 50)])
        self.assertEqual(summary5.roe_avg_5y_percent, 30)
        self.assertEqual(summary5.valid_year_count, 5)

    def test_missing_roe_does_not_enter_average(self):
        summary = summarize_historical_roe([self.record(2025, 10), self.record(2024, None), self.record(2023, 30)])
        self.assertEqual(summary.valid_year_count, 2)
        self.assertIsNone(summary.roe_avg_3y_percent)

    def test_non_finite_roe_cannot_enter_average(self):
        with self.assertRaises(ValueError):
            self.record(2025, float("nan"))

    def test_adapter_builds_sorted_records_and_historical_roe(self):
        periods = [pd.Timestamp(f"{year}-09-30") for year in range(2025, 2019, -1)]
        income = pd.DataFrame({p: [100.0 + (2025 - p.year), 20.0, 2.0, 60.0, 25.0, 10.0] for p in periods}, index=["Total Revenue", "Net Income", "Diluted EPS", "Gross Profit", "Operating Income", "Diluted Average Shares"])
        cashflow = pd.DataFrame({p: [10.0, 12.0] for p in periods}, index=["Free Cash Flow", "Operating Cash Flow"])
        balance = pd.DataFrame({p: [100.0 + (2025 - p.year), 30.0, 20.0, 300.0, 150.0, 80.0, 50.0] for p in periods}, index=["Stockholders Equity", "Total Debt", "Cash And Cash Equivalents", "Total Assets", "Current Assets", "Current Liabilities", "Long Term Debt"])
        class FakeTicker:
            info = {"trailingPE": 10.0, "priceToBook": 2.0, "returnOnEquity": 0.9, "payoutRatio": 0.2}
        FakeTicker.financials = income
        FakeTicker.cashflow = cashflow
        FakeTicker.balance_sheet = balance
        requested = datetime.now(timezone.utc)
        result = TradingAgentsFundamentalsAdapter(ticker_factory=lambda _ticker: FakeTicker()).get_fundamentals(
            "TEST", requested, "CURRENT_ANALYSIS"
        )
        records = result.current_history.annual
        self.assertEqual(len(records), 5)
        self.assertEqual([r.report_period for r in records], [date(2025, 9, 30), date(2024, 9, 30), date(2023, 9, 30), date(2022, 9, 30), date(2021, 9, 30)])
        self.assertIsNone(records[0].pe)
        self.assertIsNone(records[0].pb)
        self.assertAlmostEqual(records[0].roe_percent, 20.0 / 100.5 * 100.0)
        self.assertEqual(records[0].field_metadata["roe_percent"].basis, "net_income/average_equity")
        self.assertEqual(result.current_history.raw_annual_periods.__len__(), 6)
        self.assertEqual(len(result.current_history.analysis_years), 5)
        self.assertEqual(result.current_history.supporting_anchor_year, date(2020, 9, 30))
        self.assertEqual(result.current_history.valid_roe_years, 5)
        for field in ("total_assets", "current_assets", "current_liabilities", "long_term_debt", "operating_cash_flow", "gross_profit", "operating_income", "diluted_average_shares"):
            self.assertIsNotNone(records[0].__getattribute__(field))
        self.assertEqual(records[0].shares_basis, "diluted_average_shares")

    def test_five_analysis_roes_require_six_equity_periods(self):
        records = [self.record(year, 10.0) for year in range(2025, 2020, -1)]
        summary = summarize_historical_roe(records)
        self.assertEqual(summary.valid_year_count, 5)
        self.assertEqual(summary.roe_avg_5y_percent, 10.0)
        only_five = summarize_historical_roe(records[:-1])
        self.assertIsNone(only_five.roe_avg_5y_percent)

    def test_pr_cycle_requires_explicit_window_and_trailing_pe(self):
        result = calculate_pr_cycle(pe=15, roe_avg_percent=15, roe_period="3y_average", pe_basis="trailing", pb=2.25, pb_basis="current_snapshot")
        self.assertEqual(result.status, PRCycleStatus.AVAILABLE)
        self.assertEqual(result.roe_period.value, "3y_average")
        self.assertEqual(result.pr_cycle, 1.0)
        self.assertEqual(result.pr_cycle_pb, 1.0)
        self.assertEqual(result.pr_cycle_pe_reference, 1.0)
        unavailable = calculate_pr_cycle(pe=15, roe_avg_percent=None, roe_period="5y_average", pe_basis="trailing", pb=2.25, pb_basis="current_snapshot")
        self.assertEqual(unavailable.status, PRCycleStatus.NOT_COMPUTED)
        self.assertIn("roe_average_unavailable", unavailable.warnings)
        non_ttm = calculate_pr_cycle(pe=15, roe_avg_percent=15, roe_period="3y_average", pe_basis="forward", pb=2.25, pb_basis="current_snapshot")
        self.assertEqual(non_ttm.status, PRCycleStatus.AVAILABLE)
        self.assertIsNone(non_ttm.pr_cycle_pe_reference)
        self.assertIn("pr_cycle_pe_reference_unavailable", non_ttm.warnings)

    def test_formal_cycle_requires_current_pb_and_complete_window(self):
        missing_pb = calculate_pr_cycle(pe=15, roe_avg_percent=15, roe_period="3y_average", pe_basis="trailing", pb=None, pb_basis="current_snapshot")
        self.assertEqual(missing_pb.status, PRCycleStatus.NOT_COMPUTED)
        self.assertIn("pb_missing_or_non_positive", missing_pb.warnings)
        missing_basis = calculate_pr_cycle(pe=15, roe_avg_percent=15, roe_period="3y_average", pe_basis="trailing", pb=2.25, pb_basis="annual")
        self.assertEqual(missing_basis.status, PRCycleStatus.NOT_COMPUTED)
        self.assertIn("pb_basis_not_current_snapshot", missing_basis.warnings)

    def test_current_history_has_no_pit_safe_timestamps(self):
        record = self.record(2025, 10)
        self.assertIsNone(record.available_at)
        self.assertEqual(record.analysis_mode.value, "CURRENT_ANALYSIS")


if __name__ == "__main__":
    unittest.main()
