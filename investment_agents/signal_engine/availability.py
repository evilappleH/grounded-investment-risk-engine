"""Fail-closed point-in-time eligibility checks for signal-engine samples."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .contracts import (
    DatasetStatus,
    FeatureSnapshot,
    ForwardRelativeReturnLabel,
    SampleIdentity,
    SourceObservation,
)


class GateReason(StrEnum):
    UNKNOWN_AVAILABILITY = "UNKNOWN_AVAILABILITY"
    FUTURE_AVAILABILITY = "FUTURE_AVAILABILITY"
    FUTURE_OBSERVATION = "FUTURE_OBSERVATION"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    TICKER_MISMATCH = "TICKER_MISMATCH"
    MISSING_BENCHMARK = "MISSING_BENCHMARK"
    INCOMPLETE_FORWARD_HORIZON = "INCOMPLETE_FORWARD_HORIZON"
    SAMPLE_IDENTITY_MISMATCH = "SAMPLE_IDENTITY_MISMATCH"
    DATASET_NOT_PIT_VALIDATED = "DATASET_NOT_PIT_VALIDATED"
    EMPTY_SOURCE_OBSERVATIONS = "EMPTY_SOURCE_OBSERVATIONS"


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reasons: tuple[GateReason, ...]

    def __post_init__(self) -> None:
        if self.eligible == bool(self.reasons):
            raise ValueError("eligible must be true exactly when reasons are empty")


class AvailabilityGate:
    """Validate identity and availability without guessing missing timestamps."""

    @staticmethod
    def observation_reasons(
        observation: SourceObservation,
        identity: SampleIdentity,
        decision_cutoff: datetime,
    ) -> tuple[GateReason, ...]:
        reasons: list[GateReason] = []
        if observation.instrument_id != identity.instrument_id:
            reasons.append(GateReason.INSTRUMENT_MISMATCH)
        if observation.ticker != identity.ticker:
            reasons.append(GateReason.TICKER_MISMATCH)
        if observation.available_at is None:
            reasons.append(GateReason.UNKNOWN_AVAILABILITY)
        elif observation.available_at > decision_cutoff:
            reasons.append(GateReason.FUTURE_AVAILABILITY)
        if observation.observed_at > decision_cutoff:
            reasons.append(GateReason.FUTURE_OBSERVATION)
        return tuple(dict.fromkeys(reasons))

    def check_snapshot(self, snapshot: FeatureSnapshot) -> GateResult:
        reasons: list[GateReason] = []
        if snapshot.identity.benchmark_id is None:
            reasons.append(GateReason.MISSING_BENCHMARK)
        if snapshot.dataset_status is not DatasetStatus.PIT_VALIDATED:
            reasons.append(GateReason.DATASET_NOT_PIT_VALIDATED)
        if not snapshot.observations:
            reasons.append(GateReason.EMPTY_SOURCE_OBSERVATIONS)
        for observation in snapshot.observations:
            reasons.extend(
                self.observation_reasons(
                    observation,
                    snapshot.identity,
                    snapshot.decision_cutoff,
                )
            )
        unique = tuple(dict.fromkeys(reasons))
        return GateResult(not unique, unique)

    def check_training_sample(
        self,
        snapshot: FeatureSnapshot,
        label: ForwardRelativeReturnLabel,
    ) -> GateResult:
        reasons = list(self.check_snapshot(snapshot).reasons)
        if label.identity != snapshot.identity:
            reasons.append(GateReason.SAMPLE_IDENTITY_MISMATCH)
        if not label.complete:
            reasons.append(GateReason.INCOMPLETE_FORWARD_HORIZON)
        unique = tuple(dict.fromkeys(reasons))
        return GateResult(not unique, unique)
