from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from investment_agents.data.models import CurrentFundamentalsMetrics, FundamentalsAnalysisMode, FundamentalsResult, FundamentalsStatus
from investment_agents.data.resumable import ResumableFundamentalsCache, ResumableFundamentalsProvider, classify_failure


NOW = datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)


def result(ticker, retrieved=NOW):
    return FundamentalsResult(ticker, FundamentalsAnalysisMode.CURRENT_ANALYSIS, FundamentalsStatus.AVAILABLE, "fake", retrieved, retrieved, current_metrics=CurrentFundamentalsMetrics(net_income=1.0), reason=None)


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = set()
    def get_fundamentals(self, ticker, as_of, mode):
        self.calls.append(ticker)
        if ticker in self.fail:
            raise TimeoutError("timeout")
        return result(ticker, as_of)


class ResumableFundamentalsTests(unittest.TestCase):
    def test_valid_cache_reuse_and_corruption_rejection(self):
        with TemporaryDirectory() as temp:
            cache = ResumableFundamentalsCache(Path(temp))
            cache.save("A", result("A"), requested_as_of=NOW, provider="fake", raw_source_hash="raw", latency_seconds=0.1)
            self.assertIsNotNone(cache.load("A", now=NOW + timedelta(minutes=1)))
            path = cache.path("A")
            path.write_text(path.read_text().replace("normalized_hash", "bad_hash"), encoding="utf-8")
            self.assertIsNone(cache.load("A", now=NOW + timedelta(minutes=1)))

    def test_stale_cache_rejected_and_restart_only_fetches_missing(self):
        with TemporaryDirectory() as temp:
            cache = ResumableFundamentalsCache(Path(temp), validity=timedelta(hours=1))
            cache.save("A", result("A", NOW - timedelta(hours=2)), requested_as_of=NOW, provider="fake", raw_source_hash=None, latency_seconds=0)
            provider = Provider()
            wrapper = ResumableFundamentalsProvider(provider, cache, workers=2, retries=0)
            wrapper.prefetch(("A", "B"), NOW)
            self.assertEqual(set(provider.calls), {"A", "B"})

    def test_timeout_isolated_and_classified(self):
        with TemporaryDirectory() as temp:
            provider = Provider(); provider.fail.add("BAD")
            wrapper = ResumableFundamentalsProvider(provider, ResumableFundamentalsCache(Path(temp)), workers=2, retries=1)
            out = wrapper.prefetch(("GOOD", "BAD"), NOW)
            self.assertEqual(out["BAD"].status, FundamentalsStatus.CURRENT_DATA_UNAVAILABLE)
            self.assertEqual(classify_failure(TimeoutError("x")), "TIMEOUT")
            self.assertEqual(classify_failure(ConnectionError("x")), "NETWORK_ERROR")

    def test_current_analysis_only_and_no_order_bias(self):
        with TemporaryDirectory() as temp:
            provider = Provider(); wrapper = ResumableFundamentalsProvider(provider, ResumableFundamentalsCache(Path(temp)), workers=3, retries=0)
            out = wrapper.prefetch(("Z", "A", "M"), NOW)
            self.assertEqual(set(out), {"Z", "A", "M"})
            pit = wrapper.get_fundamentals("A", NOW, FundamentalsAnalysisMode.BACKTEST_PIT)
            self.assertEqual(pit.status, FundamentalsStatus.PIT_DATA_UNAVAILABLE)

    def test_vendor_share_class_symbols_are_canonicalized_without_merging(self):
        with TemporaryDirectory() as temp:
            provider = Provider(); wrapper = ResumableFundamentalsProvider(provider, ResumableFundamentalsCache(Path(temp)), workers=1, retries=0)
            out = wrapper.prefetch(("BRK-B", "BF-B"), NOW)
            self.assertEqual(set(out), {"BRK.B", "BF.B"})
            self.assertEqual(set(provider.calls), {"BRK.B", "BF.B"})


if __name__ == "__main__":
    unittest.main()
