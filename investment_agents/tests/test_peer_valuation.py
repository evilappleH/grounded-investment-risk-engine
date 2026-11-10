import tempfile
import unittest
from pathlib import Path

from investment_agents.valuation.peer import (
    atomic_write_artifact, build_references, company_type, percentile,
)


def snap(ticker, pe=10, pb=2):
    return {
        "ticker": ticker,
        "market_cap": {"status": "AVAILABLE", "value": 100},
        "net_income_ttm": {"status": "AVAILABLE", "value": 10},
        "equity": {"status": "AVAILABLE", "value": 50},
        "pe_ttm": {"status": "AVAILABLE", "value": pe},
        "pb": {"status": "AVAILABLE", "value": pb},
    }


class PeerValuationTests(unittest.TestCase):
    def test_leave_one_out_median(self):
        snapshots = {t: snap(t, pe=v) for t, v in [("A", 10), ("B", 20), ("C", 30)]}
        classes = {t: {"sector": "Tech", "industry": "Software", "company_type": "ORDINARY_NON_FINANCIAL"} for t in snapshots}
        out = build_references(snapshots, classes, set(), 1)
        self.assertEqual(out["rows"]["A"]["pe_ttm"]["reference_value"], 25)
        self.assertNotIn("A", out["rows"]["A"]["pe_ttm"]["peer_tickers"])

    def test_industry_then_sector_fallback_and_thresholds(self):
        snapshots = {t: snap(t) for t in ["A", "B", "C", "D"]}
        classes = {
            "A": {"sector": "Tech", "industry": "Software", "company_type": "ORDINARY_NON_FINANCIAL"},
            "B": {"sector": "Tech", "industry": "Software", "company_type": "ORDINARY_NON_FINANCIAL"},
            "C": {"sector": "Tech", "industry": "Hardware", "company_type": "ORDINARY_NON_FINANCIAL"},
            "D": {"sector": "Tech", "industry": "Hardware", "company_type": "ORDINARY_NON_FINANCIAL"},
        }
        out = build_references(snapshots, classes, set(), 1)
        self.assertEqual(out["rows"]["A"]["pe_ttm"]["reference_level"], "GICS_INDUSTRY")
        out = build_references(snapshots, classes, set(), 3)
        self.assertEqual(out["rows"]["A"]["pe_ttm"]["reference_level"], "GICS_SECTOR")

    def test_invalid_metric_and_long_loss_excluded(self):
        snapshots = {"A": snap("A"), "B": snap("B", pe=20), "C": snap("C"), "D": snap("D", pe=-1)}
        classes = {t: {"sector": "Tech", "industry": "Software", "company_type": "ORDINARY_NON_FINANCIAL"} for t in snapshots}
        out = build_references(snapshots, classes, {"C"}, 1)
        self.assertEqual(out["rows"]["A"]["pe_ttm"]["peer_tickers"], ["B"])
        self.assertEqual(out["rows"]["D"]["pe_ttm"]["reason"], "COMPANY_METRIC_UNAVAILABLE")

    def test_company_type_separation(self):
        self.assertEqual(company_type("Financials", "Diversified Banks"), "BANK")
        self.assertEqual(company_type("Financials", "Property & Casualty Insurance"), "INSURANCE")
        self.assertEqual(company_type("Real Estate", "Equity REITs"), "REIT")

    def test_ratio_and_atomic_hash(self):
        snapshots = {t: snap(t, pe=v) for t, v in [("A", 10), ("B", 20)]}
        classes = {t: {"sector": "Tech", "industry": "Software", "company_type": "ORDINARY_NON_FINANCIAL"} for t in snapshots}
        out = build_references(snapshots, classes, set(), 1)
        self.assertEqual(out["rows"]["A"]["pe_ttm"]["ratio"], .5)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "peer.json"
            digest = atomic_write_artifact(p, {"x": 1})
            self.assertTrue(digest)
            self.assertEqual(__import__("json").loads(p.read_text())["artifact_hash"], digest)


if __name__ == "__main__":
    unittest.main()
