"""Offline tests for the Step 5 qualification boundary."""

from datetime import date
import unittest

from investment_agents.data import FinancialPeriod, FundamentalRecord
from investment_agents.financial import diagnose_financials, calculate_piotroski_score
from investment_agents.qualification import QualificationStatus, no_qualified_candidate, qualify_candidate
from investment_agents.valuation import calculate_pr_metrics


class QualificationTests(unittest.TestCase):
    def record(self, year=2025, **overrides):
        values = dict(ticker="TEST", period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
                      filing_date=None, published_at=None, available_at=None, source="fixture",
                      revenue=100, net_income=20, free_cash_flow=15, equity=50, total_debt=30, cash=10,
                      total_assets=100, current_assets=80, current_liabilities=40, long_term_debt=20,
                      operating_cash_flow=25, gross_profit=40, operating_income=20,
                      diluted_average_shares=10, common_equity_issuance=0,
                      common_equity_issuance_basis="Common Stock Issuance",
                      analysis_mode="CURRENT_ANALYSIS")
        values.update(overrides)
        return FundamentalRecord(**values)

    def contexts(self, **record_overrides):
        current = self.record(2025, **record_overrides)
        history = [current, self.record(2024), self.record(2023)]
        diagnostics = diagnose_financials(current, as_of="2026-01-01")
        pr = calculate_pr_metrics(ticker="TEST", pe=15, roe_percent=15, roe_mode="ttm", pe_basis="trailing", pb=2.25, data_as_of="2026-01-01", dividend_payout_percent=50)
        piotroski = calculate_piotroski_score(history)
        return current, diagnostics, pr, piotroski

    def test_missing_inputs_are_insufficient(self):
        result = qualify_candidate(self.record(), as_of="2026-01-01")
        self.assertEqual(result.status, QualificationStatus.INSUFFICIENT_DATA)
        self.assertTrue(result.missing_requirements)

    def test_missing_fcf_is_not_negative_fact(self):
        current = self.record(free_cash_flow=None)
        diag = diagnose_financials(current)
        self.assertIn("MISSING_FCF", diag.warnings)
        self.assertNotIn("NEGATIVE_FREE_CASH_FLOW", diag.warnings)

    def test_complete_context_can_qualify_with_warnings(self):
        current, diag, pr, pi = self.contexts()
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertIn(result.status, (QualificationStatus.QUALIFIED, QualificationStatus.QUALIFIED_WITH_WARNINGS))
        self.assertIn("NET_DEBT_POSITIVE", result.warnings)

    def test_partial_piotroski_does_not_use_threshold(self):
        current, diag, pr, pi = self.contexts(common_equity_issuance=None)
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertEqual(result.status, QualificationStatus.QUALIFIED_WITH_WARNINGS)
        self.assertIsNone(pi.score)
        self.assertIn("PiotroskiScore.COMPLETE", result.optional_diagnostic_missing)

    def test_missing_noncritical_fcf_does_not_force_insufficient(self):
        current, diag, pr, pi = self.contexts(free_cash_flow=None)
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertEqual(result.status, QualificationStatus.QUALIFIED_WITH_WARNINGS)
        self.assertNotIn("current_net_income", result.missing_requirements)

    def test_missing_critical_revenue_is_insufficient(self):
        current, diag, pr, pi = self.contexts(revenue=None)
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertEqual(result.status, QualificationStatus.INSUFFICIENT_DATA)
        self.assertIn("revenue", result.missing_requirements)

    def test_pr_unavailable_and_non_trailing_are_not_rejection(self):
        current, diag, _, pi = self.contexts()
        unavailable = calculate_pr_metrics(ticker="TEST", pe=-1, roe_percent=15, roe_mode="ttm", pe_basis="trailing", data_as_of="2026-01-01")
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=unavailable, financial_diagnostics=diag, piotroski=pi)
        self.assertEqual(result.status, QualificationStatus.QUALIFIED_WITH_WARNINGS)
        non_ttm = calculate_pr_metrics(ticker="TEST", pe=15, roe_percent=15, roe_mode="ttm", pe_basis="forward", data_as_of="2026-01-01")
        self.assertIsNone(non_ttm.pr)

    def test_high_low_piotroski_labels(self):
        current, diag, pr, pi = self.contexts()
        high = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertTrue(any("PIOTROSKI_SCORE_" in item for item in high.reasons))
        low_pi = type(pi)(**{**pi.__dict__, "score": 1, "partial_score": None})
        low = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=low_pi)
        self.assertIn("LOW_F_SCORE_STRONG_WARNING", low.strong_warnings)

    def test_loss_is_only_explicit_exclusion(self):
        current, diag, pr, pi = self.contexts(net_income=-5)
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        self.assertEqual(result.status, QualificationStatus.NOT_QUALIFIED)
        self.assertIn("NOT_QUALIFIED_FOR_CURRENT_VALUE_STRATEGY", result.blockers)

    def test_low_pr_and_single_warnings_do_not_auto_qualify_or_reject(self):
        current, diag, _, pi = self.contexts()
        low_pr = calculate_pr_metrics(ticker="TEST", pe=9, roe_percent=15, roe_mode="ttm", pe_basis="trailing", data_as_of="2026-01-01", pb=1.35, dividend_payout_percent=50)
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=low_pr, financial_diagnostics=diag, piotroski=pi)
        self.assertNotEqual(result.status, QualificationStatus.NOT_QUALIFIED)
        self.assertNotIn("PR_PROJECT_REFERENCE_BAND", result.warnings)

    def test_zero_qualified_contract_and_no_trade_fields(self):
        self.assertEqual(no_qualified_candidate(), ("NO_QUALIFIED_CANDIDATE", 0))
        current, diag, pr, pi = self.contexts()
        result = qualify_candidate(current, as_of="2026-01-01", pr_metrics=pr, financial_diagnostics=diag, piotroski=pi)
        for forbidden in ("rank", "total_score", "target_price", "position_weight", "BUY", "SELL"):
            self.assertFalse(hasattr(result, forbidden))

    def test_current_analysis_boundary_and_no_kell_dependency(self):
        current, diag, pr, pi = self.contexts()
        self.assertEqual(current.analysis_mode.value, "CURRENT_ANALYSIS")
        self.assertIsNone(current.available_at)
        self.assertNotIn("kell_strategy", qualify_candidate.__module__)


if __name__ == "__main__":
    unittest.main()
