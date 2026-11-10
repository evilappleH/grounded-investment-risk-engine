from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from investment_agents.portfolio import (
    AUTONOMOUS_PORTFOLIO_ID, AllocationTarget, AutonomousPortfolioRunner, InstrumentId, InstrumentType,
    LedgerPosition, PortfolioCheckpointStore, PortfolioLedger, PortfolioLifecycleState,
    PortfolioPolicyV1, PortfolioRunMode, ResearchEligibility, SMOKE_TEST_POSITION,
    benchmark_snapshot,
    select_research_entries,
)
from investment_agents.research.integrity import VerifiedRunManifest
from investment_agents.research.integrity import create_run_manifest, verify_run_manifest
from investment_agents.risk import Quote


NOW = datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)
MANIFEST = VerifiedRunManifest("run-auto-1", "method-v1", "manifest-1", NOW.isoformat(), 2)


def target(symbol="MSFT", sector="Technology"):
    instrument = InstrumentId(symbol, InstrumentType.EQUITY)
    research = ResearchEligibility(instrument.canonical_id, "run-auto-1", "manifest-1", "method-v1", NOW, NOW, "QUALIFIED", "COMPLETE", "COMPLETE")
    return AllocationTarget(instrument, 0.1, sector, research)


class AutonomousPortfolioTests(unittest.TestCase):
    def test_smoke_position_is_excluded_from_strategy_baseline(self):
        ledger = PortfolioLedger.initialize(initial_cash=10_000, initialized_at=NOW, broker_positions=({"code": "US.AAPL", "quantity": 1},))
        self.assertEqual(ledger.positions, ())
        self.assertEqual(ledger.excluded_broker_positions[0]["exclusion"], SMOKE_TEST_POSITION)
        self.assertEqual(ledger.valuation({})["strategy_nav"], 10_000)
        ring_fenced = PortfolioLedger.initialize(initial_cash=100_000, initialized_at=NOW, broker_positions=({"code": "US.AAPL", "quantity": 1, "broker_cash": 1_000_000},))
        self.assertEqual(ring_fenced.initial_strategy_cash, 100_000)
        self.assertEqual(ring_fenced.strategy_cash, 100_000)

    def test_shadow_mode_never_submits_and_records_risk_result(self):
        ledger = PortfolioLedger.initialize(initial_cash=10_000, initialized_at=NOW)
        runner = AutonomousPortfolioRunner()
        quotes = {"US:EQUITY:MSFT": Quote("US:EQUITY:MSFT", 100.0, NOW)}
        result = runner.shadow_cycle(run_id="shadow-run-1", ledger=ledger, candidates=(target(),), quotes=quotes, verified_manifest=MANIFEST, now=NOW)
        self.assertEqual(result["mode"], PortfolioRunMode.SHADOW_MODE.value)
        self.assertFalse(result["would_submit"])
        self.assertEqual(result["broker_calls"], 0)

    def test_real_or_provider_failure_blocks_shadow_cycle(self):
        ledger = PortfolioLedger.initialize(initial_cash=10_000, initialized_at=NOW)
        result = AutonomousPortfolioRunner().shadow_cycle(run_id="shadow-run-2", ledger=ledger, candidates=(target(),), quotes={}, verified_manifest=MANIFEST, now=NOW, provider_failures=("YAHOO_TIMEOUT",))
        self.assertEqual(result["status"], "BLOCKED_PROVIDER_FAILURE")

    def test_policy_does_not_sell_on_price_decline_without_thesis_change(self):
        ledger = PortfolioLedger(AUTONOMOUS_PORTFOLIO_ID, NOW, 9_000, 9_000, (LedgerPosition(InstrumentId("MSFT", InstrumentType.EQUITY), 10, 100, "Technology"),))
        policy = PortfolioPolicyV1()
        state = ledger.strategy_state({"US:EQUITY:MSFT": 80}, as_of=NOW)
        self.assertTrue(policy.targets((target(),), state=state, now=NOW, thesis_invalidated=()))
        self.assertTrue(policy.thesis_review_required(price_changed=True, thesis_invalidated=False, material_event=False, evidence_failed=False) is False)

    def test_thesis_invalidation_can_trigger_review(self):
        self.assertTrue(PortfolioPolicyV1().thesis_review_required(price_changed=False, thesis_invalidated=True, material_event=False, evidence_failed=False))

    def test_checkpoint_duplicate_run_and_restart(self):
        with TemporaryDirectory() as temp:
            store = PortfolioCheckpointStore(Path(temp))
            store.acquire("run-lock-1")
            with self.assertRaisesRegex(RuntimeError, "DUPLICATE_RUN"):
                store.acquire("run-lock-2")
            store.release()
            checkpoint = store.load()
            self.assertIsNone(checkpoint)

    def test_benchmarks_are_separate_and_missing_values_are_not_fabricated(self):
        result = benchmark_snapshot(110, 102, {"SPY": 100, "VT": 100})
        self.assertAlmostEqual(result["SPY"], 0.1)
        self.assertAlmostEqual(result["VT"], 0.02)
        self.assertIsNone(benchmark_snapshot(None, 102, {"SPY": 100, "VT": 100})["SPY"])

    def test_manifest_corruption_and_missing_commit_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifact.json"
            artifact.write_text("{}", encoding="utf-8")
            _, commit = create_run_manifest(root=root, run_id="manifest-run-1", methodology_version="m1", artifacts={"artifact": artifact})
            verified = verify_run_manifest(root=root, run_id="manifest-run-1")
            self.assertEqual(verified.run_id, "manifest-run-1")
            artifact.write_text("{corrupt}", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_run_manifest(root=root, run_id="manifest-run-1")
            commit.unlink()
            with self.assertRaises(ValueError):
                verify_run_manifest(root=root, run_id="manifest-run-1")

    def test_budget_selects_after_ranking_not_universe_order(self):
        entries = (
            SimpleNamespace(ticker="MMM", lane="QUALIFIED_UNRANKED", pareto_front=None),
            SimpleNamespace(ticker="ACN", lane="PARETO_RANKED", pareto_front=0),
            SimpleNamespace(ticker="AOS", lane="QUALIFIED_UNRANKED", pareto_front=None),
        )
        selected = select_research_entries(entries, 1)
        self.assertEqual(selected[0].ticker, "ACN")


if __name__ == "__main__":
    unittest.main()
