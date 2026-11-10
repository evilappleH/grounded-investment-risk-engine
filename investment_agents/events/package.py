"""Build and persist an event evidence package with isolated providers."""

from __future__ import annotations
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .models import EventEvidence, EventEvidencePackage, EvidenceSourceClass
from .macro import MacroContextPackage
from .sec import SECEdgarAdapter
from .bridges import MacroEvidenceAdapter, SentimentEvidenceAdapter, TradingAgentsNewsAdapter
from investment_agents.path_safety import redact_secrets, safe_artifact_path, sanitized_provider_error, validate_artifact_ticker, validate_run_id


def collect_event_evidence(
    ticker: str,
    *,
    run_id: str | None = None,
    lookback_days: int = 30,
    end: date | None = None,
    sec: Any | None = None,
    news: Any | None = None,
    macro: Any | None = None,
    macro_context: MacroContextPackage | None = None,
    sentiment: Any | None = None,
) -> EventEvidencePackage:
    if lookback_days <= 0: raise ValueError("lookback_days must be positive")
    lookback_end = end or date.today(); lookback_start = lookback_end - timedelta(days=lookback_days)
    failures: dict[str, Any] = {}; warnings: list[str] = []
    primary = (); news_items = (); macro_items = (); sentiment_items = ()
    try: primary = (sec or SECEdgarAdapter()).fetch_filings(ticker, start=lookback_start, end=lookback_end)
    except Exception as exc: failures["sec"] = sanitized_provider_error(exc)
    try: news_items, warning = (news or TradingAgentsNewsAdapter()).fetch(ticker, start=lookback_start, end=lookback_end); warnings.extend([warning] if warning else [])
    except Exception as exc: failures["news"] = sanitized_provider_error(exc)
    macro_reference = None
    try:
        if macro_context is not None:
            macro_items = tuple(EventEvidence(
                evidence_id=f"fred:{item.series_id}:{item.observation_date.isoformat()}",
                ticker=ticker.upper(), evidence_type="MACRO_OBSERVATION",
                source_class=EvidenceSourceClass.MACRO, source=item.source,
                title=item.series_id, observed_at=item.observation_date,
                fetched_at=item.fetched_at,
                reference=f"fred:{item.series_id}:{item.observation_date.isoformat()}",
                raw_metadata={"value": item.value, "units": item.units, "frequency": item.frequency},
                provenance={"macro_context_run_id": macro_context.run_id, "analysis_mode": macro_context.analysis_mode},
                analysis_mode=macro_context.analysis_mode,
            ) for item in macro_context.observations)
            macro_reference = {"run_id": macro_context.run_id, "fetched_at": macro_context.fetched_at,
                               "analysis_mode": macro_context.analysis_mode, "pit_safe": macro_context.pit_safe,
                               "series_metadata": macro_context.series_metadata}
            failures.update({f"fred:{key}": value for key, value in macro_context.source_failures.items()})
        else:
            macro_items, warning = (macro or MacroEvidenceAdapter()).fetch(end=lookback_end, lookback_days=lookback_days)
            macro_items = tuple(replace(item, ticker=ticker.upper()) for item in macro_items)
            warnings.extend([warning] if warning else [])
    except Exception as exc: failures["macro"] = sanitized_provider_error(exc)
    try: sentiment_items, warning = (sentiment or SentimentEvidenceAdapter()).fetch(ticker, start=lookback_start, end=lookback_end); warnings.extend([warning] if warning else [])
    except Exception as exc: failures["sentiment"] = sanitized_provider_error(exc)
    if not primary and "sec" not in failures: warnings.append("NO_EVIDENCE_FOUND:sec")
    if not news_items and "news" not in failures: warnings.append("NO_EVIDENCE_FOUND:news")
    if not macro_items and "macro" not in failures: warnings.append("NO_EVIDENCE_FOUND:macro")
    if not sentiment_items and "sentiment" not in failures: warnings.append("NO_EVIDENCE_FOUND:sentiment")
    return EventEvidencePackage(ticker=ticker.upper(), run_id=run_id, generated_at=datetime.now(timezone.utc), lookback_start=lookback_start, lookback_end=lookback_end, primary_corporate_evidence=tuple(primary), news_evidence=tuple(news_items), macro_evidence=tuple(macro_items), sentiment_evidence=tuple(sentiment_items), source_failures=failures, source_warnings=tuple(dict.fromkeys(warnings)), provenance={"analysis_mode": "CURRENT_ANALYSIS", "pit_safe": False, "lookback_days": lookback_days}, macro_context_reference=macro_reference)


def persist_event_package(package: EventEvidencePackage, *, root: Path) -> Path:
    if not package.run_id: raise ValueError("run_id is required for persistence")
    run_id = validate_run_id(package.run_id); ticker = validate_artifact_ticker(package.ticker)
    path = safe_artifact_path(root, "research_runs", run_id, "events", f"{ticker}_event_evidence.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    def safe(value: Any) -> Any:
        if isinstance(value, dict): return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)): return [safe(v) for v in value]
        if hasattr(value, "value"): return value.value
        if hasattr(value, "isoformat"): return value.isoformat()
        return value
    try:
        with path.open("x", encoding="utf-8") as handle: json.dump(redact_secrets(safe(asdict(package))), handle, ensure_ascii=True, indent=2, sort_keys=True)
    except FileExistsError as exc: raise FileExistsError(f"refusing to overwrite event evidence: {path}") from exc
    return path


def persist_macro_context(context: MacroContextPackage, *, root: Path) -> Path:
    """Persist one run-level macro fetch without secrets or silent overwrite."""
    if not context.run_id:
        raise ValueError("run_id is required for persistence")
    run_id = validate_run_id(context.run_id)
    path = safe_artifact_path(root, "research_runs", run_id, "macro", "macro_context.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    def safe(value: Any) -> Any:
        if isinstance(value, dict): return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)): return [safe(v) for v in value]
        if hasattr(value, "value"): return value.value
        if hasattr(value, "isoformat"): return value.isoformat()
        return value
    try:
        with path.open("x", encoding="utf-8") as handle: json.dump(redact_secrets(safe(asdict(context))), handle, ensure_ascii=True, indent=2, sort_keys=True)
    except FileExistsError as exc: raise FileExistsError(f"refusing to overwrite macro context: {path}") from exc
    return path
