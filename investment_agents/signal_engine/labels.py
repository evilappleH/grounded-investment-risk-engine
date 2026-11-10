"""Session-aligned forward benchmark-relative labels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from .contracts import ForwardRelativeReturnLabel, Horizon, SampleIdentity
from .features import AdjustedDailyBar

LABEL_VERSION = "adjusted-close-relative-v1"


def _index_bars(bars: Sequence[AdjustedDailyBar], *, ticker: str) -> Mapping[date, AdjustedDailyBar]:
    if any(bar.ticker != ticker for bar in bars):
        raise ValueError("label history cannot mix tickers")
    result: dict[date, AdjustedDailyBar] = {}
    source_methods = {(bar.source, bar.adjustment_method) for bar in bars}
    if len(source_methods) > 1:
        raise ValueError("each label history must use one source and adjustment methodology")
    for bar in bars:
        if bar.session_date in result:
            raise ValueError("duplicate label session")
        result[bar.session_date] = bar
    return result


def build_relative_return_labels(
    asset_bars: Sequence[AdjustedDailyBar],
    benchmark_bars: Sequence[AdjustedDailyBar],
    *,
    instrument_id: str,
    ticker: str,
    benchmark_id: str,
    benchmark_ticker: str,
    as_of_session: date,
    horizons: Sequence[Horizon] = (Horizon.SESSIONS_20, Horizon.SESSIONS_60),
) -> tuple[ForwardRelativeReturnLabel, ...]:
    """Build labels on the asset/benchmark intersection of trading sessions."""

    asset = _index_bars(asset_bars, ticker=ticker)
    benchmark = _index_bars(benchmark_bars, ticker=benchmark_ticker)
    asset_methods = {bar.adjustment_method for bar in asset.values()}
    benchmark_methods = {bar.adjustment_method for bar in benchmark.values()}
    if asset_methods != benchmark_methods:
        raise ValueError("asset and benchmark must use the same adjustment methodology")
    common_sessions = sorted(set(asset).intersection(benchmark))
    if as_of_session not in common_sessions:
        raise ValueError("as_of session must exist in asset and benchmark histories")
    entry_index = common_sessions.index(as_of_session)
    results: list[ForwardRelativeReturnLabel] = []
    for raw_horizon in horizons:
        horizon = Horizon(raw_horizon)
        identity = SampleIdentity(instrument_id, ticker, as_of_session, horizon, benchmark_id)
        exit_index = entry_index + int(horizon)
        if exit_index >= len(common_sessions):
            results.append(ForwardRelativeReturnLabel(
                identity=identity, entry_session=as_of_session, exit_session=None,
                asset_total_return=None, benchmark_total_return=None, excess_return=None,
                positive_excess_return=None, label_version=LABEL_VERSION, complete=False,
            ))
            continue
        exit_session = common_sessions[exit_index]
        asset_return = asset[exit_session].close / asset[as_of_session].close - 1.0
        benchmark_return = benchmark[exit_session].close / benchmark[as_of_session].close - 1.0
        excess_return = asset_return - benchmark_return
        results.append(ForwardRelativeReturnLabel(
            identity=identity, entry_session=as_of_session, exit_session=exit_session,
            asset_total_return=asset_return, benchmark_total_return=benchmark_return,
            excess_return=excess_return, positive_excess_return=excess_return > 0,
            label_version=LABEL_VERSION, complete=True,
        ))
    return tuple(results)
