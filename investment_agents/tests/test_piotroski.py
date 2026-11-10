"""Offline tests for strict nine-signal Piotroski diagnostics."""

from datetime import date
import unittest

from investment_agents.data import FinancialPeriod, FundamentalRecord
from investment_agents.financial import calculate_piotroski_score, calculate_piotroski_score_legacy_beginning_assets


class PiotroskiTests(unittest.TestCase):
    def record(self, year, **overrides):
        values = dict(
            ticker="TEST", period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            net_income=20, operating_cash_flow=25, total_assets=100,
            long_term_debt=30, current_assets=80, current_liabilities=40,
            revenue=100, gross_profit=40, common_equity_issuance=0,
            common_equity_issuance_basis="Common Stock Issuance",
            analysis_mode="CURRENT_ANALYSIS",
        )
        values.update(overrides)
        return FundamentalRecord(**values)

    def test_complete_score_and_methodology(self):
        result = calculate_piotroski_score([self.record(2025), self.record(2024), self.record(2023)])
        self.assertEqual(result.status, "COMPLETE")
        self.assertEqual(result.score, 4)  # unchanged ratios are correctly scored zero
        self.assertIsNone(result.partial_score)
        self.assertEqual(len(result.components), 9)
        self.assertEqual(result.methodology_reference["doi"], "10.2307/2672906")

    def test_roa_cfo_delta_and_accrual(self):
        records = [self.record(2025, net_income=30, operating_cash_flow=35, total_assets=120), self.record(2024, net_income=20, operating_cash_flow=20, total_assets=100), self.record(2023, net_income=10, total_assets=100)]
        result = calculate_piotroski_score(records)
        by_name = {c.name: c for c in result.components}
        self.assertEqual(by_name["F_ROA"].point, 1)
        self.assertEqual(by_name["F_CFO"].point, 1)
        self.assertEqual(by_name["F_DELTA_ROA"].point, 1)
        self.assertEqual(by_name["F_ACCRUAL"].point, 1)

    def test_leverage_liquidity_margin_turnover_improvements(self):
        result = calculate_piotroski_score([
            self.record(2025, total_assets=120, long_term_debt=20, current_assets=100, current_liabilities=30, revenue=130, gross_profit=60),
            self.record(2024, total_assets=100, long_term_debt=40, current_assets=80, current_liabilities=40, revenue=100, gross_profit=40),
            self.record(2023, total_assets=100, long_term_debt=40, revenue=100, gross_profit=40),
        ])
        by_name = {c.name: c for c in result.components}
        self.assertEqual(by_name["F_DELTA_LEVER"].point, 1)
        self.assertEqual(by_name["F_DELTA_LIQUID"].point, 1)
        self.assertEqual(by_name["F_DELTA_MARGIN"].point, 1)
        self.assertEqual(by_name["F_DELTA_TURN"].point, 1)

    def test_formal_turnover_uses_average_assets_and_legacy_is_separate(self):
        records = [
            self.record(2025, revenue=100, total_assets=1000),
            self.record(2024, revenue=100, total_assets=100),
            self.record(2023, revenue=100, total_assets=200),
        ]
        result = calculate_piotroski_score(records)
        turn = next(component for component in result.components if component.name == "F_DELTA_TURN")
        self.assertAlmostEqual(turn.value, (100 / 550) - (100 / 150))
        self.assertEqual(turn.point, 0)
        self.assertIn("average total assets", turn.basis)
        self.assertTrue(result.official_source_aligned)
        legacy = calculate_piotroski_score_legacy_beginning_assets(records)
        legacy_turn = next(component for component in legacy.components if component.name == "F_DELTA_TURN")
        self.assertAlmostEqual(legacy_turn.value, 0.5)
        self.assertIsNone(legacy.score)
        self.assertEqual(legacy.status, "LEGACY_VARIANT_COMPLETE")
        self.assertFalse(legacy.official_source_aligned)

    def test_delta_turn_requires_two_back_assets(self):
        result = calculate_piotroski_score([
            self.record(2025), self.record(2024, total_assets=None), self.record(2023),
        ])
        turn = next(component for component in result.components if component.name == "F_DELTA_TURN")
        self.assertIsNone(turn.value)
        self.assertIsNone(turn.point)

    def test_eqoffer_strict_issuance_no_and_yes(self):
        no_issue = calculate_piotroski_score([self.record(2025, common_equity_issuance=0), self.record(2024), self.record(2023)])
        yes_issue = calculate_piotroski_score([self.record(2025, common_equity_issuance=10), self.record(2024), self.record(2023)])
        missing = calculate_piotroski_score([self.record(2025, common_equity_issuance=None), self.record(2024), self.record(2023)])
        self.assertEqual(next(c for c in no_issue.components if c.name == "F_EQOFFER").point, 1)
        self.assertEqual(next(c for c in yes_issue.components if c.name == "F_EQOFFER").point, 0)
        self.assertIsNone(next(c for c in missing.components if c.name == "F_EQOFFER").point)
        generic = calculate_piotroski_score([
            self.record(2025, common_equity_issuance=0, common_equity_issuance_basis="Issuance Of Capital Stock"),
            self.record(2024), self.record(2023),
        ])
        self.assertEqual(generic.status, "PARTIAL")
        self.assertIsNone(next(c for c in generic.components if c.name == "F_EQOFFER").point)

    def test_missing_history_is_partial_not_complete(self):
        result = calculate_piotroski_score([self.record(2025, common_equity_issuance=None), self.record(2024)])
        self.assertEqual(result.status, "PARTIAL")
        self.assertIsNone(result.score)
        self.assertIsNotNone(result.partial_score)
        self.assertLess(result.available_component_count, 9)

    def test_no_substitutions_or_directional_output(self):
        result = calculate_piotroski_score([self.record(2025, operating_cash_flow=None, long_term_debt=None), self.record(2024), self.record(2023)])
        names = {c.name for c in result.components}
        self.assertIn("F_CFO", names)
        self.assertIsNone(next(c for c in result.components if c.name == "F_CFO").point)
        self.assertIsNone(next(c for c in result.components if c.name == "F_DELTA_LEVER").point)
        text = repr(result)
        self.assertNotIn("BUY", text)
        self.assertNotIn("SELL", text)
        self.assertNotIn("REJECT", text)

    def test_nan_inf_rejected_and_zero_denominators_unavailable(self):
        with self.assertRaises(ValueError):
            self.record(2025, total_assets=float("inf"))
        result = calculate_piotroski_score([self.record(2025), self.record(2024, total_assets=0), self.record(2023)])
        self.assertIsNone(next(c for c in result.components if c.name == "F_ROA").point)


if __name__ == "__main__":
    unittest.main()
