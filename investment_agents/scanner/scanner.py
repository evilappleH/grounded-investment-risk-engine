"""Serial, failure-isolated current-analysis candidate scanner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import time
from uuid import uuid4
from typing import Callable, Mapping

from investment_agents.data.models import FundamentalsAnalysisMode, FundamentalsStatus
from investment_agents.data.providers import FundamentalsProvider
from investment_agents.data.universe import StockUniverse, UniverseConstituent
from investment_agents.financial import calculate_piotroski_score, diagnose_financials
from investment_agents.qualification import QualificationStatus, qualify_candidate
from investment_agents.valuation import calculate_pr_metrics


class ScanStatus(StrEnum):
    ANALYZED = "ANALYZED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_TICKER = "INVALID_TICKER"
    QUALIFICATION_ERROR = "QUALIFICATION_ERROR"


@dataclass(frozen=True)
class ScanRecord:
    ticker: str
    company_name: str | None
    sector: str | None
    scan_status: ScanStatus
    qualification_status: QualificationStatus | None
    data_status: str
    pr_summary: Mapping[str, object]
    piotroski_summary: Mapping[str, object]
    warnings_count: int
    strong_warnings: tuple[str, ...]
    failure_reason: str | None
    retrieved_at: datetime | None
    fundamentals_result: object | None = None
    pr_metrics: object | None = None
    financial_diagnostics: object | None = None
    piotroski_result: object | None = None
    qualification_result: object | None = None
    industry: str | None = None


@dataclass(frozen=True)
class ScanResult:
    universe: str
    universe_size: int
    attempted: int
    successfully_analyzed: int
    qualified: int
    qualified_with_warnings: int
    not_qualified: int
    insufficient_data: int
    provider_failures: int
    started_at: datetime
    completed_at: datetime
    records: tuple[ScanRecord, ...]
    status: str
    universe_metadata: Mapping[str, object]
    analysis_mode: str = FundamentalsAnalysisMode.CURRENT_ANALYSIS.value
    qualified_tickers: tuple[str, ...] = ()
    run_id: str = ""
    universe_constituents: tuple[UniverseConstituent, ...] = ()


class CandidateScanner:
    """Run existing data/valuation/diagnostic/qualification modules serially."""

    def __init__(
        self,
        universe_provider: object,
        fundamentals_provider: FundamentalsProvider,
        *,
        as_of: datetime | None = None,
        retries: int = 1,
        backoff_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.universe_provider = universe_provider
        self.fundamentals_provider = fundamentals_provider
        self.as_of = as_of or datetime.now(timezone.utc)
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    @staticmethod
    def _constituents(universe: StockUniverse) -> tuple[UniverseConstituent, ...]:
        if universe.constituents:
            return universe.constituents
        return tuple(UniverseConstituent(ticker, ticker) for ticker in universe.tickers)

    def _one(self, constituent: UniverseConstituent) -> ScanRecord:
        ticker = constituent.canonical_ticker
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                result = self.fundamentals_provider.get_fundamentals(
                    constituent.vendor_ticker, self.as_of, FundamentalsAnalysisMode.CURRENT_ANALYSIS
                )
                if (
                    result.status is FundamentalsStatus.CURRENT_DATA_UNAVAILABLE
                    and result.reason
                    and "failed" in result.reason.lower()
                    and attempt < self.retries
                ):
                    if self.backoff_seconds:
                        self.sleep(self.backoff_seconds * (2 ** attempt))
                    continue
                break
            except ValueError as exc:
                return ScanRecord(ticker, constituent.company_name, constituent.sector, ScanStatus.INVALID_TICKER, None, "INVALID_TICKER", {}, {}, 0, (), f"INVALID_TICKER:{type(exc).__name__}", None)
            except Exception as exc:  # isolated provider failure
                last_error = exc
                if attempt < self.retries and self.backoff_seconds:
                    self.sleep(self.backoff_seconds * (2 ** attempt))
        else:
            return ScanRecord(ticker, constituent.company_name, constituent.sector, ScanStatus.PROVIDER_ERROR, None, "PROVIDER_ERROR", {}, {}, 0, (), f"PROVIDER_ERROR:{type(last_error).__name__ if last_error else 'UNKNOWN'}", None)

        if result.status is not FundamentalsStatus.AVAILABLE or not result.current_history or not result.current_history.annual:
            return ScanRecord(ticker, constituent.company_name, constituent.sector, ScanStatus.DATA_UNAVAILABLE, None, result.status.value, {}, {}, 0, (), result.status.value, result.retrieved_at)
        try:
            record = result.current_history.annual[0]
            metrics = result.current_metrics
            pr = calculate_pr_metrics(
                ticker=ticker, pe=metrics.pe if metrics else None,
                roe_percent=metrics.roe_percent if metrics else None, roe_mode="ttm",
                pb=metrics.pb if metrics else None,
                dividend_payout_percent=metrics.dividend_payout_percent if metrics else None,
                data_as_of=result.as_of.isoformat(), pe_basis=metrics.pe_basis if metrics else None,
                roe_basis=metrics.roe_basis if metrics else None,
                pb_basis=metrics.pb_basis if metrics else None,
                earnings_positive=record.net_income > 0 if record.net_income is not None else None,
            )
            diagnostics = diagnose_financials(record, as_of=result.as_of, pr_metrics=pr)
            piotroski = calculate_piotroski_score(result.current_history.annual)
            qualification = qualify_candidate(record, as_of=result.as_of, pr_metrics=pr, financial_diagnostics=diagnostics, piotroski=piotroski)
        except Exception as exc:
            return ScanRecord(ticker, constituent.company_name, constituent.sector, ScanStatus.QUALIFICATION_ERROR, None, "QUALIFICATION_ERROR", {}, {}, 0, (), f"QUALIFICATION_ERROR:{type(exc).__name__}", result.retrieved_at)
        return ScanRecord(
            ticker, constituent.company_name, constituent.sector, ScanStatus.ANALYZED,
            qualification.status, "ANALYZED",
            {"pr": pr.pr, "status": pr.status.value, "warnings": pr.warnings},
            {"status": piotroski.status, "score": piotroski.score, "partial_score": piotroski.partial_score, "available_component_count": piotroski.available_component_count, "missing_components": tuple(c.name for c in piotroski.components if c.point is None)},
            len(qualification.warnings), qualification.strong_warnings, None, result.retrieved_at,
            result, pr, diagnostics, piotroski, qualification,
            industry=constituent.industry,
        )

    def scan(self, *, limit: int | None = None) -> ScanResult:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        started = datetime.now(timezone.utc)
        universe: StockUniverse = self.universe_provider.get_universe()
        constituents = self._constituents(universe)
        selected = constituents if limit is None else constituents[:limit]
        records = tuple(self._one(item) for item in selected)
        analyzed = [r for r in records if r.scan_status is ScanStatus.ANALYZED]
        qualified = sum(r.qualification_status is QualificationStatus.QUALIFIED for r in analyzed)
        qualified_warn = sum(r.qualification_status is QualificationStatus.QUALIFIED_WITH_WARNINGS for r in analyzed)
        not_qualified = sum(r.qualification_status is QualificationStatus.NOT_QUALIFIED for r in analyzed)
        insufficient = sum(r.qualification_status is QualificationStatus.INSUFFICIENT_DATA for r in analyzed)
        provider_failures = sum(r.scan_status in (ScanStatus.PROVIDER_ERROR, ScanStatus.INVALID_TICKER, ScanStatus.QUALIFICATION_ERROR) for r in records)
        completed = datetime.now(timezone.utc)
        return ScanResult(
            universe=universe.kind.value, universe_size=len(universe.tickers), attempted=len(selected),
            successfully_analyzed=len(analyzed), qualified=qualified, qualified_with_warnings=qualified_warn,
            not_qualified=not_qualified, insufficient_data=insufficient, provider_failures=provider_failures,
            started_at=started, completed_at=completed, records=records,
            status="NO_QUALIFIED_CANDIDATE" if qualified + qualified_warn == 0 else "QUALIFIED_CANDIDATES_AVAILABLE",
            universe_metadata={"source": universe.source, "source_date": universe.source_date, "fetched_at": universe.fetched_at, "name": universe.name},
            qualified_tickers=tuple(record.ticker for record in analyzed if record.qualification_status in (QualificationStatus.QUALIFIED, QualificationStatus.QUALIFIED_WITH_WARNINGS)),
            run_id=uuid4().hex,
            universe_constituents=universe.constituents,
        )
