"""Adversarial IBKR Paper-only guard, adapter, and recovery tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from investment_agents.integrations.ibkr import (
    AccountSummary, EXECUTION_POLICY_CLASSIFICATION,
    ExecutionSafetyPolicy, IBKRConnectionConfig, IBKRPaperAdapter, IBKRQuote,
    InMemoryIBKRTransport, IntentState, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN, OrderSide,
    PaperKillSwitchState, PaperOrderIntent, PaperSafetyError, ResolvedContract,
    SessionState,
)
from investment_agents.integrations.ibkr.official import OfficialIBAPIUnavailable, OfficialIBKRTransport
from investment_agents.portfolio import (
    AllocationTarget, InstrumentId, InstrumentType, PortfolioEngine,
    PortfolioState, ResearchEligibility,
)
from investment_agents.research import create_run_manifest, verify_run_manifest
from investment_agents.risk import (
    Quote, RiskConfig, RiskContext, RiskGate, RiskStatus, risk_decision_hash,
)


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)  # Monday, test policy session.
ACCOUNT = "PAPER-EXACT-1"
RUN_ID = "paper-run-1"
METHOD = "step6c-v2-piotroski-average-assets"
INSTRUMENT = InstrumentId("AAPL", InstrumentType.EQUITY)
CONTRACT = ResolvedContract(265598, "AAPL", "STK", "USD", "SMART", "NASDAQ")


class PaperCase:
    def __init__(self, root: Path, *, accounts: tuple[str, ...] = (ACCOUNT,), quote: IBKRQuote | None = None) -> None:
        self.root = root
        artifact = root / "material.json"
        artifact.write_text('{"material":true}', encoding="utf-8")
        create_run_manifest(root=root, run_id=RUN_ID, methodology_version=METHOD, artifacts={"material": artifact})
        self.manifest = verify_run_manifest(root=root, run_id=RUN_ID)
        self.state = PortfolioState(NOW, 100_000, 100_000)
        research = ResearchEligibility(
            INSTRUMENT.canonical_id, RUN_ID, self.manifest.manifest_hash, METHOD,
            NOW - timedelta(seconds=5), NOW - timedelta(seconds=10),
            "QUALIFIED", "COMPLETE", "COMPLETE",
        )
        self.proposal = PortfolioEngine().propose(
            self.state, (AllocationTarget(INSTRUMENT, .01, "Technology", research),),
            verified_manifest=self.manifest, proposal_id="paper-decision-1", decision_time=NOW,
        )
        self.risk = RiskGate(RiskConfig(
            max_single_position_exposure=.20, max_sector_exposure=.50,
            max_daily_turnover=.50, max_decision_age=timedelta(minutes=5),
            max_quote_age=timedelta(seconds=30),
        )).evaluate(
            self.proposal, self.state,
            {INSTRUMENT.canonical_id: Quote(INSTRUMENT.canonical_id, 100, NOW)},
            context=RiskContext(), checked_at=NOW,
        )
        self.quote = quote or IBKRQuote(CONTRACT.con_id, 99.5, 100.0, 99.8, NOW)
        summary_account = accounts[0] if len(accounts) == 1 else ""
        self.transport = InMemoryIBKRTransport(
            account_ids=accounts, contracts=(CONTRACT,), quote=self.quote,
            account_summary=AccountSummary(summary_account, "INDIVIDUAL", 100_000, 100_000, 100_000),
        )
        self.config = IBKRConnectionConfig(
            paper_account_allowlist=(ACCOUNT,), expected_account_id=ACCOUNT,
            live_account_denylist=("LIVE-1",),
        )
        self.audit_path = root / "paper-audit.jsonl"
        self.adapter = IBKRPaperAdapter(
            config=self.config, transport=self.transport, audit_path=self.audit_path,
            artifact_root=root,
        )
        trade = self.risk.trades[0]
        self.intent = PaperOrderIntent(
            self.risk.decision_id, "intent-1", RUN_ID, self.manifest.manifest_hash,
            METHOD, self.proposal.proposal_hash, self.risk.decision_hash,
            "AAPL", CONTRACT.con_id, OrderSide.BUY, trade.quantity, "USD", "SMART",
            trade.quote_price, self.quote, 100.0, "DAY", NOW, NOW + timedelta(minutes=2),
        )

    def connect_verify(self) -> None:
        self.adapter.connect()
        self.adapter.verify_paper_account()

    def prepare(self, *, kill: PaperKillSwitchState = PaperKillSwitchState.ARMED_FOR_PAPER) -> None:
        self.adapter.prepare_order(self.intent, self.risk, self.proposal, kill_switch_state=kill, now=NOW)

    def submit(self):
        return self.adapter.submit_order(
            self.intent, self.risk, self.proposal,
            kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW,
        )


class IBKRPaperSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.case = PaperCase(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_default_connection_remains_read_only_and_supports_probe_operations(self):
        case = self.case
        case.adapter.connect()
        self.assertEqual(case.adapter.guard.current_session_state, SessionState.READ_ONLY_PROBE)
        self.assertTrue(case.transport.read_only_requested)
        self.assertEqual(case.adapter.account_summary().settled_cash, 100_000)
        self.assertEqual(case.adapter.positions(), ())
        self.assertEqual(case.adapter.open_orders(), ())
        self.assertEqual(case.adapter.completed_orders(), ())
        self.assertEqual(case.adapter.executions(), ())
        contract = case.adapter.resolve_contract("AAPL")
        self.assertEqual(case.adapter.quote_snapshot(contract), case.quote)
        self.assertNotIn("place_limit_order", case.transport.calls)
        with self.assertRaisesRegex(PaperSafetyError, "ORDER_NOT_CANCELLABLE"):
            case.adapter.cancel_order("unrelated-order", kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
        self.assertNotIn("cancel_order", case.transport.calls)

    def test_reconnect_returns_to_read_only_and_requires_fresh_verification(self):
        self.case.connect_verify(); self.case.prepare()
        self.case.adapter.reconnect()
        self.assertEqual(self.case.adapter.guard.current_session_state, SessionState.READ_ONLY_PROBE)
        with self.assertRaisesRegex(PaperSafetyError, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN):
            self.case.submit()
        self.assertEqual(self.case.transport.calls.count("place_limit_order"), 0)

    def test_unknown_wrong_live_and_ambiguous_accounts_fail_closed(self):
        for index, accounts in enumerate(((), ("WRONG",), ("LIVE-1",), (ACCOUNT, "OTHER"))):
            with self.subTest(accounts=accounts), TemporaryDirectory() as temp:
                case = PaperCase(Path(temp), accounts=accounts)
                case.adapter.connect()
                with self.assertRaisesRegex(PaperSafetyError, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN):
                    case.adapter.verify_paper_account()
                self.assertEqual(case.adapter.guard.current_session_state, SessionState.DISARMED)
                self.assertNotIn("place_limit_order", case.transport.calls)

    def test_correct_exact_broker_reported_account_verifies_but_does_not_arm(self):
        self.case.connect_verify()
        guard = self.case.adapter.guard
        self.assertEqual(guard.selected_account, ACCOUNT)
        self.assertEqual(guard.paper_verification_result, "EXACT_BROKER_ACCOUNT_ALLOWLIST_MATCH")
        self.assertEqual(guard.current_session_state, SessionState.PAPER_VERIFIED)
        self.assertIsNotNone(guard.broker_server_version)

    def test_host_port_and_allowlist_configuration_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "host"):
            IBKRConnectionConfig(host="remote.example")
        with self.assertRaisesRegex(ValueError, "port"):
            IBKRConnectionConfig(port=7496)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            IBKRConnectionConfig(paper_account_allowlist=("X",), live_account_denylist=("X",))

    def test_correct_paper_preflight_and_limit_submission(self):
        self.case.connect_verify()
        self.case.prepare()
        self.assertEqual(self.case.adapter.guard.current_session_state, SessionState.PAPER_EXECUTION_ARMED)
        ack = self.case.submit()
        self.assertTrue(ack.accepted)
        self.assertEqual(self.case.adapter.journal.state("intent-1"), IntentState.ACKNOWLEDGED)
        self.assertEqual(self.case.adapter.order_status("intent-1").order_type, "LMT")
        events = [json.loads(line) for line in self.case.audit_path.read_text(encoding="utf-8").splitlines()]
        submitting = next(row for row in events if row["event_type"] == "SUBMITTING")
        for name in (
            "source_artifact_manifest", "decision", "portfolio_proposal",
            "risk_gate_output", "session_identity", "quote", "contract_details", "order_request",
        ):
            self.assertIn(name, submitting["links"])
        self.assertEqual(submitting["links"]["order_request"]["order_type"], "LMT")

    def test_risk_failure_and_kill_switch_block_arming(self):
        self.case.connect_verify()
        rejected = replace(self.case.risk, status=RiskStatus.REJECTED, rejection_reasons=("TEST",), decision_hash="")
        rejected = replace(rejected, decision_hash=risk_decision_hash(rejected))
        with self.assertRaisesRegex(PaperSafetyError, "RISK_GATE_NOT_PASSED"):
            self.case.adapter.prepare_order(
                self.case.intent, rejected, self.case.proposal,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW,
            )
        self.assertNotIn("place_limit_order", self.case.transport.calls)
        with TemporaryDirectory() as temp:
            case = PaperCase(Path(temp)); case.connect_verify()
            with self.assertRaisesRegex(PaperSafetyError, "PAPER_EXECUTION_ARMING_REJECTED"):
                case.prepare(kill=PaperKillSwitchState.BLOCKED)
            self.assertNotIn("place_limit_order", case.transport.calls)

    def test_kill_switch_is_rechecked_immediately_before_submission(self):
        self.case.connect_verify(); self.case.prepare()
        with self.assertRaisesRegex(PaperSafetyError, "KILL_SWITCH_NOT_ARMED"):
            self.case.adapter.submit_order(
                self.case.intent, self.case.risk, self.case.proposal,
                kill_switch_state=PaperKillSwitchState.BLOCKED, now=NOW,
            )
        self.assertNotIn("place_limit_order", self.case.transport.calls)

    def test_usd_settled_and_available_cash_are_both_required(self):
        for summary, code in (
            (AccountSummary(ACCOUNT, "INDIVIDUAL", 100_000, None, 100_000, "USD"), "SETTLED_OR_AVAILABLE_CASH_UNAVAILABLE"),
            (AccountSummary(ACCOUNT, "INDIVIDUAL", 100_000, 100_000, 50, "USD"), "SETTLED_OR_AVAILABLE_CASH_EXCEEDED"),
            (AccountSummary(ACCOUNT, "INDIVIDUAL", 100_000, 100_000, 100_000, "HKD"), "USD_CASH_BALANCE_UNAVAILABLE"),
            (AccountSummary(ACCOUNT, "INDIVIDUAL", 100_000, float("nan"), 100_000, "USD"), "SETTLED_OR_AVAILABLE_CASH_UNAVAILABLE"),
        ):
            with self.subTest(code=code), TemporaryDirectory() as temp:
                case = PaperCase(Path(temp)); case.transport.summary = summary; case.connect_verify()
                with self.assertRaisesRegex(PaperSafetyError, code):
                    case.prepare()

    def test_reconciliation_provider_failure_is_sanitized_and_disarms(self):
        self.case.connect_verify()
        self.case.transport.positions = Mock(side_effect=RuntimeError("password=do-not-persist"))
        with self.assertRaisesRegex(PaperSafetyError, "RECONCILIATION_FAILED"):
            self.case.prepare()
        persisted = self.case.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("do-not-persist", persisted)
        self.assertIn("PROVIDER_RUNTIMEERROR", persisted)
        self.assertEqual(self.case.adapter.guard.current_session_state, SessionState.DISARMED)
        self.assertEqual(self.case.adapter.guard.kill_switch_state, PaperKillSwitchState.BLOCKED)

    def test_stale_missing_and_wide_quotes_are_rejected(self):
        quotes = (
            (IBKRQuote(CONTRACT.con_id, 99.5, 100, 99.8, NOW - timedelta(minutes=1)), "STALE_OR_FUTURE_QUOTE"),
            (IBKRQuote(CONTRACT.con_id, None, None, None, NOW, "UNAVAILABLE"), "QUOTE_UNAVAILABLE"),
            (IBKRQuote(CONTRACT.con_id, 90, 110, 100, NOW), "QUOTE_SPREAD_UNAVAILABLE_OR_TOO_WIDE"),
        )
        for quote, code in quotes:
            with self.subTest(code=code), TemporaryDirectory() as temp:
                case = PaperCase(Path(temp), quote=quote); case.connect_verify()
                with self.assertRaisesRegex(PaperSafetyError, code):
                    case.prepare()

    def test_limit_price_deviation_and_stale_decision_are_rejected(self):
        self.case.connect_verify()
        far = replace(self.case.intent, limit_price=105)
        with self.assertRaisesRegex(PaperSafetyError, "LIMIT_PRICE_DEVIATION_EXCEEDED"):
            self.case.adapter.prepare_order(far, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        with TemporaryDirectory() as temp:
            case = PaperCase(Path(temp)); case.connect_verify()
            with self.assertRaisesRegex(PaperSafetyError, "STALE_OR_FUTURE_DECISION"):
                case.adapter.prepare_order(case.intent, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW + timedelta(minutes=10))

    def test_account_identity_change_disarms_before_every_submission(self):
        self.case.connect_verify(); self.case.prepare()
        self.case.transport.account_ids = ("LIVE-1",)
        with self.assertRaisesRegex(PaperSafetyError, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN):
            self.case.submit()
        self.assertEqual(self.case.adapter.guard.current_session_state, SessionState.DISARMED)
        self.assertNotIn("place_limit_order", self.case.transport.calls)

    def test_duplicate_intent_and_duplicate_decision_are_rejected(self):
        self.case.connect_verify(); self.case.prepare(); self.case.submit()
        with self.assertRaisesRegex(PaperSafetyError, "ORDER_INTENT_NOT_PREFLIGHT_PASSED"):
            self.case.submit()
        second = replace(self.case.intent, order_intent_id="intent-2")
        self.case.adapter.guard.current_session_state = SessionState.PAPER_VERIFIED
        with self.assertRaises(PaperSafetyError) as caught:
            self.case.adapter.prepare_order(second, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        self.assertTrue({"DUPLICATE_DECISION", "OUTSTANDING_ORDER_BLOCKS_NEW_SUBMISSION"} & set(caught.exception.reasons))
        self.assertEqual(self.case.transport.calls.count("place_limit_order"), 1)

    def test_attempted_short_and_unsupported_security_are_rejected(self):
        self.case.connect_verify()
        sell = replace(self.case.intent, side=OrderSide.SELL_TO_REDUCE_EXISTING_LONG)
        with self.assertRaises(PaperSafetyError) as caught:
            self.case.adapter.prepare_order(sell, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        self.assertIn("SELL_WOULD_CREATE_SHORT", caught.exception.reasons)
        with self.assertRaisesRegex(ValueError, "only STK"):
            replace(self.case.intent, security_type="OPT")
        with self.assertRaisesRegex(PaperSafetyError, "UNSUPPORTED_INSTRUMENT"):
            self.case.adapter.resolve_contract("AAPL", security_type="FUT")

    def test_fractional_quantity_requires_config_and_contract_support(self):
        self.case.connect_verify()
        fractional = replace(self.case.intent, quantity=1.5)
        with self.assertRaisesRegex(PaperSafetyError, "FRACTIONAL_QUANTITY_NOT_CONFIGURED"):
            self.case.adapter.prepare_order(fractional, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)

    def test_source_manifest_tamper_is_rejected(self):
        self.case.connect_verify()
        (Path(self.temp.name) / "material.json").write_text('{"material":false}', encoding="utf-8")
        with self.assertRaisesRegex(PaperSafetyError, "SOURCE_MANIFEST_VERIFICATION_FAILED"):
            self.case.prepare()
        self.assertNotIn("place_limit_order", self.case.transport.calls)


class IBKRPaperLifecycleTests(unittest.TestCase):
    def test_partial_fill_fill_and_commission_reconciliation(self):
        with TemporaryDirectory() as temp:
            case = PaperCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.broker_order_id, 4, 99.9, commission=.25)
            case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state("intent-1"), IntentState.PARTIALLY_FILLED)
            case.transport.fill(ack.broker_order_id, 6, 100.0, commission=.30)
            snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state("intent-1"), IntentState.FILLED)
            self.assertEqual([row.commission for row in snapshot.executions], [.25, .30])

    def test_broker_reject_is_terminal(self):
        with TemporaryDirectory() as temp:
            case = PaperCase(Path(temp)); case.transport.place_behavior = "REJECT"
            case.connect_verify(); case.prepare(); ack = case.submit()
            self.assertFalse(ack.accepted)
            self.assertEqual(case.adapter.journal.state("intent-1"), IntentState.REJECTED)
            self.assertEqual(case.adapter.guard.current_session_state, SessionState.DISARMED)

    def test_ambiguous_acknowledgement_blocks_until_provably_reconciled(self):
        with TemporaryDirectory() as temp:
            case = PaperCase(Path(temp)); case.transport.place_behavior = "AMBIGUOUS"
            case.connect_verify(); case.prepare()
            with self.assertRaisesRegex(PaperSafetyError, "UNKNOWN_BROKER_STATE"):
                case.submit()
            self.assertEqual(case.adapter.journal.state("intent-1"), IntentState.UNKNOWN_BROKER_STATE)
            case.adapter.verify_paper_account()
            snapshot = case.adapter.reconcile()
            self.assertIn("intent-1", snapshot.unresolved_intent_ids)
            second = replace(case.intent, order_intent_id="intent-2", decision_id="paper-decision-2")
            with self.assertRaises(PaperSafetyError) as caught:
                case.adapter.prepare_order(second, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
            self.assertIn("UNKNOWN_BROKER_STATE", caught.exception.reasons)
            self.assertEqual(case.transport.calls.count("place_limit_order"), 1)

    def test_process_death_during_submit_recovers_ack_without_retry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.transport.place_behavior = "RAISE_AFTER_ACCEPT"
            case.connect_verify(); case.prepare()
            with self.assertRaisesRegex(PaperSafetyError, "UNKNOWN_BROKER_STATE"):
                case.submit()
            place_calls = case.transport.calls.count("place_limit_order")
            restarted = IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_paper_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state("intent-1"), IntentState.ACKNOWLEDGED)
            self.assertEqual(case.transport.calls.count("place_limit_order"), place_calls)

    def test_restart_before_submit_can_reverify_same_preflight_without_duplicate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.connect_verify(); case.prepare()
            restarted = IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_paper_account(); restarted.reconcile()
            restarted.prepare_order(case.intent, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
            ack = restarted.submit_order(case.intent, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
            self.assertTrue(ack.accepted)
            self.assertEqual(case.transport.calls.count("place_limit_order"), 1)

    def test_fill_before_local_persistence_is_recovered_from_broker(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.broker_order_id, 10, 100, commission=.50)
            restarted = IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_paper_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state("intent-1"), IntentState.FILLED)

    def test_restart_after_partial_fill_recovers_without_resubmission(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.broker_order_id, 4, 99.9, commission=.25)
            place_calls = case.transport.calls.count("place_limit_order")
            restarted = IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_paper_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state("intent-1"), IntentState.PARTIALLY_FILLED)
            self.assertEqual(case.transport.calls.count("place_limit_order"), place_calls)

    def test_cancel_and_restart_recovery_during_uncertain_cancellation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.connect_verify(); case.prepare(); case.submit()
            case.transport.cancel_behavior = "AMBIGUOUS"
            with self.assertRaisesRegex(PaperSafetyError, "UNKNOWN_BROKER_STATE"):
                case.adapter.cancel_order("intent-1", kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            restarted = IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_paper_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state("intent-1"), IntentState.ACKNOWLEDGED)
            restarted.resume_order_control(case.intent, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            case.transport.cancel_behavior = "CANCEL"
            ack = restarted.cancel_order("intent-1", kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            self.assertEqual(ack.status, "Cancelled")
            self.assertEqual(restarted.journal.state("intent-1"), IntentState.CANCELLED)

    def test_audit_chain_tamper_is_detected_on_restart(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = PaperCase(root); case.connect_verify(); case.prepare(); case.submit()
            text = case.audit_path.read_text(encoding="utf-8").replace('"order_type":"LMT"', '"order_type":"MKT"', 1)
            case.audit_path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity failure"):
                IBKRPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)

    def test_execution_policy_is_explicitly_experimental(self):
        self.assertEqual(ExecutionSafetyPolicy().policy_classification, EXECUTION_POLICY_CLASSIFICATION)
        with self.assertRaises(ValueError):
            ExecutionSafetyPolicy(policy_classification="OPTIMAL")

    def test_official_transport_dependency_is_lazy(self):
        try:
            transport = OfficialIBKRTransport(timeout_seconds=.01)
        except OfficialIBAPIUnavailable:
            return
        self.assertFalse(transport.is_connected())


if __name__ == "__main__":
    unittest.main()
