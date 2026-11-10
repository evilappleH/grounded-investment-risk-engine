"""Hand-checkable tests for Signal Engine Slice B price features and labels."""

from __future__ import annotations

from datetime import date, timedelta
import unittest

from investment_agents.signal_engine import Horizon
from investment_agents.signal_engine.features import AdjustedDailyBar, PriceVolumeFeatureBuilder
from investment_agents.signal_engine.labels import build_relative_return_labels


START = date(2024, 1, 1)


def bars(ticker: str, count: int, *, daily_gain: float = 1.0, start_price: float = 100.0, start: date = START):
    result = []
    for index in range(count):
        close = start_price + daily_gain * index
        result.append(AdjustedDailyBar(
            ticker=ticker, session_date=start + timedelta(days=index),
            open=close, high=close * 1.01, low=close * 0.99, close=close,
            volume=1_000.0 + index, source="fixture", adjustment_method="fixture-total-return-v1",
        ))
    return tuple(result)


class PriceVolumeFeatureTests(unittest.TestCase):
    def test_explainable_features_use_only_adjusted_history_through_as_of(self) -> None:
        history = bars("AAPL", 260)
        features = PriceVolumeFeatureBuilder().build(
            history, ticker="AAPL", as_of=history[-1].session_date,
        )
        self.assertAlmostEqual(features["return_1d"], 359 / 358 - 1)
        self.assertAlmostEqual(features["return_20d"], 359 / 339 - 1)
        self.assertAlmostEqual(features["sma20_distance"], 359 / 349.5 - 1)
        self.assertEqual(features["drawdown_252"], 0.0)
        self.assertEqual(features["rsi14"], 100.0)
        self.assertIsNotNone(features["macd_histogram"])

    def test_future_row_and_duplicate_session_are_rejected(self) -> None:
        history = bars("AAPL", 3)
        with self.assertRaisesRegex(ValueError, "future price row"):
            PriceVolumeFeatureBuilder().build(
                history, ticker="AAPL", as_of=history[-2].session_date,
            )
        duplicate = (*history, history[-1])
        with self.assertRaisesRegex(ValueError, "duplicate price session"):
            PriceVolumeFeatureBuilder().build(
                duplicate, ticker="AAPL", as_of=history[-1].session_date,
            )

    def test_warmup_is_missing_not_zero(self) -> None:
        history = bars("AAPL", 10)
        features = PriceVolumeFeatureBuilder().build(
            history, ticker="AAPL", as_of=history[-1].session_date,
        )
        self.assertIsNone(features["return_20d"])
        self.assertIsNone(features["sma200_distance"])
        self.assertIsNone(features["rsi14"])

    def test_mixed_adjustment_method_is_rejected(self) -> None:
        history = list(bars("AAPL", 3))
        last = history[-1]
        history[-1] = AdjustedDailyBar(
            ticker=last.ticker, session_date=last.session_date, open=last.open,
            high=last.high, low=last.low, close=last.close, volume=last.volume,
            source=last.source, adjustment_method="different-method",
        )
        with self.assertRaisesRegex(ValueError, "one source and adjustment"):
            PriceVolumeFeatureBuilder().build(history, ticker="AAPL", as_of=last.session_date)


class RelativeLabelTests(unittest.TestCase):
    def test_label_uses_shared_sessions_and_relative_return(self) -> None:
        asset = bars("AAPL", 25, daily_gain=2.0)
        benchmark = bars("SPY", 25, daily_gain=1.0)
        label = build_relative_return_labels(
            asset, benchmark, instrument_id="US:EQUITY:AAPL", ticker="AAPL",
            benchmark_id="US:ETF:SPY", benchmark_ticker="SPY",
            as_of_session=START, horizons=(Horizon.SESSIONS_20,),
        )[0]
        self.assertTrue(label.complete)
        self.assertEqual(label.exit_session, START + timedelta(days=20))
        self.assertAlmostEqual(label.asset_total_return, 140 / 100 - 1)
        self.assertAlmostEqual(label.benchmark_total_return, 120 / 100 - 1)
        self.assertAlmostEqual(label.excess_return, 0.2)
        self.assertTrue(label.positive_excess_return)

    def test_incomplete_horizon_has_no_partial_target(self) -> None:
        label = build_relative_return_labels(
            bars("AAPL", 10), bars("SPY", 10), instrument_id="US:EQUITY:AAPL",
            ticker="AAPL", benchmark_id="US:ETF:SPY", benchmark_ticker="SPY",
            as_of_session=START, horizons=(Horizon.SESSIONS_20,),
        )[0]
        self.assertFalse(label.complete)
        self.assertIsNone(label.excess_return)
        self.assertIsNone(label.positive_excess_return)

    def test_missing_shared_as_of_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "must exist"):
            build_relative_return_labels(
                bars("AAPL", 25), bars("SPY", 25, start=START + timedelta(days=1)),
                instrument_id="US:EQUITY:AAPL", ticker="AAPL",
                benchmark_id="US:ETF:SPY", benchmark_ticker="SPY",
                as_of_session=START, horizons=(Horizon.SESSIONS_20,),
            )

    def test_asset_and_benchmark_adjustment_must_match(self) -> None:
        benchmark = list(bars("SPY", 25))
        benchmark = tuple(AdjustedDailyBar(
            ticker=bar.ticker, session_date=bar.session_date, open=bar.open,
            high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
            source=bar.source, adjustment_method="other-total-return-method",
        ) for bar in benchmark)
        with self.assertRaisesRegex(ValueError, "same adjustment methodology"):
            build_relative_return_labels(
                bars("AAPL", 25), benchmark, instrument_id="US:EQUITY:AAPL",
                ticker="AAPL", benchmark_id="US:ETF:SPY", benchmark_ticker="SPY",
                as_of_session=START, horizons=(Horizon.SESSIONS_20,),
            )


if __name__ == "__main__":
    unittest.main()
