"""Offline tests for Step 6A universe and scanner boundaries."""

from datetime import date, datetime, timezone
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import pandas as pd

from investment_agents.data import (
    CurrentFundamentalsMetrics, FinancialPeriod, FundamentalRecord, FundamentalsAnalysisMode,
    FundamentalsHistory, FundamentalsResult, FundamentalsStatus, StaticUniverseProvider,
)
from investment_agents.scanner import CandidateScanner, ScanStatus, SP500UniverseProvider, canonicalize_sp500_ticker, vendor_ticker
from investment_agents.data import TradingAgentsFundamentalsAdapter


class FakeFundamentalsProvider:
    def __init__(self, results):
        self.results = results
    def get_fundamentals(self, ticker, as_of, mode):
        value = self.results[ticker]
        if isinstance(value, Exception):
            raise value
        return value


def make_result(ticker, *, loss=False):
    records = []
    for year in (2025, 2024, 2023):
        records.append(FundamentalRecord(
            ticker=ticker, period=FinancialPeriod.ANNUAL, report_period=date(year, 12, 31),
            filing_date=None, published_at=None, available_at=None, source="fixture",
            revenue=100, net_income=-1 if loss else 20, free_cash_flow=15, equity=50,
            total_debt=30, cash=10, total_assets=100, current_assets=80,
            current_liabilities=40, long_term_debt=20, operating_cash_flow=25,
            gross_profit=40, operating_income=20, diluted_average_shares=10,
            common_equity_issuance=0, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
        ))
    metrics = CurrentFundamentalsMetrics(pe=15, pb=2.25, roe_percent=15, dividend_payout_percent=50, pe_basis="trailing", roe_basis="vendor_defined")
    history = FundamentalsHistory(ticker=ticker, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc), annual=tuple(records), source="fixture", analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS)
    return FundamentalsResult(ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS, status=FundamentalsStatus.AVAILABLE, source="fixture", retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), as_of=history.as_of, current_metrics=metrics, current_history=history)


class ScannerTests(unittest.TestCase):
    def test_yfinance_cache_boundary_uses_supported_writable_location(self):
        adapter = TradingAgentsFundamentalsAdapter()
        with patch("yfinance.cache.set_cache_location") as configure:
            adapter._configure_yfinance_cache()
        configure.assert_called_once()
        configured = configure.call_args.args[0]
        self.assertTrue(configured.replace("\\", "/").endswith("data_cache/yfinance"))

    def test_cli_regression_smoke_without_network(self):
        completed = subprocess.run(
            [sys.executable, "-m", "investment_agents", "scan", "--universe", "custom", "--limit", "0"],
            cwd=os.fspath(__import__("pathlib").Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["attempted"], 0)
        self.assertEqual(payload["status"], "NO_QUALIFIED_CANDIDATE")

    def test_sp500_parser_and_metadata(self):
        provider = SP500UniverseProvider(html_loader=lambda _url: [pd.DataFrame({"Symbol": ["BRK.B", "BRK.B", "AAPL"], "Security": ["Berkshire", "Duplicate", "Apple"], "GICS Sector": ["Finance", "Finance", "Tech"]})])
        universe = provider.get_universe()
        self.assertEqual(universe.tickers, ("BRK.B", "AAPL"))
        self.assertEqual(universe.constituents[0].vendor_ticker, "BRK-B")
        self.assertIsNotNone(universe.fetched_at)

    def test_ticker_normalization_is_explicit(self):
        self.assertEqual(canonicalize_sp500_ticker("brk-b"), "BRK.B")
        self.assertEqual(vendor_ticker("BRK.B"), "BRK-B")

    def test_custom_universe_and_limit_are_not_ranking(self):
        provider = StaticUniverseProvider(["AAPL", "KO"])
        fake = FakeFundamentalsProvider({"AAPL": make_result("AAPL"), "KO": make_result("KO")})
        result = CandidateScanner(provider, fake).scan(limit=1)
        self.assertEqual(result.attempted, 1)
        self.assertEqual(result.records[0].ticker, "AAPL")
        self.assertFalse(hasattr(result, "rank"))
        self.assertFalse(hasattr(result, "top_n"))

    def test_provider_error_does_not_stop_scan(self):
        provider = StaticUniverseProvider(["AAPL", "KO"])
        fake = FakeFundamentalsProvider({"AAPL": RuntimeError("outage"), "KO": make_result("KO")})
        result = CandidateScanner(provider, fake, retries=0).scan()
        self.assertEqual(result.provider_failures, 1)
        self.assertEqual(result.successfully_analyzed, 1)
        self.assertEqual(result.records[0].scan_status, ScanStatus.PROVIDER_ERROR)

    def test_zero_qualified_contract(self):
        provider = StaticUniverseProvider(["AAPL"])
        fake = FakeFundamentalsProvider({"AAPL": make_result("AAPL", loss=True)})
        result = CandidateScanner(provider, fake).scan()
        self.assertEqual(result.qualified + result.qualified_with_warnings, 0)
        self.assertEqual(result.status, "NO_QUALIFIED_CANDIDATE")

    def test_empty_and_malformed_universe_rows(self):
        with self.assertRaises(ValueError):
            SP500UniverseProvider(html_loader=lambda _url: [pd.DataFrame({"Security": ["missing symbol"]})]).get_universe()
        self.assertEqual(StaticUniverseProvider([]).get_universe().tickers, ())

    def test_current_analysis_and_no_trade_fields(self):
        provider = StaticUniverseProvider(["AAPL"])
        result = CandidateScanner(provider, FakeFundamentalsProvider({"AAPL": make_result("AAPL")})).scan()
        self.assertEqual(result.analysis_mode, "CURRENT_ANALYSIS")
        self.assertFalse(hasattr(result.records[0], "rank"))
        self.assertNotIn("BUY", repr(result))
        self.assertNotIn("SELL", repr(result))


if __name__ == "__main__":
    unittest.main()
