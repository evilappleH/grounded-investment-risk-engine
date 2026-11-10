"""Immutable contracts for the risk-first signal engine.

These types carry data and enforce local invariants. They do not fetch data,
fit models, decide allocations, authorize execution, or call a broker. Unknown
or unvalidated information stays explicit instead of becoming a neutral value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum, StrEnum
from math import isfinite

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


class DatasetStatus(StrEnum):
    """Publication eligibility of a dataset, not model quality."""

    BLOCKED_PIT = "BLOCKED_PIT"
    RESEARCH_ONLY_BIASED_DATASET = "RESEARCH_ONLY_BIASED_DATASET"
    PIT_VALIDATED = "PIT_VALIDATED"


class ModelStatus(StrEnum):
    """Whether a probability may be shown to a user."""

    NOT_EVALUATED = "NOT_EVALUATED"
    BLOCKED_DATA = "BLOCKED_DATA"
    BLOCKED_CALIBRATION = "BLOCKED_CALIBRATION"
    RESEARCH_ONLY_UNVALIDATED = "RESEARCH_ONLY_UNVALIDATED"
    VALIDATED = "VALIDATED"


class Horizon(IntEnum):
    SESSIONS_20 = 20
    SESSIONS_60 = 60


class CalibrationMethod(StrEnum):
    NONE = "NONE"
    SIGMOID = "SIGMOID"
    ISOTONIC = "ISOTONIC"


class ResearchState(StrEnum):
    """Research states only; none is an order instruction."""

    ENTRY_RESEARCH_CANDIDATE = "ENTRY_RESEARCH_CANDIDATE"
    WATCH = "WATCH"
    HOLD = "HOLD"
    ADD_RESEARCH_CANDIDATE = "ADD_RESEARCH_CANDIDATE"
    NO_ADD = "NO_ADD"
    REDUCE_REVIEW = "REDUCE_REVIEW"
    SELL_REVIEW = "SELL_REVIEW"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_hash(value: str, field_name: str) -> None:
    if not _HEX_64.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _validate_ticker(value: str) -> None:
    if not _TICKER.fullmatch(value):
        raise ValueError("ticker must be a normalized US-equities symbol")


@dataclass(frozen=True)
class SampleIdentity:
    """Stable identity for one instrument/as-of/horizon training row."""

    instrument_id: str
    ticker: str
    as_of_session: date
    horizon: Horizon
    benchmark_id: str | None

    def __post_init__(self) -> None:
        _validate_ticker(self.ticker)
        if not self.instrument_id or not self.instrument_id.endswith(f":{self.ticker}"):
            raise ValueError("instrument_id must end with the normalized ticker")
        object.__setattr__(self, "horizon", Horizon(self.horizon))
        if self.benchmark_id is not None and not self.benchmark_id.strip():
            raise ValueError("benchmark_id cannot be blank")


@dataclass(frozen=True)
class SourceObservation:
    """One source item together with when it became usable."""

    instrument_id: str
    ticker: str
    observed_at: datetime
    available_at: datetime | None
    source: str
    source_version: str
    provenance_hash: str
    payload_kind: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_ticker(self.ticker)
        _require_aware(self.observed_at, "observed_at")
        if self.available_at is not None:
            _require_aware(self.available_at, "available_at")
        for name in ("instrument_id", "source", "source_version", "payload_kind"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _validate_hash(self.provenance_hash, "provenance_hash")


@dataclass(frozen=True)
class FeatureSnapshot:
    """Versioned numerical inputs visible at one decision cutoff."""

    identity: SampleIdentity
    decision_cutoff: datetime
    generated_at: datetime
    feature_set_version: str
    dataset_status: DatasetStatus
    features: Mapping[str, float | None]
    observations: tuple[SourceObservation, ...]
    snapshot_hash: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.decision_cutoff, "decision_cutoff")
        _require_aware(self.generated_at, "generated_at")
        object.__setattr__(self, "dataset_status", DatasetStatus(self.dataset_status))
        if not self.feature_set_version.strip():
            raise ValueError("feature_set_version is required")
        if not self.features:
            raise ValueError("features cannot be empty")
        for name, value in self.features.items():
            if not str(name).strip():
                raise ValueError("feature names cannot be blank")
            if value is not None and (isinstance(value, bool) or not isfinite(float(value))):
                raise ValueError(f"feature {name!r} must be finite or None")
        _validate_hash(self.snapshot_hash, "snapshot_hash")


@dataclass(frozen=True)
class ForwardRelativeReturnLabel:
    """Observed forward excess-return label; never an input feature."""

    identity: SampleIdentity
    entry_session: date
    exit_session: date | None
    asset_total_return: float | None
    benchmark_total_return: float | None
    excess_return: float | None
    positive_excess_return: bool | None
    label_version: str
    complete: bool

    def __post_init__(self) -> None:
        if not self.label_version.strip():
            raise ValueError("label_version is required")
        if self.entry_session != self.identity.as_of_session:
            raise ValueError("entry_session must equal the sample as_of_session")
        values = (self.asset_total_return, self.benchmark_total_return, self.excess_return)
        if any(value is not None and not isfinite(float(value)) for value in values):
            raise ValueError("label returns must be finite or None")
        if self.complete:
            if self.identity.benchmark_id is None:
                raise ValueError("complete relative-return label requires a benchmark")
            if self.exit_session is None or self.exit_session <= self.entry_session:
                raise ValueError("complete label requires an exit session after entry")
            if any(value is None for value in values) or self.positive_excess_return is None:
                raise ValueError("complete label requires all return fields and class target")
            expected_excess = float(self.asset_total_return) - float(self.benchmark_total_return)
            if abs(expected_excess - float(self.excess_return)) > 1e-12:
                raise ValueError("excess_return does not match asset minus benchmark return")
            if self.positive_excess_return != (float(self.excess_return) > 0):
                raise ValueError("positive_excess_return does not match excess_return")
        elif any(value is not None for value in (*values, self.positive_excess_return)):
            raise ValueError("incomplete labels must not contain partial target values")


@dataclass(frozen=True)
class WalkForwardFold:
    """Chronological train/calibration/test boundaries."""

    fold_id: str
    train_start: date
    train_end: date
    calibration_start: date
    calibration_end: date
    test_start: date
    test_end: date
    purge_sessions: int
    embargo_sessions: int

    def __post_init__(self) -> None:
        if not self.fold_id.strip():
            raise ValueError("fold_id is required")
        if not (
            self.train_start <= self.train_end < self.calibration_start
            <= self.calibration_end < self.test_start <= self.test_end
        ):
            raise ValueError("walk-forward blocks must be chronological and disjoint")
        if self.purge_sessions < 0 or self.embargo_sessions < 0:
            raise ValueError("purge and embargo sessions cannot be negative")


@dataclass(frozen=True)
class CalibrationSummary:
    method: CalibrationMethod
    version: str
    validation_start: date
    validation_end: date
    sample_count: int
    brier_score: float
    log_loss: float
    bucket_report_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", CalibrationMethod(self.method))
        if self.method is CalibrationMethod.NONE:
            raise ValueError("calibration summary requires an applied method")
        if not self.version.strip():
            raise ValueError("calibration version is required")
        if self.validation_end < self.validation_start or self.sample_count <= 0:
            raise ValueError("calibration window and sample count must be valid")
        if not isfinite(self.brier_score) or not 0 <= self.brier_score <= 1:
            raise ValueError("brier_score must be within [0, 1]")
        if not isfinite(self.log_loss) or self.log_loss < 0:
            raise ValueError("log_loss must be finite and non-negative")
        _validate_hash(self.bucket_report_hash, "bucket_report_hash")


@dataclass(frozen=True)
class CalibratedTimingOutput:
    """A displayable probability exists only for a validated model."""

    identity: SampleIdentity
    model_status: ModelStatus
    dataset_status: DatasetStatus
    calibrated_probability_positive_excess_return: float | None
    feature_snapshot_hash: str
    model_bundle_hash: str
    calibration: CalibrationSummary | None
    generated_at: datetime
    data_limitations: tuple[str, ...] = ()
    top_deterministic_drivers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_status", ModelStatus(self.model_status))
        object.__setattr__(self, "dataset_status", DatasetStatus(self.dataset_status))
        _require_aware(self.generated_at, "generated_at")
        _validate_hash(self.feature_snapshot_hash, "feature_snapshot_hash")
        _validate_hash(self.model_bundle_hash, "model_bundle_hash")
        probability = self.calibrated_probability_positive_excess_return
        displayable = (
            self.model_status is ModelStatus.VALIDATED
            and self.dataset_status is DatasetStatus.PIT_VALIDATED
            and self.calibration is not None
        )
        if self.model_status is ModelStatus.VALIDATED and not displayable:
            raise ValueError("VALIDATED status requires PIT-validated data and calibration")
        if displayable:
            if probability is None or not isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("validated output requires a calibrated probability within [0, 1]")
        elif probability is not None:
            raise ValueError("unvalidated or PIT-blocked output must not expose a probability")
