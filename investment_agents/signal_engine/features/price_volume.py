"""Deterministic and explainable price/volume features.

All OHLC fields in ``AdjustedDailyBar`` must share one adjustment methodology.
The builder rejects future and duplicate rows instead of silently trimming them.
Insufficient warm-up produces ``None`` rather than a fabricated zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite, sqrt
from statistics import mean, median, stdev


@dataclass(frozen=True)
class AdjustedDailyBar:
    ticker: str
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    adjustment_method: str

    def __post_init__(self) -> None:
        if not self.ticker or not self.source.strip() or not self.adjustment_method.strip():
            raise ValueError("ticker, source, and adjustment_method are required")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(isinstance(value, bool) or not isfinite(float(value)) for value in values):
            raise ValueError("OHLCV values must be finite numbers")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("adjusted OHLC must be positive and volume non-negative")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("OHLC range is inconsistent")


def _return(closes: Sequence[float], sessions: int) -> float | None:
    if len(closes) <= sessions:
        return None
    return closes[-1] / closes[-sessions - 1] - 1.0


def _distance_to_mean(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    average = mean(values[-window:])
    return values[-1] / average - 1.0 if average else None


def _linear_stats(values: Sequence[float], window: int) -> tuple[float | None, float | None]:
    if len(values) < window:
        return None, None
    ys = tuple(values[-window:])
    x_mean = (window - 1) / 2.0
    y_mean = mean(ys)
    denominator = sum((index - x_mean) ** 2 for index in range(window))
    slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(ys)) / denominator
    fitted = tuple(y_mean + slope * (index - x_mean) for index in range(window))
    total = sum((value - y_mean) ** 2 for value in ys)
    residual = sum((value - estimate) ** 2 for value, estimate in zip(ys, fitted, strict=True))
    r_squared = 1.0 - residual / total if total > 0 else 1.0
    return (slope / y_mean if y_mean else None), r_squared


def _drawdown(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    peak = max(values[-window:])
    return values[-1] / peak - 1.0


def _daily_returns(values: Sequence[float]) -> list[float]:
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def _realized_volatility(values: Sequence[float], window: int) -> float | None:
    if len(values) <= window:
        return None
    returns = _daily_returns(values[-window - 1:])
    return stdev(returns) * sqrt(252.0) if len(returns) >= 2 else None


def _downside_volatility(values: Sequence[float], window: int) -> float | None:
    if len(values) <= window:
        return None
    returns = _daily_returns(values[-window - 1:])
    return sqrt(mean(min(value, 0.0) ** 2 for value in returns)) * sqrt(252.0)


def _atr_ratio(bars: Sequence[AdjustedDailyBar], window: int) -> float | None:
    if len(bars) <= window:
        return None
    selected = bars[-window:]
    previous_close = bars[-window - 1].close
    true_ranges: list[float] = []
    for bar in selected:
        true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
        previous_close = bar.close
    return mean(true_ranges) / bars[-1].close


def _volume_ratio(volumes: Sequence[float], window: int, *, use_median: bool = False) -> float | None:
    if len(volumes) < window:
        return None
    baseline = median(volumes[-window:]) if use_median else mean(volumes[-window:])
    return volumes[-1] / baseline if baseline > 0 else None


def _robust_volume_z(volumes: Sequence[float], window: int = 20) -> float | None:
    if len(volumes) < window:
        return None
    selected = volumes[-window:]
    center = median(selected)
    mad = median(tuple(abs(value - center) for value in selected))
    if mad == 0:
        return 0.0 if volumes[-1] == center else None
    return (volumes[-1] - center) / (1.4826 * mad)


def _up_down_volume(closes: Sequence[float], volumes: Sequence[float], window: int = 20) -> tuple[float | None, float | None]:
    if len(closes) <= window or len(volumes) <= window:
        return None, None
    returns = _daily_returns(closes[-window - 1:])
    selected_volumes = volumes[-window:]
    up = sum(volume for value, volume in zip(returns, selected_volumes, strict=True) if value > 0)
    down = sum(volume for value, volume in zip(returns, selected_volumes, strict=True) if value < 0)
    total = up + down
    return (up / down if down > 0 else None), (up / total if total > 0 else None)


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = sqrt(left_ss * right_ss)
    return numerator / denominator if denominator > 0 else None


def _return_volume_correlation(closes: Sequence[float], volumes: Sequence[float], window: int = 20) -> float | None:
    if len(closes) <= window or len(volumes) <= window:
        return None
    return _correlation(_daily_returns(closes[-window - 1:]), volumes[-window:])


def _ema(values: Sequence[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def _rsi(values: Sequence[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    changes = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = mean(max(change, 0.0) for change in changes)
    losses = mean(max(-change, 0.0) for change in changes)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    relative_strength = gains / losses
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _macd(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    if len(values) < 35:
        return None, None, None
    fast, slow = _ema(values, 12), _ema(values, 26)
    line = [fast[index] - slow[index] for index in range(len(values))]
    signal = _ema(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


class PriceVolumeFeatureBuilder:
    """Build a fixed V1 feature set from adjusted bars available by ``as_of``."""

    feature_set_version = "price-volume-v1"

    def build(self, bars: Sequence[AdjustedDailyBar], *, ticker: str, as_of: date) -> dict[str, float | None]:
        if not bars:
            raise ValueError("price history is empty")
        if any(bar.ticker != ticker for bar in bars):
            raise ValueError("price history cannot mix tickers")
        methods = {bar.adjustment_method for bar in bars}
        sources = {bar.source for bar in bars}
        if len(methods) != 1 or len(sources) != 1:
            raise ValueError("price history must use one source and adjustment methodology")
        if any(bar.session_date > as_of for bar in bars):
            raise ValueError("future price row supplied to feature builder")
        ordered = tuple(sorted(bars, key=lambda bar: bar.session_date))
        dates = [bar.session_date for bar in ordered]
        if len(dates) != len(set(dates)):
            raise ValueError("duplicate price session")
        if dates[-1] != as_of:
            raise ValueError("as_of session is missing from price history")

        closes = [bar.close for bar in ordered]
        volumes = [bar.volume for bar in ordered]
        gaps = [ordered[index].open / ordered[index - 1].close - 1.0 for index in range(1, len(ordered))]
        slope20, r2_20 = _linear_stats(closes, 20)
        slope60, r2_60 = _linear_stats(closes, 60)
        up_down_ratio, up_volume_share = _up_down_volume(closes, volumes)
        volume_slope20, _ = _linear_stats(volumes, 20)
        return20 = _return(closes, 20)
        divergence = None
        if return20 is not None and volume_slope20 is not None:
            divergence = float((return20 > 0 > volume_slope20) or (return20 < 0 < volume_slope20))
        macd_line, macd_signal, macd_histogram = _macd(closes)
        return {
            "return_1d": _return(closes, 1),
            "return_5d": _return(closes, 5),
            "return_20d": return20,
            "return_60d": _return(closes, 60),
            "sma20_distance": _distance_to_mean(closes, 20),
            "sma60_distance": _distance_to_mean(closes, 60),
            "sma200_distance": _distance_to_mean(closes, 200),
            "trend_slope_20": slope20,
            "trend_r2_20": r2_20,
            "trend_slope_60": slope60,
            "trend_r2_60": r2_60,
            "drawdown_20": _drawdown(closes, 20),
            "drawdown_60": _drawdown(closes, 60),
            "drawdown_252": _drawdown(closes, 252),
            "gap_1d": gaps[-1] if gaps else None,
            "mean_abs_gap_20": mean(abs(value) for value in gaps[-20:]) if len(gaps) >= 20 else None,
            "realized_vol_20": _realized_volatility(closes, 20),
            "realized_vol_60": _realized_volatility(closes, 60),
            "downside_vol_20": _downside_volatility(closes, 20),
            "downside_vol_60": _downside_volatility(closes, 60),
            "atr14_ratio": _atr_ratio(ordered, 14),
            "atr20_ratio": _atr_ratio(ordered, 20),
            "volume_mean_ratio_20": _volume_ratio(volumes, 20),
            "volume_mean_ratio_60": _volume_ratio(volumes, 60),
            "volume_median_ratio_20": _volume_ratio(volumes, 20, use_median=True),
            "volume_robust_z_20": _robust_volume_z(volumes),
            "up_down_volume_ratio_20": up_down_ratio,
            "up_volume_share_20": up_volume_share,
            "return_volume_corr_20": _return_volume_correlation(closes, volumes),
            "price_volume_divergence_20": divergence,
            "rsi14": _rsi(closes),
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
        }
