import unittest
from datetime import date, datetime, timezone

from investment_agents.financial import classify_long_term_profitability
from investment_agents.portfolio import AutonomousPortfolioRunner, STRATEGY_INITIAL_NAV_USD
from investment_agents.integrations.moomoo import evaluate_pre_auto_paper_test


class ProfitabilityAndRingFenceTests(unittest.TestCase):
    def test_rule_a_three_consecutive_losses(self):
        rows = [{"report_period": str(y), "net_income": -1} for y in (2025, 2024, 2023)]
        out = classify_long_term_profitability("X", rows)
        self.assertEqual(out.status, "LONG_TERM_UNPROFITABLE")
        self.assertTrue(out.rule_a)

    def test_rule_b_four_of_five_and_negative_cumulative(self):
        rows = [{"report_period": str(y), "net_income": value} for y, value in zip((2025, 2024, 2023, 2022, 2021), (1, -2, -2, -2, -2))]
        out = classify_long_term_profitability("X", rows)
        self.assertEqual(out.status, "LONG_TERM_UNPROFITABLE")
        self.assertTrue(out.rule_b)

    def test_incomplete_history_is_not_excluded(self):
        out = classify_long_term_profitability("X", [{"report_period": "2025", "net_income": -1}, {"report_period": "2024", "net_income": -1}])
        self.assertEqual(out.status, "INSUFFICIENT_PROFIT_HISTORY")

    def test_runner_hard_ring_fence(self):
        self.assertEqual(STRATEGY_INITIAL_NAV_USD, 100000.0)
        with self.assertRaises(ValueError):
            AutonomousPortfolioRunner().run_current_sp500_shadow(root="data_cache", run_id="ring-fence-test", initial_strategy_cash=100001.0)

    def test_pre_auto_test_gate_hard_limits(self):
        self.assertTrue(evaluate_pre_auto_paper_test(acc_id=1000001, expected_acc_id=1000001, trd_env="SIMULATE", market="US", quantity=1, limit_price=100, quote_fresh=True, risk_pass=True, policy_pass=True).eligible)
        self.assertFalse(evaluate_pre_auto_paper_test(acc_id=1, expected_acc_id=1000001, trd_env="REAL", market="US", quantity=1, limit_price=100, quote_fresh=True, risk_pass=True, policy_pass=True).eligible)
        self.assertFalse(evaluate_pre_auto_paper_test(acc_id=1000001, expected_acc_id=1000001, trd_env="SIMULATE", market="US", quantity=1, limit_price=501, quote_fresh=True, risk_pass=True, policy_pass=True).eligible)


if __name__ == "__main__":
    unittest.main()
