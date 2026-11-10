"""Deterministic portfolio, risk-gate, and simulator fault-injection tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from investment_agents.integrations import (
    DuplicateSubmissionError, LocalBrokerSimulator, SimulatedDisconnect,
    SimulatedOrderRequest, SimulatorOrderStatus,
)
from investment_agents.portfolio import (
    POLICY_CLASSIFICATION, AllocationTarget, Holding, InstrumentId, InstrumentType,
    PortfolioEngine, PortfolioState, ProposalStatus, ResearchEligibility,
)
from investment_agents.research import VerifiedRunManifest
from investment_agents.risk import KillSwitch, Quote, RiskConfig, RiskContext, RiskGate, RiskStatus


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
MANIFEST = VerifiedRunManifest(
    run_id="research-run", methodology_version="step6c-v2-piotroski-average-assets",
    manifest_hash="a" * 64, created_at=NOW.isoformat(), artifact_count=4,
)
EQUITY = InstrumentId("AAPL", InstrumentType.EQUITY)


def evidence(**changes):
    values = dict(
        instrument_id=EQUITY.canonical_id, source_run_id=MANIFEST.run_id,
        source_manifest_hash=MANIFEST.manifest_hash,
        methodology_version=MANIFEST.methodology_version,
        generated_at=NOW - timedelta(minutes=2), data_effective_at=NOW - timedelta(minutes=3),
        qualification_status="QUALIFIED_WITH_WARNINGS", piotroski_status="COMPLETE",
        evidence_status="COMPLETE",
    )
    values.update(changes)
    return ResearchEligibility(**values)


def state(*, settled=1000.0, positions=()):
    return PortfolioState(NOW - timedelta(seconds=5), 1000.0, settled, tuple(positions))


def proposal(weight=.10, *, research=None, current_state=None, proposal_id="decision-1"):
    return PortfolioEngine().propose(
        current_state or state(),
        (AllocationTarget(EQUITY, weight, "Technology", research or evidence()),),
        verified_manifest=MANIFEST, proposal_id=proposal_id, decision_time=NOW,
    )


def risk_decision(item=None, current_state=None, quote=None, **kwargs):
    item = item or proposal(current_state=current_state)
    current_state = current_state or state()
    quote = quote or Quote(EQUITY.canonical_id, 100.0, NOW - timedelta(seconds=1))
    return RiskGate().evaluate(item, current_state, {EQUITY.canonical_id: quote}, checked_at=NOW, **kwargs)


class PortfolioTests(unittest.TestCase):
    def test_structured_cash_long_only_proposal_and_sector_exposure(self):
        result = proposal()
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        self.assertAlmostEqual(result.cash_weight, .9)
        self.assertAlmostEqual(result.cash_notional, 900)
        self.assertEqual(result.sector_exposures, {"Technology": .1})
        self.assertEqual(result.allocations[0].canonical_instrument_id, "US:EQUITY:AAPL")
        self.assertEqual(result.policy_classification, POLICY_CLASSIFICATION)
        self.assertFalse(hasattr(result, "broker_order"))

    def test_duplicate_positions_and_targets_fail_closed(self):
        holding = Holding(EQUITY, 1, 100, "Technology", NOW)
        self.assertEqual(state(positions=(holding,)).positions_by_id[EQUITY.canonical_id], holding)
        with self.assertRaisesRegex(ValueError, "duplicate holding"):
            state(positions=(holding, holding))
        target = AllocationTarget(EQUITY, .05, "Technology", evidence())
        result = PortfolioEngine().propose(state(), (target, target), verified_manifest=MANIFEST, proposal_id="dup", decision_time=NOW)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertIn("DUPLICATE_TARGET_INSTRUMENT", result.rejection_reasons)

    def test_ineligible_research_never_drives_allocation(self):
        cases = (
            evidence(piotroski_status="PARTIAL"),
            evidence(evidence_status="PARTIAL"),
            evidence(provider_failures=("YFINANCE",)),
            evidence(unverified_model_claims=("claim",)),
            evidence(data_effective_at=NOW - timedelta(days=2)),
            evidence(source_manifest_hash="b" * 64),
        )
        for index, item in enumerate(cases):
            result = proposal(research=item, proposal_id=f"bad-{index}")
            self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertTrue(any("INCOMPLETE_RESEARCH_EVIDENCE" in reason for reason in proposal(research=cases[0], proposal_id="partial").rejection_reasons))
        self.assertTrue(any("UNVERIFIED_MODEL_CLAIM" in reason for reason in proposal(research=cases[3], proposal_id="unverified").rejection_reasons))

    def test_balanced_partial_piotroski_is_not_universal_gate(self):
        result = proposal(research=evidence(piotroski_status="PARTIAL", research_lane="BALANCED"), proposal_id="balanced-partial")
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)

    def test_strict_partial_piotroski_remains_blocked(self):
        result = proposal(research=evidence(piotroski_status="PARTIAL", research_lane="STRICT"), proposal_id="strict-partial")
        self.assertEqual(result.status, ProposalStatus.REJECTED)

    def test_only_supported_usd_equity_or_etf_identifiers_exist(self):
        self.assertEqual(InstrumentId("spy", InstrumentType.ETF).canonical_id, "US:ETF:SPY")
        with self.assertRaises(ValueError):
            InstrumentId("AAPL", InstrumentType.EQUITY, "EUR")
        with self.assertRaises(ValueError):
            InstrumentId("AAPL", "OPTION")


class RiskTests(unittest.TestCase):
    def test_approved_risk_decision_is_deterministic_and_cash_only(self):
        result = risk_decision()
        again = risk_decision()
        self.assertEqual(result.status, RiskStatus.APPROVED)
        self.assertEqual(result.decision_hash, again.decision_hash)
        self.assertEqual(result.trades[0].side, "BUY")
        self.assertAlmostEqual(result.trades[0].quantity, 1.0)
        self.assertAlmostEqual(result.projected_cash, 900.0)
        self.assertEqual(result.policy_classification, POLICY_CLASSIFICATION)

    def test_risk_limits_staleness_provider_cash_kill_and_live_context_reject(self):
        high = proposal(.30, proposal_id="high")
        result = RiskGate().evaluate(
            high, state(settled=50),
            {EQUITY.canonical_id: Quote(EQUITY.canonical_id, 100, NOW - timedelta(minutes=2), "PROVIDER_ERROR")},
            context=RiskContext(environment="LIVE", account_identifier="DU123", daily_turnover_fraction=.19),
            kill_switch=KillSwitch(True, "test"), checked_at=NOW,
        )
        joined = " ".join(result.rejection_reasons)
        for code in (
            "LIVE_OR_EXTERNAL_ACCOUNT_FORBIDDEN", "KILL_SWITCH_ACTIVE", "QUOTE_PROVIDER_FAILURE",
            "STALE_OR_FUTURE_QUOTE", "MAX_SINGLE_POSITION_EXCEEDED", "MAX_SECTOR_EXPOSURE_EXCEEDED",
            "SETTLED_CASH_EXCEEDED", "MAX_DAILY_TURNOVER_EXCEEDED",
        ):
            self.assertIn(code, joined)
        self.assertEqual(result.status, RiskStatus.REJECTED)

    def test_duplicate_decision_id_and_hash_are_rejected(self):
        first = risk_decision()
        duplicate = risk_decision(context=RiskContext(seen_decision_ids=(first.decision_id,), seen_decision_hashes=(first.decision_hash,)))
        self.assertIn("DUPLICATE_DECISION_ID", duplicate.rejection_reasons)
        self.assertIn("DUPLICATE_DECISION_HASH", duplicate.rejection_reasons)
        self.assertEqual(duplicate.status, RiskStatus.REJECTED)

    def test_stale_decision_and_tampered_proposal_hash_are_rejected(self):
        old_time = NOW - timedelta(minutes=10)
        old_research = evidence(generated_at=old_time - timedelta(minutes=1), data_effective_at=old_time - timedelta(minutes=2))
        old = PortfolioEngine().propose(
            state(), (AllocationTarget(EQUITY, .1, "Technology", old_research),),
            verified_manifest=MANIFEST, proposal_id="old", decision_time=old_time,
        )
        stale = RiskGate().evaluate(old, state(), {EQUITY.canonical_id: Quote(EQUITY.canonical_id, 100, NOW)}, checked_at=NOW)
        self.assertIn("STALE_OR_FUTURE_DECISION", stale.rejection_reasons)
        tampered = replace(proposal(), cash_notional=1)
        rejected = risk_decision(tampered)
        self.assertIn("PROPOSAL_HASH_MISMATCH", rejected.rejection_reasons)

    def test_existing_position_can_only_be_sold_not_shorted(self):
        holding = Holding(EQUITY, 2, 100, "Technology", NOW)
        current = state(positions=(holding,))
        target = proposal(.10, current_state=current, proposal_id="reduce")
        result = risk_decision(target, current)
        self.assertEqual(result.status, RiskStatus.APPROVED)
        self.assertEqual(result.trades[0].side, "SELL")
        self.assertLessEqual(result.trades[0].quantity, holding.quantity)


class SimulatorTests(unittest.TestCase):
    def approved(self):
        return risk_decision()

    def request(self, decision, order_id="order-1", **changes):
        request = SimulatedOrderRequest.from_risk_trade(decision.trades[0], decision, client_order_id=order_id)
        return replace(request, **changes)

    def test_submit_ack_partial_fill_fill_and_restart_recovery(self):
        decision = self.approved()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "orders.jsonl"
            simulator = LocalBrokerSimulator(path)
            request = self.request(decision)
            self.assertEqual(simulator.submit(request, decision).status, SimulatorOrderStatus.SUBMITTED)
            self.assertEqual(simulator.acknowledge("order-1").status, SimulatorOrderStatus.ACKNOWLEDGED)
            self.assertEqual(simulator.partial_fill("order-1", .4, 101).status, SimulatorOrderStatus.PARTIALLY_FILLED)
            final = simulator.fill("order-1", 102)
            self.assertEqual(final.status, SimulatorOrderStatus.FILLED)
            self.assertAlmostEqual(final.filled_quantity, 1.0)
            recovered = LocalBrokerSimulator(path)
            self.assertEqual(recovered.orders["order-1"].status, SimulatorOrderStatus.FILLED)
            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            for field in ("source_manifest_hash", "decision_hash", "risk_result", "quote", "request", "request_hash", "event_hash"):
                self.assertIn(field, event)

    def test_duplicate_submission_is_idempotent_and_conflict_rejected(self):
        decision = self.approved()
        with TemporaryDirectory() as temp:
            simulator = LocalBrokerSimulator(Path(temp) / "orders.jsonl")
            request = self.request(decision)
            simulator.submit(request, decision)
            duplicate = simulator.submit(request, decision)
            self.assertEqual(duplicate.status, SimulatorOrderStatus.SUBMITTED)
            conflicting = replace(request, created_at=request.created_at + timedelta(seconds=1))
            with self.assertRaises(DuplicateSubmissionError):
                simulator.submit(conflicting, decision)
            events = [json.loads(line)["event_type"] for line in simulator.audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertIn("DUPLICATE_SUBMISSION", events)
            self.assertIn("DUPLICATE_CONFLICT", events)

    def test_uncertain_ack_reconnect_cancel_reject_and_disconnect_faults(self):
        decision = self.approved()
        with TemporaryDirectory() as temp:
            simulator = LocalBrokerSimulator(Path(temp) / "orders.jsonl")
            uncertain = simulator.submit(self.request(decision, "uncertain"), decision, fault="UNCERTAIN_ACKNOWLEDGEMENT")
            self.assertEqual(uncertain.status, SimulatorOrderStatus.UNCERTAIN_ACKNOWLEDGEMENT)
            reconciled = simulator.reconnect({"uncertain": "ACKNOWLEDGED"})[0]
            self.assertEqual(reconciled.status, SimulatorOrderStatus.ACKNOWLEDGED)
            self.assertEqual(simulator.cancel("uncertain").status, SimulatorOrderStatus.CANCELLED)
            rejected = simulator.submit(self.request(decision, "rejected"), decision, fault="REJECT")
            self.assertEqual(rejected.status, SimulatorOrderStatus.REJECTED)
            with self.assertRaises(SimulatedDisconnect):
                simulator.submit(self.request(decision, "disconnected"), decision, fault="DISCONNECT_AFTER_SUBMIT")
            recovered = LocalBrokerSimulator(simulator.audit_path)
            self.assertEqual(recovered.orders["disconnected"].status, SimulatorOrderStatus.SUBMITTED)

    def test_audit_chain_tamper_is_detected(self):
        decision = self.approved()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "orders.jsonl"
            simulator = LocalBrokerSimulator(path)
            simulator.submit(self.request(decision), decision)
            text = path.read_text(encoding="utf-8").replace('"risk_result":"APPROVED"', '"risk_result":"REJECTED"', 1)
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity failure"):
                LocalBrokerSimulator(path)

    def test_tampered_risk_decision_is_refused(self):
        decision = self.approved()
        request = self.request(decision)
        tampered = replace(decision, projected_cash=0)
        with TemporaryDirectory() as temp:
            simulator = LocalBrokerSimulator(Path(temp) / "orders.jsonl")
            with self.assertRaisesRegex(ValueError, "approved risk trade"):
                simulator.submit(request, tampered)
            self.assertFalse(simulator.audit_path.exists())


if __name__ == "__main__":
    unittest.main()
