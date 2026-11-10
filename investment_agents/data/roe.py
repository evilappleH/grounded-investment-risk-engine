"""Auditable historical ROE summaries for CURRENT_ANALYSIS only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Sequence

from .models import FinancialPeriod, FundamentalRecord


class ROEAverageStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ROE_AVG_3Y_UNAVAILABLE = "ROE_AVG_3Y_UNAVAILABLE"
    ROE_AVG_5Y_UNAVAILABLE = "ROE_AVG_5Y_UNAVAILABLE"


@dataclass(frozen=True)
class ROEHistorySummary:
    annual_roe_percent: tuple[tuple[object, float], ...]
    roe_avg_3y_percent: float | None
    roe_avg_5y_percent: float | None
    status_3y: ROEAverageStatus
    status_5y: ROEAverageStatus
    min_roe_percent: float | None
    max_roe_percent: float | None
    roe_range_percent: float | None
    valid_year_count: int


def summarize_historical_roe(records: Sequence[FundamentalRecord]) -> ROEHistorySummary:
    """Summarize only finite, explicitly calculated annual ROE values.

    The adapter supplies ROE as ``net_income / average_equity * 100``. This
    function does not fill missing years or infer shorter windows as 3Y/5Y.
    """
    if any(record.period is not FinancialPeriod.ANNUAL for record in records):
        raise ValueError("formal historical ROE requires annual records")
    if len({record.ticker for record in records}) > 1:
        raise ValueError("formal historical ROE cannot mix tickers")
    ordered = sorted(records, key=lambda record: record.report_period, reverse=True)
    valid = [
        (record.report_period, float(record.roe_percent))
        for record in ordered
        if record.roe_percent is not None and isfinite(float(record.roe_percent))
    ]
    values = [value for _, value in valid]

    def formal_average(years: int) -> float | None:
        window = ordered[:years]
        if len(window) != years:
            return None
        if any(
            newer.report_period.year - older.report_period.year != 1
            for newer, older in zip(window, window[1:])
        ):
            return None
        window_values = [record.roe_percent for record in window]
        if any(value is None or not isfinite(float(value)) for value in window_values):
            return None
        return sum(float(value) for value in window_values if value is not None) / years

    avg3 = formal_average(3)
    avg5 = formal_average(5)
    return ROEHistorySummary(
        annual_roe_percent=tuple(valid),
        roe_avg_3y_percent=avg3,
        roe_avg_5y_percent=avg5,
        status_3y=ROEAverageStatus.AVAILABLE if avg3 is not None else ROEAverageStatus.ROE_AVG_3Y_UNAVAILABLE,
        status_5y=ROEAverageStatus.AVAILABLE if avg5 is not None else ROEAverageStatus.ROE_AVG_5Y_UNAVAILABLE,
        min_roe_percent=min(values) if values else None,
        max_roe_percent=max(values) if values else None,
        roe_range_percent=(max(values) - min(values)) if values else None,
        valid_year_count=len(values),
    )
