"""Financial fact and data-quality diagnostics.

This module deliberately does not score, rank, qualify, reject, or emit a
trading signal.  Missing values remain missing and all derived ratios are
computed only with finite, valid denominators.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from typing import Mapping

from investment_agents.data.models import FundamentalRecord
from investment_agents.valuation.profit_ratio import PRMetrics


class DataQuality(StrEnum):
    """Completeness of the supplied diagnostic inputs, not company quality."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class FinancialDiagnostics:
    """Facts, derived metrics, warnings, and provenance for one record."""

    ticker: str
    as_of: date | datetime | None
    report_period: date
    metrics: Mapping[str, float | None]
    warnings: tuple[str, ...]
    data_quality: DataQuality
    source: str
    retrieved_at: datetime | None
    source_metadata: Mapping[str, object]
    pr_metrics: PRMetrics | None = None


def _finite(value: float | int | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def diagnose_financials(
    record: FundamentalRecord,
    *,
    as_of: date | datetime | None = None,
    pr_metrics: PRMetrics | None = None,
) -> FinancialDiagnostics:
    """Describe financial facts without producing an investment conclusion."""

    values: dict[str, float | None] = {
        "revenue": _finite(record.revenue),
        "net_income": _finite(record.net_income),
        "free_cash_flow": _finite(record.free_cash_flow),
        "equity": _finite(record.equity),
        "total_debt": _finite(record.total_debt),
        "cash": _finite(record.cash),
    }
    warnings: list[str] = list(record.warnings)
    field_names = {
        "revenue": "MISSING_REVENUE",
        "net_income": "MISSING_NET_INCOME",
        "free_cash_flow": "MISSING_FCF",
        "equity": "MISSING_EQUITY",
        "total_debt": "MISSING_DEBT",
        "cash": "MISSING_CASH",
    }
    for field, warning in field_names.items():
        if values[field] is None:
            warnings.append(warning)

    net_income = values["net_income"]
    fcf = values["free_cash_flow"]
    revenue = values["revenue"]
    equity = values["equity"]
    debt = values["total_debt"]
    cash = values["cash"]

    if net_income is not None and net_income < 0:
        warnings.append("NEGATIVE_NET_INCOME")
    if fcf is not None and fcf < 0:
        warnings.append("NEGATIVE_FREE_CASH_FLOW")
    if equity is not None and equity < 0:
        warnings.append("NEGATIVE_EQUITY")
    if equity == 0:
        warnings.append("ZERO_EQUITY")
    if equity is not None and equity <= 0:
        warnings.append("ROE_NOT_INTERPRETABLE_NON_POSITIVE_EQUITY")

    metrics: dict[str, float | None] = dict(values)
    metrics["fcf_margin"] = fcf / revenue if fcf is not None and revenue is not None and revenue > 0 else None
    metrics["fcf_to_net_income"] = (
        fcf / net_income if fcf is not None and net_income is not None and net_income > 0 else None
    )
    metrics["net_debt"] = debt - cash if debt is not None and cash is not None else None
    metrics["debt_to_equity"] = debt / equity if debt is not None and equity is not None and equity > 0 else None
    metrics["cash_to_debt"] = cash / debt if cash is not None and debt is not None and debt > 0 else None

    net_debt = metrics["net_debt"]
    if net_debt is not None and net_debt > 0:
        warnings.append("NET_DEBT_POSITIVE")
    if fcf is not None and net_income is not None and fcf < net_income:
        warnings.append("FCF_BELOW_NET_INCOME")

    # Preserve only contextual PR warnings; never alter the PR value.
    if pr_metrics is not None:
        warnings.extend(pr_metrics.warnings)

    present_core = sum(value is not None for value in values.values())
    quality = DataQuality.COMPLETE if present_core == len(values) else (
        DataQuality.PARTIAL if present_core else DataQuality.INSUFFICIENT
    )
    source_metadata = {
        "analysis_mode": record.analysis_mode.value,
        "field_sources": dict(record.field_sources),
        "field_metadata": dict(record.field_metadata),
        "filing_date": record.filing_date,
        "published_at": record.published_at,
        "available_at": record.available_at,
    }
    return FinancialDiagnostics(
        ticker=record.ticker,
        as_of=as_of,
        report_period=record.report_period,
        metrics=metrics,
        warnings=tuple(dict.fromkeys(warnings)),
        data_quality=quality,
        source=record.source,
        retrieved_at=record.retrieved_at,
        source_metadata=source_metadata,
        pr_metrics=pr_metrics,
    )
