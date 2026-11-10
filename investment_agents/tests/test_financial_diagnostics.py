"""Offline tests for fact-only financial diagnostics."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from investment_agents.data import FinancialPeriod, FundamentalRecord
from investment_agents.financial import DataQuality, diagnose_financials
from investment_agents.valuation import calculate_pr_metrics


class FinancialDiagnosticsTests(unittest.TestCase):
    def record(self, **overrides):
        values = dict(
            ticker="TEST", period=FinancialPeriod.ANNUAL, report_period=date(2025, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), revenue=100,
            net_income=20, free_cash_flow=15, equity=50, total_debt=30, cash=10,
        )
        values.update(overrides)
        return FundamentalRecord(**values)

    def test_positive_data_derives_expected_metrics(self):
        result = diagnose_financials(self.record())
        self.assertEqual(result.data_quality, DataQuality.COMPLETE)
        self.assertAlmostEqual(result.metrics["fcf_margin"], 0.15)
        self.assertAlmostEqual(result.metrics["fcf_to_net_income"], 0.75)
        self.assertAlmostEqual(result.metrics["net_debt"], 20)
        self.assertAlmostEqual(result.metrics["debt_to_equity"], 0.6)
        self.assertAlmostEqual(result.metrics["cash_to_debt"], 1 / 3)

    def test_fact_warnings_for_negative_values(self):
        result = diagnose_financials(self.record(net_income=-1, free_cash_flow=-2, equity=-3))
        self.assertIn("NEGATIVE_NET_INCOME", result.warnings)
        self.assertIn("NEGATIVE_FREE_CASH_FLOW", result.warnings)
        self.assertIn("NEGATIVE_EQUITY", result.warnings)

    def test_zero_equity_does_not_divide(self):
        result = diagnose_financials(self.record(equity=0))
        self.assertIn("ZERO_EQUITY", result.warnings)
        self.assertIsNone(result.metrics["debt_to_equity"])

    def test_missing_values_are_not_negative_or_zero(self):
        result = diagnose_financials(self.record(equity=None, free_cash_flow=None))
        self.assertEqual(result.data_quality, DataQuality.PARTIAL)
        self.assertIn("MISSING_EQUITY", result.warnings)
        self.assertIn("MISSING_FCF", result.warnings)
        self.assertNotIn("NEGATIVE_EQUITY", result.warnings)
        self.assertIsNone(result.metrics["free_cash_flow"])
        self.assertIsNone(result.metrics["fcf_margin"])

    def test_missing_all_core_data_is_insufficient(self):
        result = diagnose_financials(self.record(
            revenue=None, net_income=None, free_cash_flow=None, equity=None, total_debt=None, cash=None
        ))
        self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)

    def test_net_debt_and_positive_fact_warning(self):
        result = diagnose_financials(self.record(total_debt=40, cash=10))
        self.assertEqual(result.metrics["net_debt"], 30)
        self.assertIn("NET_DEBT_POSITIVE", result.warnings)

    def test_fcf_below_net_income_is_context_only(self):
        result = diagnose_financials(self.record(free_cash_flow=5, net_income=10))
        self.assertIn("FCF_BELOW_NET_INCOME", result.warnings)
        self.assertFalse(any(item in result.warnings for item in ("BUY", "SELL", "REJECT")))

    def test_zero_denominators_are_not_divided(self):
        result = diagnose_financials(self.record(revenue=0, net_income=0, total_debt=0))
        self.assertIsNone(result.metrics["fcf_margin"])
        self.assertIsNone(result.metrics["fcf_to_net_income"])
        self.assertIsNone(result.metrics["cash_to_debt"])

    def test_pr_is_context_and_unchanged(self):
        pr = calculate_pr_metrics(
            ticker="TEST", pe=15, roe_percent=15, roe_mode="ttm", pe_basis="trailing",
            data_as_of="2026-01-01", pb=2.25, dividend_payout_percent=50,
        )
        result = diagnose_financials(self.record(equity=-10), pr_metrics=pr)
        self.assertIs(result.pr_metrics, pr)
        self.assertEqual(result.pr_metrics.pr, 1.0)
        self.assertIn("NEGATIVE_EQUITY", result.warnings)
        self.assertNotIn("BUY", result.warnings)
        self.assertNotIn("SELL", result.warnings)

    def test_provenance_is_preserved(self):
        result = diagnose_financials(self.record(field_sources={"equity": "fixture:balance"}))
        self.assertEqual(result.source, "fixture")
        self.assertEqual(result.report_period, date(2025, 12, 31))
        self.assertEqual(result.retrieved_at, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.source_metadata["field_sources"]["equity"], "fixture:balance")


if __name__ == "__main__":
    unittest.main()
