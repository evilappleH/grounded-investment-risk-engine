"""Explainable, deliberately conservative candidate qualification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Mapping

from investment_agents.data.models import FundamentalRecord, FundamentalsAnalysisMode
from investment_agents.financial.diagnostics import DataQuality, FinancialDiagnostics
from investment_agents.financial.piotroski import PiotroskiScore
from investment_agents.valuation.profit_ratio import PRCycleMetrics, PRMetrics


class QualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    QUALIFIED_WITH_WARNINGS = "QUALIFIED_WITH_WARNINGS"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class QualificationRule:
    rule_id: str
    description: str
    required_inputs: tuple[str, ...]
    severity: str
    action: str
    scope: str
    rationale: str
    source: str
    exceptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    triggered: bool
    action: str
    reason: str


@dataclass(frozen=True)
class CandidateQualificationResult:
    ticker: str
    status: QualificationStatus
    as_of: date | datetime | str | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    strong_warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    data_quality: DataQuality | str
    missing_requirements: tuple[str, ...]
    pr_context: PRMetrics | PRCycleMetrics | None
    piotroski_context: PiotroskiScore | None
    financial_context: FinancialDiagnostics | None
    rule_results: tuple[RuleResult, ...]
    source_metadata: Mapping[str, object]
    optional_diagnostic_missing: tuple[str, ...] = ()


RULES: tuple[QualificationRule, ...] = (
    QualificationRule(
        "CURRENT_LOSS_EXCLUSION", "Current net loss is outside the profitable long-term equity candidate scope",
        ("FundamentalRecord.net_income",), "BLOCKER", "NOT_QUALIFY", "current_analysis",
        "The declared scope is profitable/analyzable long-term equity candidates; this is not a forecast of price decline.",
        "project_scope:profitable_current_value_analysis", ("Not a BUY/SELL conclusion",),
    ),
)


def _pr_labels(pr: PRMetrics | PRCycleMetrics | None) -> tuple[str, ...]:
    if pr is None or not isinstance(pr, PRMetrics):
        return ("PR_UNAVAILABLE",)
    if pr.pr is None:
        return ("PR_UNAVAILABLE",)
    if 0.4 <= pr.pr <= 0.6:
        # The approved project reference band is descriptive PR metadata only.
        # It must not alter qualification status or warnings.
        return ()
    if pr.pr == 1.0:
        return ("PR_NEAR_REFERENCE",)
    if pr.pr > 1.0:
        return ("PR_HIGH",)
    return ()


def qualify_candidate(
    record: FundamentalRecord | None,
    *,
    as_of: date | datetime | str | None,
    pr_metrics: PRMetrics | None = None,
    pr_cycle: PRCycleMetrics | None = None,
    financial_diagnostics: FinancialDiagnostics | None = None,
    piotroski: PiotroskiScore | None = None,
) -> CandidateQualificationResult:
    """Qualify one candidate using existing module results only."""
    ticker = record.ticker if record else (financial_diagnostics.ticker if financial_diagnostics else "")
    warnings: list[str] = []
    strong_warnings: list[str] = []
    blockers: list[str] = []
    reasons: list[str] = []
    missing: list[str] = []
    rule_results: list[RuleResult] = []

    if record is None:
        missing.append("FundamentalRecord")
    if financial_diagnostics is None:
        missing.append("FinancialDiagnostics")
    if as_of is None:
        missing.append("as_of")
    if missing:
        return CandidateQualificationResult(
            ticker=ticker, status=QualificationStatus.INSUFFICIENT_DATA, as_of=as_of,
            reasons=("required qualification inputs are missing",), warnings=tuple(warnings),
            strong_warnings=tuple(strong_warnings), blockers=tuple(blockers),
            data_quality=financial_diagnostics.data_quality if financial_diagnostics else DataQuality.INSUFFICIENT,
            missing_requirements=tuple(missing), pr_context=pr_metrics or pr_cycle,
            optional_diagnostic_missing=(),
            piotroski_context=piotroski, financial_context=financial_diagnostics,
            rule_results=tuple(rule_results), source_metadata={"analysis_mode": record.analysis_mode.value if record else None},
        )

    if record.analysis_mode is not FundamentalsAnalysisMode.CURRENT_ANALYSIS:
        raise ValueError("candidate qualification requires CURRENT_ANALYSIS fundamentals")
    for name, context_ticker in (
        ("FinancialDiagnostics", financial_diagnostics.ticker),
        ("PRMetrics", pr_metrics.ticker if pr_metrics is not None else ticker),
        ("PiotroskiScore", piotroski.ticker if piotroski is not None and piotroski.ticker else ticker),
    ):
        if context_ticker != ticker:
            raise ValueError(f"{name} ticker does not match candidate ticker")

    critical_fields = {
        "current_net_income": record.net_income,
        "revenue": record.revenue,
        "equity": record.equity,
        "total_debt": record.total_debt,
        "cash": record.cash,
    }
    critical_missing = tuple(name for name, value in critical_fields.items() if value is None)
    optional_missing: list[str] = []
    if pr_metrics is None:
        optional_missing.append("PRMetrics")
    elif pr_metrics.pr is None:
        optional_missing.append("PRMetrics.pr")
    if piotroski is None:
        optional_missing.append("PiotroskiScore")
    elif piotroski.status != "COMPLETE":
        optional_missing.append("PiotroskiScore.COMPLETE")
    if "filing_time_unavailable" in financial_diagnostics.warnings:
        optional_missing.append("filing_time_unavailable")
    if "available_time_unavailable" in financial_diagnostics.warnings:
        optional_missing.append("available_time_unavailable")
    if critical_missing:
        return CandidateQualificationResult(
            ticker=ticker, status=QualificationStatus.INSUFFICIENT_DATA, as_of=as_of,
            reasons=("critical current financial data is missing",), warnings=tuple(warnings),
            strong_warnings=tuple(strong_warnings), blockers=tuple(blockers),
            data_quality=financial_diagnostics.data_quality,
            missing_requirements=critical_missing,
            optional_diagnostic_missing=tuple(optional_missing), pr_context=pr_metrics or pr_cycle,
            piotroski_context=piotroski, financial_context=financial_diagnostics,
            rule_results=tuple(rule_results), source_metadata={"analysis_mode": record.analysis_mode.value, "source": record.source},
        )

    if optional_missing:
        warnings.extend(f"OPTIONAL_DIAGNOSTIC_MISSING:{item}" for item in optional_missing)

    warnings.extend(financial_diagnostics.warnings)
    warnings.extend(_pr_labels(pr_metrics))
    if pr_metrics is not None and pr_metrics.pr is not None and any(
        item in pr_metrics.warnings for item in ("ROE_BASIS_VENDOR_DEFINED", "DATA_BASIS_MISMATCH")
    ):
        warnings.append("PR_RELIABILITY_WARNING")
    if piotroski is not None and piotroski.status == "COMPLETE" and piotroski.score is not None:
        if piotroski.score >= 8:
            reasons.append("HIGH_F_SCORE")
        elif piotroski.score <= 1:
            strong_warnings.append("LOW_F_SCORE_STRONG_WARNING")
        else:
            reasons.append(f"PIOTROSKI_SCORE_{piotroski.score}")
    else:
        warnings.append("PIOTROSKI_PARTIAL")

    loss = record.net_income is not None and record.net_income < 0
    rule_results.append(RuleResult("CURRENT_LOSS_EXCLUSION", loss, "NOT_QUALIFY" if loss else "NONE", "current net income is negative" if loss else "current net income is not negative"))
    if loss:
        blockers.append("NOT_QUALIFIED_FOR_CURRENT_VALUE_STRATEGY")
        reasons.append("current loss is outside the declared profitable-candidate scope")
        status = QualificationStatus.NOT_QUALIFIED
    elif optional_missing:
        status = QualificationStatus.QUALIFIED_WITH_WARNINGS
    elif warnings or strong_warnings:
        status = QualificationStatus.QUALIFIED_WITH_WARNINGS
    else:
        status = QualificationStatus.QUALIFIED
    return CandidateQualificationResult(
        ticker=ticker, status=status, as_of=as_of, reasons=tuple(reasons),
        warnings=tuple(dict.fromkeys(warnings)), strong_warnings=tuple(dict.fromkeys(strong_warnings)),
        blockers=tuple(blockers), data_quality=financial_diagnostics.data_quality,
        missing_requirements=(), optional_diagnostic_missing=tuple(optional_missing), pr_context=pr_metrics or pr_cycle,
        piotroski_context=piotroski, financial_context=financial_diagnostics,
        rule_results=tuple(rule_results), source_metadata={"analysis_mode": record.analysis_mode.value, "source": record.source},
    )


def no_qualified_candidate() -> tuple[str, int]:
    """Explicit zero-qualified contract for a future scanner."""
    return ("NO_QUALIFIED_CANDIDATE", 0)
