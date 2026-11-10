"""Offline tests for Signal Engine Slice A contracts and PIT gates."""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest

from investment_agents.signal_engine import (
    AvailabilityGate,
    CalibratedTimingOutput,
    CalibrationMethod,
    CalibrationSummary,
    DatasetStatus,
    FeatureSnapshot,
    ForwardRelativeReturnLabel,
    GateReason,
    Horizon,
    ModelStatus,
    ResearchState,
    SampleIdentity,
    SourceObservation,
    WalkForwardFold,
)


UTC = timezone.utc
CUTOFF = datetime(2025, 1, 2, 21, tzinfo=UTC)
HASH = hashlib.sha256(b"fixture").hexdigest()


def identity(*, ticker: str = "AAPL", benchmark: str | None = "US:ETF:SPY") -> SampleIdentity:
    return SampleIdentity("US:EQUITY:AAPL", ticker, date(2025, 1, 2), Horizon.SESSIONS_20, benchmark)


def observation(**overrides: object) -> SourceObservation:
    values = {
        "instrument_id": "US:EQUITY:AAPL",
        "ticker": "AAPL",
        "observed_at": CUTOFF - timedelta(minutes=5),
        "available_at": CUTOFF - timedelta(minutes=1),
        "source": "fixture",
        "source_version": "v1",
        "provenance_hash": HASH,
        "payload_kind": "DAILY_BAR",
    }
    values.update(overrides)
    return SourceObservation(**values)


def snapshot(*, sample_identity: SampleIdentity | None = None, observations=None, status=DatasetStatus.PIT_VALIDATED) -> FeatureSnapshot:
    return FeatureSnapshot(
        identity=sample_identity or identity(), decision_cutoff=CUTOFF,
        generated_at=CUTOFF + timedelta(minutes=1), feature_set_version="signal-v1-fixture",
        dataset_status=status, features={"return_20d": 0.05, "event_uncertainty": None},
        observations=tuple(observations if observations is not None else (observation(),)),
        snapshot_hash=HASH,
    )


def complete_label(sample_identity: SampleIdentity | None = None) -> ForwardRelativeReturnLabel:
    return ForwardRelativeReturnLabel(
        identity=sample_identity or identity(), entry_session=date(2025, 1, 2),
        exit_session=date(2025, 1, 31), asset_total_return=0.07,
        benchmark_total_return=0.02, excess_return=0.05,
        positive_excess_return=True, label_version="close-to-close-relative-v1", complete=True,
    )


class AvailabilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = AvailabilityGate()

    def test_valid_pit_sample_passes(self) -> None:
        result = self.gate.check_training_sample(snapshot(), complete_label())
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, ())

    def test_unknown_availability_fails_closed(self) -> None:
        result = self.gate.check_snapshot(snapshot(observations=(observation(available_at=None),)))
        self.assertFalse(result.eligible)
        self.assertIn(GateReason.UNKNOWN_AVAILABILITY, result.reasons)

    def test_future_timestamps_fail_closed(self) -> None:
        future = CUTOFF + timedelta(seconds=1)
        result = self.gate.check_snapshot(snapshot(observations=(observation(
            observed_at=future, available_at=future,
        ),)))
        self.assertIn(GateReason.FUTURE_OBSERVATION, result.reasons)
        self.assertIn(GateReason.FUTURE_AVAILABILITY, result.reasons)

    def test_wrong_ticker_fails_closed(self) -> None:
        result = self.gate.check_snapshot(snapshot(observations=(observation(ticker="MSFT"),)))
        self.assertFalse(result.eligible)
        self.assertIn(GateReason.TICKER_MISMATCH, result.reasons)

    def test_missing_benchmark_fails_closed(self) -> None:
        sample = identity(benchmark=None)
        result = self.gate.check_snapshot(snapshot(sample_identity=sample))
        self.assertIn(GateReason.MISSING_BENCHMARK, result.reasons)

    def test_incomplete_forward_horizon_fails_closed(self) -> None:
        label = ForwardRelativeReturnLabel(
            identity=identity(), entry_session=date(2025, 1, 2), exit_session=None,
            asset_total_return=None, benchmark_total_return=None, excess_return=None,
            positive_excess_return=None, label_version="v1", complete=False,
        )
        result = self.gate.check_training_sample(snapshot(), label)
        self.assertIn(GateReason.INCOMPLETE_FORWARD_HORIZON, result.reasons)

    def test_biased_dataset_cannot_pass_pit_gate(self) -> None:
        result = self.gate.check_snapshot(snapshot(status=DatasetStatus.RESEARCH_ONLY_BIASED_DATASET))
        self.assertIn(GateReason.DATASET_NOT_PIT_VALIDATED, result.reasons)


