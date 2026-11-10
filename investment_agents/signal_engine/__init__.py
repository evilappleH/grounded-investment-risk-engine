"""Risk-first, broker-independent signal-engine contracts.

This package is intentionally safe to import in offline research and test
processes. It does not import market-data clients, LLM runtimes, portfolio
execution, or broker adapters.
"""

from .availability import AvailabilityGate, GateReason, GateResult
from .contracts import (
    CalibratedTimingOutput,
    CalibrationMethod,
    CalibrationSummary,
    DatasetStatus,
    FeatureSnapshot,
    ForwardRelativeReturnLabel,
    Horizon,
    ModelStatus,
    ResearchState,
    SampleIdentity,
    SourceObservation,
    WalkForwardFold,
)
from .features import AdjustedDailyBar, PriceVolumeFeatureBuilder
from .labels import LABEL_VERSION, build_relative_return_labels

__all__ = [
    "AvailabilityGate",
    "AdjustedDailyBar",
    "CalibratedTimingOutput",
    "CalibrationMethod",
    "CalibrationSummary",
    "DatasetStatus",
    "FeatureSnapshot",
    "ForwardRelativeReturnLabel",
    "GateReason",
    "GateResult",
    "Horizon",
    "ModelStatus",
    "PriceVolumeFeatureBuilder",
    "ResearchState",
    "SampleIdentity",
    "SourceObservation",
    "WalkForwardFold",
    "LABEL_VERSION",
    "build_relative_return_labels",
]
