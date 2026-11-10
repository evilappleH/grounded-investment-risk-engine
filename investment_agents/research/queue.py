"""Research lanes and the structured package future agents may consume.

This layer preserves existing outputs; it does not calculate new financial
metrics or create an investment conclusion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from investment_agents.qualification import QualificationStatus
from investment_agents.ranking import RankingResult
from investment_agents.scanner import ScanResult


METHODOLOGY_VERSION = "step6c-v2-piotroski-average-assets"


@dataclass(frozen=True)
class ResearchPackage:
    ticker: str
    company_name: str | None
    sector: str | None
    industry: str | None
    current_fundamentals: object | None
    historical_fundamentals: object | None
    valuation: object | None
    financial_diagnostics: object | None
    piotroski: object | None
    qualification: object | None
    ranking_context: Mapping[str, object]
    provenance: Mapping[str, object]


@dataclass(frozen=True)
class ResearchQueueEntry:
    ticker: str
    company_name: str | None
    sector: str | None
    industry: str | None
    lane: str
    qualification_status: QualificationStatus
    pareto_front: int | None
    rankability_status: str
    unranked_reason: str | None
    normalized_pr: float | None
    pr_status: str | None
    pr_warnings: tuple[str, ...]
    piotroski_status: str | None
    official_piotroski_score: int | None
    partial_score: int | None
    missing_piotroski_components: tuple[str, ...]
    financial_warnings: tuple[str, ...]
    strong_warnings: tuple[str, ...]
    fundamental_report_period: date | None
    data_retrieved_at: datetime | None
    universe_source: str | None
    universe_fetched_at: datetime | None
    methodology: Mapping[str, object]
    research_package: ResearchPackage


@dataclass(frozen=True)
class ResearchQueue:
    run_id: str
    universe: str
    status: str
    generated_at: datetime
    methodology_version: str
    entries: tuple[ResearchQueueEntry, ...]
    pareto_ranked_count: int
    qualified_unranked_count: int
    excluded_nonqualified_count: int
    universe_snapshot_reference: Mapping[str, object]


def _package(record: object, *, ranking_context: Mapping[str, object], scan: ScanResult) -> ResearchPackage:
    fundamentals = getattr(record, "fundamentals_result", None)
    history = getattr(fundamentals, "current_history", None)
    metrics = getattr(fundamentals, "current_metrics", None)
    return ResearchPackage(
        ticker=record.ticker,
        company_name=record.company_name,
        sector=record.sector,
        industry=getattr(record, "industry", None),
        current_fundamentals=metrics,
        historical_fundamentals=history,
        valuation=getattr(record, "pr_metrics", None),
        financial_diagnostics=getattr(record, "financial_diagnostics", None),
        piotroski=getattr(record, "piotroski_result", None),
        qualification=getattr(record, "qualification_result", None),
        ranking_context=ranking_context,
        provenance={
            "source": getattr(fundamentals, "source", None),
            "retrieved_at": getattr(record, "retrieved_at", None),
            "report_periods": tuple(getattr(item, "report_period", None) for item in getattr(history, "annual", ())),
            "analysis_mode": getattr(getattr(fundamentals, "analysis_mode", None), "value", getattr(fundamentals, "analysis_mode", None)),
            "universe_source": scan.universe_metadata.get("source"),
            "universe_fetched_at": scan.universe_metadata.get("fetched_at"),
            "methodology_version": METHODOLOGY_VERSION,
            "filing_time_unavailable": "filing_time_unavailable" in getattr(history, "warnings", ()),
            "available_time_unavailable": "available_time_unavailable" in getattr(history, "warnings", ()),
        },
    )


def build_research_queue(scan: ScanResult, ranking: RankingResult) -> ResearchQueue:
    """Preserve every qualified result in exactly one research lane."""
    scan_tickers = tuple(record.ticker for record in scan.records)
    ranking_tickers = tuple(candidate.ticker for front in ranking.fronts for candidate in front) + tuple(candidate.ticker for candidate in ranking.unranked_candidates)
    if len(set(scan_tickers)) != len(scan_tickers):
        raise ValueError("ScanResult contains duplicate tickers")
    if ranking.universe != scan.universe or ranking.input_candidate_count != len(scan.records) or set(ranking_tickers) != set(scan_tickers):
        raise ValueError("RankingResult does not correspond to ScanResult")
    ranked_by_ticker = {
        candidate.ticker: candidate
        for front in ranking.fronts
        for candidate in front
    }
    unranked_by_ticker = {candidate.ticker: candidate for candidate in ranking.unranked_candidates}
    entries: list[ResearchQueueEntry] = []
    excluded = 0
    for record in scan.records:
        if record.qualification_status not in (QualificationStatus.QUALIFIED, QualificationStatus.QUALIFIED_WITH_WARNINGS):
            excluded += 1
            continue
        ranked = ranked_by_ticker.get(record.ticker)
        unranked = unranked_by_ticker.get(record.ticker)
        pi = record.piotroski_summary
        pr = record.pr_summary
        if ranked is not None:
            lane = "PARETO_RANKED"
            rankability = "RANKABLE"
            front = ranked.pareto_front
            reason = None
            ranking_context = {"lane": lane, "pareto_front": front, "display_order_only": True}
        else:
            # Qualified packages with verified structural evidence form the
            # balanced research lane.  Diagnostic incompleteness is retained
            # as context and does not erase research eligibility.
            lane = "QUALIFIED_EVIDENCE_COMPLETE_UNRANKED"
            rankability = "UNRANKED"
            front = None
            reason = unranked.reason if unranked else "RANKABILITY_LIMITATION"
            ranking_context = {"lane": lane, "unranked_reason": reason}
        package = _package(record, ranking_context=ranking_context, scan=scan)
        entries.append(ResearchQueueEntry(
            ticker=record.ticker, company_name=record.company_name, sector=record.sector,
            industry=record.industry, lane=lane, qualification_status=record.qualification_status,
            pareto_front=front, rankability_status=rankability, unranked_reason=reason,
            normalized_pr=pr.get("pr"), pr_status=pr.get("status"),
            pr_warnings=tuple(str(item) for item in pr.get("warnings", ()) or ()),
            piotroski_status=pi.get("status"), official_piotroski_score=pi.get("score"),
            partial_score=pi.get("partial_score"),
            missing_piotroski_components=tuple(str(item) for item in pi.get("missing_components", ()) or ()),
            financial_warnings=tuple(getattr(record.financial_diagnostics, "warnings", ()) or ()),
            strong_warnings=record.strong_warnings,
            fundamental_report_period=(
                getattr(getattr(record.fundamentals_result, "current_history", None), "annual", ())[0].report_period
                if getattr(getattr(record.fundamentals_result, "current_history", None), "annual", ()) else None
            ),
            data_retrieved_at=record.retrieved_at,
            universe_source=scan.universe_metadata.get("source"),
            universe_fetched_at=scan.universe_metadata.get("fetched_at"),
            methodology={"version": METHODOLOGY_VERSION, "ranking": ranking.methodology},
            research_package=package,
        ))
    status = "NO_RESEARCH_CANDIDATE" if not entries else "RESEARCH_CANDIDATES_AVAILABLE"
    return ResearchQueue(
        run_id=scan.run_id, universe=scan.universe, status=status,
        generated_at=datetime.now().astimezone(), methodology_version=METHODOLOGY_VERSION,
        entries=tuple(entries), pareto_ranked_count=sum(e.lane == "PARETO_RANKED" for e in entries),
        qualified_unranked_count=sum(e.lane == "QUALIFIED_EVIDENCE_COMPLETE_UNRANKED" for e in entries),
        excluded_nonqualified_count=excluded,
        universe_snapshot_reference={"source": scan.universe_metadata.get("source"), "source_date": scan.universe_metadata.get("source_date"), "fetched_at": scan.universe_metadata.get("fetched_at"), "pit_safe": False},
    )


def jsonable(value: Any) -> Any:
    """Convert frozen models to JSON-safe values without dropping warnings."""
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [jsonable(item) for item in value]
    return value
