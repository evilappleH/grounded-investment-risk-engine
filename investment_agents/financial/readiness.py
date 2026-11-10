"""Non-scoring data readiness checks for future financial models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from investment_agents.data.models import FinancialPeriod, FundamentalRecord


class ReadinessStatus(StrEnum):
    PIOTROSKI_READY = "PIOTROSKI_READY"
    PIOTROSKI_PARTIAL = "PIOTROSKI_PARTIAL"
    PIOTROSKI_INSUFFICIENT = "PIOTROSKI_INSUFFICIENT"


@dataclass(frozen=True)
class FinancialModelReadiness:
    model: str
    status: ReadinessStatus
    available_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    notes: tuple[str, ...] = ()


_PIOTROSKI_FIELDS = (
    "net_income", "operating_cash_flow", "total_assets", "long_term_debt",
    "current_assets", "current_liabilities", "revenue", "gross_profit",
    "common_equity_issuance",
)


def assess_piotroski_readiness(records: Sequence[FundamentalRecord]) -> FinancialModelReadiness:
    """Report field completeness only; this function never computes a score."""
    if any(record.period is not FinancialPeriod.ANNUAL for record in records):
        raise ValueError("Piotroski readiness requires annual records")
    if len({record.ticker for record in records}) > 1:
        raise ValueError("Piotroski readiness cannot mix tickers")
    ordered = sorted(records, key=lambda record: record.report_period, reverse=True)
    relevant = ordered[:3]
    periods_needed = {
        "net_income": 2,
        "operating_cash_flow": 1,
        "total_assets": 3,
        "long_term_debt": 2,
        "current_assets": 2,
        "current_liabilities": 2,
        "revenue": 2,
        "gross_profit": 2,
        "common_equity_issuance": 1,
    }
    present = tuple(
        field for field in _PIOTROSKI_FIELDS
        if len(relevant) >= periods_needed[field]
        and all(getattr(record, field) is not None for record in relevant[:periods_needed[field]])
    )
    confirmed_issuance = bool(
        relevant
        and relevant[0].common_equity_issuance_basis
        in {"Common Stock Issuance", "confirmed_common_equity_issuance", "SEC_COMMON_EQUITY_ISSUANCE"}
        and not relevant[0].common_equity_issuance_is_proxy
    )
    if "common_equity_issuance" in present and not confirmed_issuance:
        present = tuple(field for field in present if field != "common_equity_issuance")
    missing = tuple(field for field in _PIOTROSKI_FIELDS if field not in present)
    if len(relevant) < 3 or not present:
        status = ReadinessStatus.PIOTROSKI_INSUFFICIENT
    elif missing:
        status = ReadinessStatus.PIOTROSKI_PARTIAL
    else:
        status = ReadinessStatus.PIOTROSKI_READY
    return FinancialModelReadiness(
        model="piotroski_data_readiness", status=status,
        available_fields=present, missing_fields=missing,
        notes=("readiness only; no Piotroski F-score calculated",),
    )
