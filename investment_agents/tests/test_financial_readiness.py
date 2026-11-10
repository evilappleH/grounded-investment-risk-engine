"""Offline tests for raw financial-field mapping and readiness only."""

from datetime import date
import unittest

from investment_agents.data import FinancialPeriod, FundamentalRecord
from investment_agents.financial import ReadinessStatus, assess_piotroski_readiness


class FinancialReadinessTests(unittest.TestCase):
    def record(self, **values):
        base = dict(ticker="T", period=FinancialPeriod.ANNUAL, report_period=date(2025, 12, 31),
                    filing_date=None, published_at=None, available_at=None, source="fixture")
        base.update(values)
        return FundamentalRecord(**base)

    def test_complete_partial_insufficient(self):
        fields = dict(net_income=1, operating_cash_flow=1, total_assets=1, equity=1,
                      total_debt=1, long_term_debt=1, current_assets=1,
                      current_liabilities=1, diluted_average_shares=1, revenue=1, gross_profit=1,
                      common_equity_issuance=0)
        fields["common_equity_issuance_basis"] = "Common Stock Issuance"
        self.assertEqual(assess_piotroski_readiness([self.record(**fields)] * 3).status, ReadinessStatus.PIOTROSKI_READY)
        partial = assess_piotroski_readiness([self.record(net_income=1, operating_cash_flow=1)])
        self.assertEqual(partial.status, ReadinessStatus.PIOTROSKI_INSUFFICIENT)
        self.assertIn("total_assets", partial.missing_fields)
        self.assertEqual(assess_piotroski_readiness([]).status, ReadinessStatus.PIOTROSKI_INSUFFICIENT)

    def test_readiness_has_no_score_or_direction(self):
        result = assess_piotroski_readiness([self.record(net_income=1)])
        self.assertFalse(any(word in result.notes for word in ("BUY", "SELL", "REJECT", "score")))

    def test_substitutes_are_not_silent(self):
        result = assess_piotroski_readiness([self.record(net_income=1, free_cash_flow=1, total_debt=1)])
        self.assertIn("operating_cash_flow", result.missing_fields)
        self.assertIn("long_term_debt", result.missing_fields)

    def test_missing_values_remain_missing(self):
        result = assess_piotroski_readiness([self.record(operating_cash_flow=None, gross_profit=None)])
        self.assertIn("operating_cash_flow", result.missing_fields)
        self.assertIn("gross_profit", result.missing_fields)


if __name__ == "__main__":
    unittest.main()