class ContractInvariantTests(unittest.TestCase):
    def test_incomplete_label_cannot_smuggle_partial_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain partial"):
            ForwardRelativeReturnLabel(
                identity=identity(), entry_session=date(2025, 1, 2), exit_session=None,
                asset_total_return=0.1, benchmark_total_return=None, excess_return=None,
                positive_excess_return=None, label_version="v1", complete=False,
            )

    def test_unvalidated_model_cannot_expose_probability(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not expose"):
            CalibratedTimingOutput(
                identity=identity(), model_status=ModelStatus.RESEARCH_ONLY_UNVALIDATED,
                dataset_status=DatasetStatus.RESEARCH_ONLY_BIASED_DATASET,
                calibrated_probability_positive_excess_return=0.68,
                feature_snapshot_hash=HASH, model_bundle_hash=HASH,
                calibration=None, generated_at=CUTOFF,
            )

    def test_validated_probability_requires_calibration_and_pit_data(self) -> None:
        calibration = CalibrationSummary(
            method=CalibrationMethod.SIGMOID, version="v1",
            validation_start=date(2024, 1, 1), validation_end=date(2024, 12, 31),
            sample_count=100, brier_score=0.2, log_loss=0.6, bucket_report_hash=HASH,
        )
        output = CalibratedTimingOutput(
            identity=identity(), model_status=ModelStatus.VALIDATED,
            dataset_status=DatasetStatus.PIT_VALIDATED,
            calibrated_probability_positive_excess_return=0.68,
            feature_snapshot_hash=HASH, model_bundle_hash=HASH,
            calibration=calibration, generated_at=CUTOFF,
        )
        self.assertEqual(output.calibrated_probability_positive_excess_return, 0.68)

        with self.assertRaisesRegex(ValueError, "requires PIT-validated data and calibration"):
            CalibratedTimingOutput(
                identity=identity(), model_status=ModelStatus.VALIDATED,
                dataset_status=DatasetStatus.PIT_VALIDATED,
                calibrated_probability_positive_excess_return=None,
                feature_snapshot_hash=HASH, model_bundle_hash=HASH,
                calibration=None, generated_at=CUTOFF,
            )

    def test_walk_forward_blocks_must_be_disjoint_and_chronological(self) -> None:
        fold = WalkForwardFold(
            "fold-1", date(2020, 1, 1), date(2022, 12, 31),
            date(2023, 1, 1), date(2023, 6, 30),
            date(2023, 7, 1), date(2023, 12, 31), 60, 60,
        )
        self.assertEqual(fold.embargo_sessions, 60)
        with self.assertRaisesRegex(ValueError, "chronological"):
            WalkForwardFold(
                "bad", date(2020, 1, 1), date(2023, 1, 1),
                date(2022, 1, 1), date(2022, 6, 1),
                date(2024, 1, 1), date(2024, 2, 1), 20, 20,
            )

    def test_research_states_are_not_broker_actions(self) -> None:
        values = {state.value for state in ResearchState}
        self.assertFalse(values.intersection({"BUY", "SELL", "PLACE_ORDER", "CANCEL_ORDER"}))

    def test_signal_engine_has_no_broker_imports(self) -> None:
        package = Path(__file__).resolve().parents[1] / "signal_engine"
        forbidden_prefixes = (
            "investment_agents.integrations.ibkr",
            "investment_agents.integrations.moomoo",
        )
        violations: list[str] = []
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(forbidden_prefixes):
                            violations.append(f"{path.name}:{alias.name}")
                if module and module.startswith(forbidden_prefixes):
                    violations.append(f"{path.name}:{module}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
