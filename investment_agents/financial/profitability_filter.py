"""Approved long-term profitability universe filter.

This is a deterministic exclusion rule over completed annual GAAP net income.
It never substitutes TTM, adjusted earnings, forecasts, or partial history.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ProfitabilityClassification:
    ticker: str
    status: str
    rule_a: bool
    rule_b: bool
    annual_net_income: tuple[tuple[str, float], ...]
    reason: str


def _annual_values(history: Any) -> list[tuple[str, float]]:
    if isinstance(history, Mapping):
        rows = history.get("annual", ()) or ()
    else:
        rows = getattr(history, "annual", history if isinstance(history, (list, tuple)) else ()) or ()
    values: list[tuple[str, float]] = []
    for row in rows:
        period = getattr(row, "report_period", None)
        value = getattr(row, "net_income", None)
        if isinstance(row, Mapping):
            period = row.get("report_period", period)
            value = row.get("net_income", value)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        values.append((str(period), number))
    # Latest completed fiscal years, newest first; duplicate periods are not
    # silently counted twice.
    dedup = {period: value for period, value in values}
    return sorted(dedup.items(), reverse=True)


def classify_long_term_profitability(ticker: str, history: Any) -> ProfitabilityClassification:
    values = _annual_values(history)
    negatives3 = len(values) >= 3 and all(value < 0 for _, value in values[:3])
    negatives5 = len(values) >= 5 and sum(value < 0 for _, value in values[:5]) >= 4 and sum(value for _, value in values[:5]) < 0
    if negatives3 or negatives5:
        return ProfitabilityClassification(ticker, "LONG_TERM_UNPROFITABLE", negatives3, negatives5, tuple(values[:5]), "RULE_A" if negatives3 else "RULE_B")
    if len(values) < 3:
        return ProfitabilityClassification(ticker, "INSUFFICIENT_PROFIT_HISTORY", False, False, tuple(values), "FEWER_THAN_3_COMPLETED_ANNUAL_YEARS")
    return ProfitabilityClassification(ticker, "PROFITABLE_OR_NOT_LONG_TERM_UNPROFITABLE", False, False, tuple(values[:5]), "NO_RULE_TRIGGERED")
