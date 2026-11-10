"""Vendor-neutral event evidence models; no score or investment conclusion."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Mapping
from investment_agents.path_safety import sanitized_failure_record


class EvidenceSourceClass(StrEnum):
    PRIMARY_CORPORATE = "PRIMARY_CORPORATE"
    NEWS = "NEWS"
    MACRO = "MACRO"
    SENTIMENT = "SENTIMENT"


class EvidenceMateriality(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    POTENTIALLY_MATERIAL = "POTENTIALLY_MATERIAL"
    MATERIALITY_UNKNOWN = "MATERIALITY_UNKNOWN"


@dataclass(frozen=True)
class EventEvidence:
    evidence_id: str
    ticker: str
    evidence_type: str
    source_class: EvidenceSourceClass
    source: str
    title: str | None = None
    observed_at: datetime | date | None = None
    published_at: datetime | date | None = None
    fetched_at: datetime | None = None
    reference: str | None = None
    summary: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    analysis_mode: str = "CURRENT_ANALYSIS"
    materiality: EvidenceMateriality = EvidenceMateriality.MATERIALITY_UNKNOWN
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SentimentEvidence:
    ticker: str
    source: str
    observed_at: datetime | date | None
    fetched_at: datetime | None
    text_or_reference: str | None
    structured_metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventEvidencePackage:
    ticker: str
    run_id: str | None
    generated_at: datetime
    lookback_start: date
    lookback_end: date
    primary_corporate_evidence: tuple[EventEvidence, ...] = ()
    news_evidence: tuple[EventEvidence, ...] = ()
    macro_evidence: tuple[EventEvidence, ...] = ()
    sentiment_evidence: tuple[SentimentEvidence, ...] = ()
    source_failures: Mapping[str, Any] = field(default_factory=dict)
    source_warnings: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    macro_context_reference: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_failures", {
            str(key): sanitized_failure_record(value)
            for key, value in self.source_failures.items()
        })

    @property
    def evidence_count(self) -> int:
        return len(self.primary_corporate_evidence) + len(self.news_evidence) + len(self.macro_evidence) + len(self.sentiment_evidence)
