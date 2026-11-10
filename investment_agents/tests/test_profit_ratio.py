"""Offline standard-library unit tests for the custom PR formula and guards."""

from __future__ import annotations

import unittest

from investment_agents.valuation import (
    PRStatus,
    calculate_pr_from_pb,
    calculate_pr_metrics,
    payout_correction_factor,
    unavailable_pr_cycle,
)


class ProfitRatioTests(unittest.TestCase):
    def metrics(self, **overrides):
        values = {"ticker": "TEST", "pe": 15, "roe_percent": 15, "roe_mode": "ttm", "pb": 2.25,
                  "dividend_payout_percent": 50, "data_as_of": "2026-09-08T00:00:00Z",
                  "earnings_positive": True, "pe_basis": "trailing"}
        values.update(overrides)
        return calculate_pr_metrics(**values)

    def test_pr_values_and_pb_identity(self) -> None:
        self.assertAlmostEqual(self.metrics().pr, 1.0)
        self.assertAlmostEqual(self.metrics(pe=9, pb=1.35).pr, 0.6)
        result = self.metrics()
        self.assertAlmostEqual(result.pr, calculate_pr_from_pb(result.pb, result.roe_percent))
        self.assertAlmostEqual(result.pr_original_scale, 100.0)
        self.assertAlmostEqual(result.pr_original_scale, result.pr * 100.0)

    def test_payout_limits_and_linear_interpolation(self) -> None:
        self.assertEqual(payout_correction_factor(50).factor, 1.0)
        self.assertEqual(payout_correction_factor(70).factor, 1.0)
        self.assertEqual(payout_correction_factor(25).factor, 2.0)
        self.assertEqual(payout_correction_factor(10).factor, 2.0)
        self.assertAlmostEqual(payout_correction_factor(37.5).factor, 1.5)

    def test_invalid_or_missing_data_never_makes_pr(self) -> None:
        for overrides, warning in (({"pe": -5}, "pe_non_positive"), ({"roe_percent": 0}, "roe_non_positive"),
                                   ({"roe_percent": -4}, "roe_non_positive"), ({"pe": None}, "pe_missing"),
                                   ({"roe_percent": None}, "roe_missing"), ({"data_as_of": None}, "data_as_of_missing"),
                                   ({"earnings_positive": False}, "earnings_loss")):
            with self.subTest(overrides=overrides):
                result = self.metrics(**overrides)
                self.assertEqual(result.status, PRStatus.UNAVAILABLE)
                self.assertIsNone(result.pr)
                self.assertIn(warning, result.warnings)

    def test_missing_payout_leaves_corrected_pr_unavailable(self) -> None:
        result = self.metrics(dividend_payout_percent=None)
        self.assertEqual(result.status, PRStatus.CORRECTION_UNAVAILABLE)
        self.assertAlmostEqual(result.pr, 1.0)
        self.assertIsNone(result.pr_corrected)
        self.assertIsNone(result.correction_factor)

    def test_small_valid_percentage_values_are_not_rejected(self) -> None:
        self.assertAlmostEqual(self.metrics(roe_percent=1.0, pe=1.0, pb=0.01).pr, 1.0)
        self.assertAlmostEqual(self.metrics(roe_percent=0.8, pe=0.8, pb=0.0064).pr, 1.0)
        self.assertEqual(payout_correction_factor(1.0).factor, 2.0)

    def test_basis_mismatch_is_warning_only_and_corrected_is_experimental(self) -> None:
        result = self.metrics(
            pe_basis="trailing", pb_basis="current_snapshot", roe_basis="vendor_defined"
        )
        self.assertAlmostEqual(result.pr, 1.0)
        self.assertAlmostEqual(result.pr_from_pb_roe, 1.0)
        self.assertIn("DATA_BASIS_MISMATCH", result.warnings)
        self.assertTrue(result.pr_corrected_experimental)
        self.assertNotIn("BUY", result.warnings)
        self.assertNotIn("SELL", result.warnings)

    def test_only_trailing_pe_is_accepted_for_formal_pr(self) -> None:
        self.assertEqual(self.metrics(pe_basis="trailing").status, PRStatus.AVAILABLE)
        for basis in ("forward", "unknown", None, ""):
            with self.subTest(basis=basis):
                result = self.metrics(pe_basis=basis)
                self.assertEqual(result.status, PRStatus.UNAVAILABLE_NON_TTM_PE)
                self.assertIsNone(result.pr)
                self.assertIn("pe_basis_not_trailing", result.warnings)

    def test_corrected_uses_normalized_pr_and_reference_range_is_not_a_signal(self) -> None:
        result = self.metrics(dividend_payout_percent=25)
        self.assertAlmostEqual(result.pr, 1.0)
        self.assertAlmostEqual(result.pr_corrected, 2.0)
        self.assertTrue(result.pr_corrected_experimental)
        self.assertEqual(result.project_reference_band, (0.4, 0.6))
        self.assertIsNone(next((item for item in result.warnings if item in {"BUY", "SELL"}), None))

    def test_low_roe_warning_does_not_reject_positive_pr(self) -> None:
        result = self.metrics(pe=0.8, roe_percent=0.8, reliability_warnings=("LOW_ROE_WARNING",))
        self.assertAlmostEqual(result.pr, 1.0)
        self.assertIn("LOW_ROE_WARNING", result.warnings)
        self.assertEqual(result.status, PRStatus.AVAILABLE)

    def test_cycle_pr_is_not_computed_without_real_multi_year_roe(self) -> None:
        result = unavailable_pr_cycle()
        self.assertIsNone(result.pr_cycle)
        self.assertIsNone(result.pr_cycle_pb)
        self.assertEqual(result.status.value, "PR_CYCLE_NOT_COMPUTED")


if __name__ == "__main__":
    unittest.main()
