from datetime import date, datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from investment_agents.valuation.current import (
    build_current_valuation_snapshot, persist_current_snapshot,
    latest_completed_daily_close, select_sec_shares, compute_ttm_from_sec_rows,
    resolve_sec_cik,
)


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def facts(**overrides):
    base = dict(net_income_ttm=10, operating_cash_flow_ttm=12, capital_expenditures_ttm=2,
                ebitda_ttm=15, cash=3, total_debt=5, equity=20, roe_percent=10,
                accounting_basis="SEC", capex_basis="SEC", capex_is_outflow=True)
    base.update(overrides)
    return base


class CurrentValuationTests(unittest.TestCase):
    def test_identities_and_provenance(self):
        s = build_current_valuation_snapshot("ABNB", as_of=NOW,
            market=dict(price=10, shares_outstanding=100, shares_basis="common_outstanding",
                        security_identity="ABNB", share_class_identity="ABNB", price_source="quote"),
            fundamentals=facts())
        self.assertEqual(s.market_cap.value, 1000)
        self.assertEqual(s.fcf_ttm.value, 10)
        self.assertEqual(s.enterprise_value.value, 1002)
        self.assertEqual(s.pe_ttm.value, 100)
        self.assertEqual(s.fcf_yield.value, .01)
        self.assertEqual(s.ev_ebitda.value, 1002/15)
        self.assertEqual(s.pb.value, 50)
        self.assertEqual(s.pr.value, 10)
        self.assertEqual(s.market_cap.provenance["formula"], "price * compatible_shares_outstanding")

    def test_share_class_mismatch_fails_closed(self):
        s = build_current_valuation_snapshot("BRK.B", as_of=NOW,
            market=dict(price=500, shares_outstanding=100, shares_basis="common_outstanding",
                        security_identity="BRK.B", share_class_identity="BRK-A"), fundamentals=facts())
        self.assertIsNone(s.market_cap.value)
        self.assertEqual(s.shares_outstanding.reason, "SHARE_CLASS_BASIS_UNCERTAIN")

    def test_nonpositive_denominators(self):
        s = build_current_valuation_snapshot("X", as_of=NOW,
            market=dict(price=10, shares_outstanding=100, shares_basis="common_outstanding",
                        security_identity="X", share_class_identity="X"),
            fundamentals=facts(net_income_ttm=0, ebitda_ttm=0, equity=-1))
        self.assertEqual(s.pe_ttm.reason, "NONPOSITIVE_NET_INCOME")
        self.assertEqual(s.ev_ebitda.reason, "EV_EBITDA_NOT_MEANINGFUL")
        self.assertEqual(s.pb.reason, "NONPOSITIVE_EQUITY")

    def test_missing_values_never_become_zero_and_pit_fails_closed(self):
        s = build_current_valuation_snapshot("X", as_of=NOW, market=None, fundamentals={})
        self.assertIsNone(s.market_cap.value)
        self.assertNotEqual(s.market_cap.value, 0)
        pit = build_current_valuation_snapshot("X", as_of=NOW, market={}, fundamentals=facts(), analysis_mode="BACKTEST_PIT")
        self.assertEqual(pit.market_cap.reason, "BACKTEST_PIT_VALUATION_UNAVAILABLE")

    def test_atomic_persistence_and_hash(self):
        s = build_current_valuation_snapshot("X", as_of=NOW,
            market=dict(price=1, shares_outstanding=2, shares_basis="common_outstanding", security_identity="X", share_class_identity="X"), fundamentals=facts())
        with tempfile.TemporaryDirectory() as d:
            p = persist_current_snapshot(s, d)
            payload = json.loads(Path(p).read_text())
            self.assertEqual(payload["analysis_mode"], "CURRENT_ANALYSIS")
            self.assertTrue(payload["snapshot_hash"])

    def test_latest_completed_close_ignores_future_and_null(self):
        out = latest_completed_daily_close([
            {"Date": "2026-09-10", "Close": 10},
            {"Date": "2026-09-11", "Close": None},
            {"Date": "2026-09-12", "Close": 12},
        ], as_of=date(2026, 9, 11))
        self.assertEqual(out["price"], 10)
        self.assertEqual(out["price_date"], "2026-09-10")

    def test_sec_current_shares_precedence_and_conflict(self):
        rows = [
            {"val": 100, "end": "2026-06-30", "filed": "2026-07-01", "form": "10-Q"},
            {"val": 110, "end": "2026-06-30", "filed": "2026-07-02", "form": "10-Q/A"},
        ]
        facts = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": rows}}}}}
        out = select_sec_shares(facts, as_of=date(2026, 9, 1), security_identity="X")
        self.assertEqual(out["shares_outstanding"], 110)
        bad_rows = [
            {"val": 100, "end": "2026-06-30", "filed": "2026-07-02", "form": "10-Q"},
            {"val": 110, "end": "2026-06-30", "filed": "2026-07-02", "form": "10-Q/A"},
        ]
        bad = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": bad_rows}}}}}
        self.assertEqual(select_sec_shares(bad, as_of=date(2026, 9, 1), security_identity="X")["reason"], "SHARES_CONFLICT")

    def test_ttm_fy_and_ytd_adjustment(self):
        fy = [{"val": 100, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "fp": "FY"}]
        self.assertEqual(compute_ttm_from_sec_rows(fy, as_of=date(2026, 3, 1))["value"], 100)
        rows = fy + [
            {"val": 60, "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "fp": "Q2"},
            {"val": 50, "start": "2025-01-01", "end": "2025-06-30", "filed": "2025-08-01", "form": "10-Q", "fp": "Q2"},
        ]
        out = compute_ttm_from_sec_rows(rows, as_of=date(2026, 9, 1))
        self.assertEqual(out["value"], 110)
        self.assertEqual(out["method"], "FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD")

    def test_ttm_noncomparable_period_fails_closed(self):
        rows = [
            {"val": 100, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "fp": "FY"},
            {"val": 60, "start": "2026-01-01", "end": "2026-09-30", "filed": "2026-11-01", "form": "10-Q", "fp": "Q3"},
        ]
        self.assertEqual(compute_ttm_from_sec_rows(rows, as_of=date(2026, 12, 1))["reason"], "TTM_PERIOD_INCOMPATIBLE")

    def test_sec_cik_lookup_preserves_share_class_identity(self):
        mapping = {"BRK-B": "0001067983", "ABNB": "0001559720"}
        self.assertEqual(resolve_sec_cik("ABNB", mapping)["cik"], "0001559720")
        self.assertEqual(resolve_sec_cik("BRK.B", mapping)["cik"], "0001067983")
        self.assertEqual(resolve_sec_cik("BRK.B", mapping)["normalized_ticker"], "BRK.B")

    def test_missing_shares_does_not_block_sec_flows(self):
        s = build_current_valuation_snapshot("X", as_of=NOW,
            market={"price": 10, "shares_outstanding": None, "security_identity": "X", "share_class_identity": "X"},
            fundamentals=facts(net_income_ttm=20, operating_cash_flow_ttm=30, equity=40))
        self.assertEqual(s.net_income_ttm.value, 20)
        self.assertEqual(s.operating_cash_flow_ttm.value, 30)
        self.assertEqual(s.market_cap.reason, "MARKET_CAP_UNAVAILABLE")

    def test_missing_capex_does_not_block_market_metrics(self):
        s = build_current_valuation_snapshot("X", as_of=NOW,
            market={"price": 10, "shares_outstanding": 100, "shares_basis": "common_outstanding",
                    "security_identity": "X", "share_class_identity": "X"},
            fundamentals=facts(capital_expenditures_ttm=None, net_income_ttm=20, equity=40))
        self.assertEqual(s.market_cap.value, 1000)
        self.assertEqual(s.pe_ttm.value, 50)
        self.assertEqual(s.pb.value, 25)

    def test_missing_debt_does_not_block_pe_or_pb(self):
        s = build_current_valuation_snapshot("X", as_of=NOW,
            market={"price": 10, "shares_outstanding": 100, "shares_basis": "common_outstanding",
                    "security_identity": "X", "share_class_identity": "X"},
            fundamentals=facts(total_debt=None, net_income_ttm=20, equity=40))
        self.assertEqual(s.pe_ttm.value, 50)
        self.assertEqual(s.pb.value, 25)


if __name__ == "__main__":
    unittest.main()
