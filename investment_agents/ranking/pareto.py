"""Unweighted two-dimensional Pareto research-priority ranking.

This module does not score, recommend, or choose investments.  It only
constructs non-dominated fronts among already-qualified candidates with a
complete Piotroski score and formal normalized PR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Mapping

from investment_agents.qualification import QualificationStatus
from investment_agents.scanner.scanner import ScanResult, ScanRecord, ScanStatus


@dataclass(frozen=True)
class RankedCandidate:
    ticker: str
    qualification_status: QualificationStatus
    pareto_front: int
    piotroski_score: int
    normalized_pr: float
    caveats: tuple[str, ...]
    methodology_inputs: Mapping[str, object]


@dataclass(frozen=True)
class UnrankedCandidate:
    ticker: str
    reason: str
    qualification_status: QualificationStatus | None
    available_metrics: Mapping[str, object]
    missing_metrics: tuple[str, ...]


@dataclass(frozen=True)
class RankingResult:
    universe: str
    input_candidate_count: int
    rankable_count: int
    unranked_count: int
    fronts: tuple[tuple[RankedCandidate, ...], ...]
    unranked_candidates: tuple[UnrankedCandidate, ...]
    methodology: Mapping[str, object]
    generated_at: datetime
    status: str


def _record_pr(record: ScanRecord) -> float | None:
    value = record.pr_summary.get("pr")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)) else None


def _eligible(record: ScanRecord) -> tuple[bool, str, tuple[str, ...]]:
    if record.scan_status is not ScanStatus.ANALYZED:
        return False, "SCAN_RECORD_NOT_ANALYZED", ("scan_status",)
    if record.qualification_status not in (
        QualificationStatus.QUALIFIED,
        QualificationStatus.QUALIFIED_WITH_WARNINGS,
    ):
        return False, "QUALIFICATION_NOT_ELIGIBLE", ("qualification_status",)
    if record.piotroski_summary.get("status") != "COMPLETE" or record.piotroski_summary.get("score") is None:
        return False, "UNRANKED_DIAGNOSTIC_INCOMPLETE", ("piotroski_complete_score",)
    score = record.piotroski_summary.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 9:
        return False, "UNRANKED_DIAGNOSTIC_INVALID", ("piotroski_score_0_to_9",)
    if record.pr_summary.get("status") != "PR_AVAILABLE" or _record_pr(record) is None:
        return False, "UNRANKED_VALUATION_UNAVAILABLE", ("normalized_pr",)
    return True, "", ()


def _dominates(a: RankedCandidate, b: RankedCandidate) -> bool:
    return (
        a.piotroski_score >= b.piotroski_score
        and a.normalized_pr <= b.normalized_pr
        and (a.piotroski_score > b.piotroski_score or a.normalized_pr < b.normalized_pr)
    )


def rank_scan_result(scan_result: ScanResult) -> RankingResult:
    """Build Pareto fronts from an existing ``ScanResult`` only.

    The input records are never re-analyzed.  Ties remain on the same front;
    candidates inside each front are alphabetized for deterministic display
    only, not as a secondary investment ordering.
    """
    eligible: list[RankedCandidate] = []
    unranked: list[UnrankedCandidate] = []
    for record in scan_result.records:
        ok, reason, missing = _eligible(record)
        if not ok:
            unranked.append(UnrankedCandidate(
                ticker=record.ticker,
                reason=reason,
                qualification_status=record.qualification_status,
                available_metrics={
                    "piotroski_status": record.piotroski_summary.get("status"),
                    "piotroski_score": record.piotroski_summary.get("score"),
                    "normalized_pr": record.pr_summary.get("pr"),
                },
                missing_metrics=missing,
            ))
            continue
        caveats = tuple(str(item) for item in record.pr_summary.get("warnings", ()) or ())
        eligible.append(RankedCandidate(
            ticker=record.ticker,
            qualification_status=record.qualification_status,  # type: ignore[arg-type]
            pareto_front=0,
            piotroski_score=int(record.piotroski_summary["score"]),
            normalized_pr=float(record.pr_summary["pr"]),
            caveats=caveats,
            methodology_inputs={
                "piotroski": "COMPLETE official score",
                "normalized_pr": "formal PR = PE_TTM / ROE_pct",
                "display_order": "alphabetical within front only",
            },
        ))

    remaining = list(eligible)
    fronts: list[tuple[RankedCandidate, ...]] = []
    front_number = 1
    while remaining:
        front = tuple(
            sorted(
                (candidate for candidate in remaining if not any(
                    _dominates(other, candidate) for other in remaining if other is not candidate
                )),
                key=lambda candidate: candidate.ticker,
            )
        )
        if not front:  # defensive, should be impossible for finite candidates
            break
        fronts.append(tuple(
            RankedCandidate(**{**candidate.__dict__, "pareto_front": front_number})
            for candidate in front
        ))
        front_tickers = {candidate.ticker for candidate in front}
        remaining = [candidate for candidate in remaining if candidate.ticker not in front_tickers]
        front_number += 1

    return RankingResult(
        universe=scan_result.universe,
        input_candidate_count=len(scan_result.records),
        rankable_count=len(eligible),
        unranked_count=len(unranked),
        fronts=tuple(fronts),
        unranked_candidates=tuple(sorted(unranked, key=lambda candidate: candidate.ticker)),
        methodology={
            "name": "two-dimensional Pareto research-priority frontier",
            "dimensions": ("Piotroski COMPLETE official score (higher)", "normalized PR (lower)"),
            "dominance": "score >= and PR <=, with at least one strict improvement",
            "within_front_order": "alphabetical display order only",
            "exclusions": ("PR_corrected", "PR_cycle", "warnings", "market data", "sector"),
        },
        generated_at=datetime.now(timezone.utc),
        status="NO_RANKABLE_CANDIDATE" if not eligible else "RANKABLE_CANDIDATES_AVAILABLE",
    )
