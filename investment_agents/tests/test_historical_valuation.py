import unittest
from datetime import date
from investment_agents.valuation.historical import (
    historical_pb, historical_pe_a, historical_pe_b, history_bucket,
    relative_difference, select_historical_shares,
)


class HistoricalValuationTests(unittest.TestCase):
    def facts(self):
        return {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 100, "end": "2024-12-31", "filed": "2025-02-01", "form": "10-K"},
            {"val": 110, "end": "2025-01-20", "filed": "2025-02-10", "form": "10-K"},
        ]}}}}}

    def test_exact_and_window_share_matching(self):
        self.assertEqual(select_historical_shares(self.facts(), date(2024, 12, 31), window_days=0)["shares"], 100)
        self.assertEqual(select_historical_shares(self.facts(), date(2025, 1, 1), window_days=30)["shares"], 100)
        self.assertEqual(select_historical_shares(self.facts(), date(2025, 3, 1), window_days=60)["shares"], 110)

    def test_method_a_b_pb_and_invalid_denominators(self):
        self.assertEqual(historical_pe_a(10, 100, 50), 20)
        self.assertIsNone(historical_pe_a(10, 100, -1))
        self.assertEqual(historical_pe_b(10, 2, basis_compatible=True), 5)
        self.assertIsNone(historical_pe_b(10, 2, basis_compatible=False))
        self.assertEqual(historical_pb(1000, 100), 10)
        self.assertIsNone(historical_pb(1000, 0))

    def test_multiclass_and_history_buckets(self):
        self.assertEqual(select_historical_shares(self.facts(), date(2024, 12, 31), window_days=0, security_class_compatible=False)["reason"], "HISTORICAL_SHARE_CLASS_BASIS_UNCERTAIN")
        self.assertEqual(history_bucket(5), "5_PLUS_VALID_YEARS")
        self.assertEqual(history_bucket(3), "3_TO_4_VALID_YEARS")
        self.assertEqual(history_bucket(1), "1_TO_2_VALID_YEARS")
        self.assertEqual(history_bucket(0), "ZERO_VALID_YEARS")

    def test_relative_difference(self):
        self.assertAlmostEqual(relative_difference(10, 11), 1/10.5)
        self.assertIsNone(relative_difference(0, 10))


if __name__ == "__main__":
    unittest.main()
