"""Adversarial tests for the permanent SIMULATE-only Moomoo adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from investment_agents.integrations.moomoo import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN, MOOMOO_SIMULATE_CAPABILITIES, InMemoryMoomooTransport, IntentState,
    MoomooAccount, MoomooAcknowledgement, MoomooBalance, MoomooConnectionConfig, MoomooOrderIntent,
    MoomooOrderSide, MoomooPaperAdapter, MoomooPosition, MoomooQuote,
    MoomooSafetyError, MoomooSecurityKind, MoomooSessionState,
    PaperKillSwitchState,
    run_account_discovery_probe,
)
from investment_agents.integrations.moomoo.official import OfficialMoomooTransport
from investment_agents.portfolio import (
    AllocationTarget, InstrumentId, InstrumentType, PortfolioEngine,
    PortfolioState, ResearchEligibility,
)
from investment_agents.research import create_run_manifest, verify_run_manifest
from investment_agents.risk import Quote, RiskConfig, RiskContext, RiskGate, RiskStatus, risk_decision_hash


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
ACC_ID = "88001234"
RUN_ID = "moomoo-run-1"
METHOD = "step6c-v2-piotroski-average-assets"
INSTRUMENT = InstrumentId("AAPL", InstrumentType.EQUITY)
SIM_ACCOUNT = MoomooAccount(ACC_ID, "SIMULATE", "MARGIN", "STOCK", ("US",))
QUOTE = MoomooQuote("US.AAPL", 99.5, 100.0, 99.8, NOW, True)


class MoomooCase:
    def __init__(
        self,
        root: Path,
        *,
        accounts: tuple[MoomooAccount, ...] = (SIM_ACCOUNT,),
        quote: MoomooQuote = QUOTE,
        expected_acc_id: str | None = ACC_ID,
        simulate_account_allowlist: tuple[str, ...] = (ACC_ID,),
    ) -> None:
        self.root = root
        artifact = root / "material.json"; artifact.write_text('{"material":true}', encoding="utf-8")
        create_run_manifest(root=root, run_id=RUN_ID, methodology_version=METHOD, artifacts={"material": artifact})
        self.manifest = verify_run_manifest(root=root, run_id=RUN_ID)
        self.state = PortfolioState(NOW, 100_000, 100_000)
        evidence = ResearchEligibility(
            INSTRUMENT.canonical_id, RUN_ID, self.manifest.manifest_hash, METHOD,
            NOW - timedelta(seconds=5), NOW - timedelta(seconds=10),
            "QUALIFIED", "COMPLETE", "COMPLETE",
        )
        self.proposal = PortfolioEngine().propose(
            self.state, (AllocationTarget(INSTRUMENT, .01, "Technology", evidence),),
            verified_manifest=self.manifest, proposal_id="moomoo-decision-1", decision_time=NOW,
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
        self.transport = InMemoryMoomooTransport(accounts=accounts, quote=quote)
        self.config = MoomooConnectionConfig(
            simulate_account_allowlist=simulate_account_allowlist,
            expected_acc_id=expected_acc_id,
        )
        self.audit_path = root / "moomoo-audit.jsonl"
        self.adapter = MoomooPaperAdapter(
            config=self.config, transport=self.transport, audit_path=self.audit_path,
            artifact_root=root,
        )
        trade = self.risk.trades[0]
        self.intent = MoomooOrderIntent(
            self.risk.decision_id, "moomoo-intent-1", RUN_ID,
            self.manifest.manifest_hash, METHOD, self.proposal.proposal_hash,
            self.risk.decision_hash, "AAPL", "US.AAPL", MoomooSecurityKind.STOCK,
            MoomooOrderSide.BUY, trade.quantity, "USD", trade.quote_price,
            quote, 100.0, NOW, NOW + timedelta(minutes=2),
        )

    def connect_verify(self) -> None:
        self.adapter.connect(); self.adapter.verify_simulate_account()

    def prepare(self, kill: PaperKillSwitchState = PaperKillSwitchState.ARMED_FOR_PAPER) -> None:
        self.adapter.prepare_order(self.intent, self.risk, self.proposal, kill_switch_state=kill, now=NOW)

    def submit(self):
        return self.adapter.submit_order(
            self.intent, self.risk, self.proposal,
            kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW,
        )


class MoomooAccountSafetyTests(unittest.TestCase):
    def test_simulate_account_is_accepted_but_not_implicitly_armed(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.adapter.connect()
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.READ_ONLY_PROBE)
            account = case.adapter.verify_simulate_account()
            self.assertEqual(account.acc_id, ACC_ID)
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.SIMULATE_VERIFIED)
            self.assertEqual(account.acc_type, "MARGIN")  # recorded, never used to permit leverage.
            self.assertEqual(account.sim_acc_type, "STOCK")
            self.assertEqual(account.trdmarket_auth, ("US",))

    def test_real_only_is_blocked_at_simulate_verification(self):
        real = MoomooAccount("9000", "REAL", "MARGIN", "N/A", ("US",))
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp), accounts=(real,))
            case.adapter.connect()
            with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN):
                case.adapter.verify_simulate_account()
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.DISARMED)

    def test_real_plus_exact_simulate_verifies_only_exact_simulate(self):
        real = MoomooAccount("9000", "REAL", "MARGIN", "N/A", ("US",))
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp), accounts=(SIM_ACCOUNT, real))
            case.adapter.connect()
            selected = case.adapter.verify_simulate_account()
            self.assertEqual(selected.acc_id, ACC_ID)
            self.assertEqual(selected.trd_env, "SIMULATE")
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.SIMULATE_VERIFIED)

    def test_wrong_or_missing_simulation_account_fails_closed(self):
        wrong = MoomooAccount("WRONG", "SIMULATE", "MARGIN", "STOCK", ("US",))
        for accounts in ((), (wrong,)):
            with self.subTest(accounts=accounts), TemporaryDirectory() as temp:
                case = MoomooCase(Path(temp), accounts=accounts); case.adapter.connect()
                with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN):
                    case.adapter.verify_simulate_account()

    def test_multiple_simulate_requires_explicit_expected_account(self):
        second = MoomooAccount("SECOND", "SIMULATE", "MARGIN", "STOCK_AND_OPTION", ("US",))
        with TemporaryDirectory() as temp:
            case = MoomooCase(
                Path(temp),
                accounts=(SIM_ACCOUNT, second),
                expected_acc_id=None,
                simulate_account_allowlist=(ACC_ID, "SECOND"),
            )
            case.adapter.connect()
            with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN):
                case.adapter.verify_simulate_account()
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp), accounts=(SIM_ACCOUNT, second))
            case.adapter.connect()
            self.assertEqual(case.adapter.verify_simulate_account().acc_id, ACC_ID)

    def test_endpoint_is_local_allowlisted_only(self):
        with self.assertRaisesRegex(ValueError, "host"):
            MoomooConnectionConfig(host="remote.example")
        with self.assertRaisesRegex(ValueError, "port"):
            MoomooConnectionConfig(port=11112)

    def test_reconnect_returns_to_read_only(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); case.adapter.reconnect()
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.READ_ONLY_PROBE)
            with self.assertRaises(MoomooSafetyError): case.submit()
            self.assertNotIn(("place_limit_order", "SIMULATE"), case.transport.calls)

    def test_account_identity_change_and_provider_disconnect_disarm(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare()
            case.transport.accounts = (MoomooAccount("CHANGED", "SIMULATE", "MARGIN", "STOCK", ("US",)),)
            with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN): case.submit()
            self.assertEqual(case.adapter.guard.state, MoomooSessionState.DISARMED)
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare()
            case.transport.accounts = (MoomooAccount(ACC_ID, "REAL", "MARGIN", "N/A", ("US",)),)
            with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN): case.submit()
            self.assertNotIn(("place_limit_order", "SIMULATE"), case.transport.calls)
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); case.transport.heartbeat_available = False
            with self.assertRaisesRegex(MoomooSafetyError, "OPEND_SESSION_UNAVAILABLE"): case.submit()


class MoomooOrderSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(); self.case = MoomooCase(Path(self.temp.name)); self.case.connect_verify()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_successful_limit_day_submission_and_audit_links(self):
        self.case.prepare(); ack = self.case.submit()
        self.assertTrue(ack.accepted); self.assertEqual(ack.trd_env, "SIMULATE")
        self.assertEqual(self.case.adapter.journal.state(self.case.intent.order_intent_id), IntentState.ACKNOWLEDGED)
        self.assertEqual(self.case.adapter.order_status(self.case.intent.order_intent_id).order_type, "NORMAL")
        rows = [json.loads(line) for line in self.case.audit_path.read_text(encoding="utf-8").splitlines()]
        event = next(row for row in rows if row["event_type"] == "MOOMOO_SUBMITTING")
        for key in ("source_artifact_manifest", "decision", "portfolio_proposal", "risk_gate_output", "session_identity", "quote", "order_request"):
            self.assertIn(key, event["links"])
        self.assertEqual(event["links"]["order_request"]["order_type"], "LIMIT")
        self.assertTrue(all(env in {None, "SIMULATE"} for _, env in self.case.transport.calls))

    def test_real_environment_response_is_forbidden(self):
        self.case.transport.place_behavior = "REAL_RESPONSE"; self.case.prepare()
        with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN): self.case.submit()
        self.assertEqual(self.case.adapter.guard.state, MoomooSessionState.DISARMED)

    def test_order_transport_refuses_real_environment_argument(self):
        with self.assertRaisesRegex(PermissionError, MOOMOO_REAL_EXECUTION_FORBIDDEN):
            self.case.transport.place_limit_order(
                self.case.intent,
                ACC_ID,
                trd_env="REAL",
            )
        self.assertNotIn(("place_limit_order", "REAL"), self.case.transport.calls)

    def test_duplicate_intent_and_decision_are_rejected(self):
        self.case.prepare(); self.case.submit()
        with self.assertRaisesRegex(MoomooSafetyError, "ORDER_INTENT_NOT_PREFLIGHT_PASSED"): self.case.submit()
        second = replace(self.case.intent, order_intent_id="moomoo-intent-2")
        self.case.adapter.guard.state = MoomooSessionState.SIMULATE_VERIFIED
        with self.assertRaises(MoomooSafetyError) as caught:
            self.case.adapter.prepare_order(second, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        self.assertIn("DUPLICATE_DECISION", caught.exception.reasons)
        self.assertEqual(self.case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)

    def test_stale_quote_and_missing_quote_are_rejected(self):
        for quote, code in (
            (replace(QUOTE, quoted_at=NOW - timedelta(minutes=1)), "STALE_OR_FUTURE_QUOTE"),
            (MoomooQuote("US.AAPL", None, None, None, None, True, "UNAVAILABLE"), "QUOTE_UNAVAILABLE"),
        ):
            with self.subTest(code=code), TemporaryDirectory() as temp:
                case = MoomooCase(Path(temp), quote=quote); case.connect_verify()
                with self.assertRaisesRegex(MoomooSafetyError, code): case.prepare()

    def test_insufficient_or_unavailable_cash_rejected(self):
        for balance, code in (
            (MoomooBalance(ACC_ID, "SIMULATE", "USD", 50, 50), "AVAILABLE_CASH_EXCEEDED"),
            (MoomooBalance(ACC_ID, "SIMULATE", "USD", None, 100_000), "USD_AVAILABLE_CASH_UNAVAILABLE"),
            (MoomooBalance(ACC_ID, "SIMULATE", "HKD", 100_000, 100_000), "USD_AVAILABLE_CASH_UNAVAILABLE"),
        ):
            with self.subTest(code=code), TemporaryDirectory() as temp:
                case = MoomooCase(Path(temp)); case.transport.balance_row = balance; case.connect_verify()
                with self.assertRaisesRegex(MoomooSafetyError, code): case.prepare()

    def test_attempted_short_is_rejected(self):
        sell = replace(self.case.intent, side=MoomooOrderSide.SELL_TO_REDUCE_EXISTING_LONG)
        with self.assertRaises(MoomooSafetyError) as caught:
            self.case.adapter.prepare_order(sell, self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        self.assertIn("SELL_WOULD_CREATE_SHORT", caught.exception.reasons)

    def test_unsupported_instrument_region_currency_market_and_fraction_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "canonical US ticker"): replace(self.case.intent, code="HK.00700")
        with self.assertRaises(ValueError): replace(self.case.intent, security_kind="OPTION")
        with self.assertRaisesRegex(ValueError, "USD LIMIT DAY"): replace(self.case.intent, order_type="MARKET")
        unsupported_quote = replace(QUOTE, v1_eligible_security=False)
        self.case.transport.quote = unsupported_quote
        with self.assertRaisesRegex(MoomooSafetyError, "UNSUPPORTED_SECURITY"):
            self.case.adapter.prepare_order(replace(self.case.intent, quote=unsupported_quote), self.case.risk, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); fractional = replace(case.intent, quantity=1.5)
            with self.assertRaisesRegex(MoomooSafetyError, "FRACTIONAL_QUANTITY_UNSUPPORTED"):
                case.adapter.prepare_order(fractional, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)

    def test_risk_rejection_and_kill_switch_block(self):
        rejected = replace(self.case.risk, status=RiskStatus.REJECTED, rejection_reasons=("TEST",), decision_hash="")
        rejected = replace(rejected, decision_hash=risk_decision_hash(rejected))
        with self.assertRaisesRegex(MoomooSafetyError, "RISK_GATE_NOT_PASSED"):
            self.case.adapter.prepare_order(self.case.intent, rejected, self.case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify()
            with self.assertRaisesRegex(MoomooSafetyError, "MOOMOO_SIMULATE_ARMING_REJECTED"): case.prepare(PaperKillSwitchState.BLOCKED)
            self.assertNotIn(("place_limit_order", "SIMULATE"), case.transport.calls)

    def test_source_manifest_and_audit_tampering_are_detected(self):
        (Path(self.temp.name) / "material.json").write_text('{"material":false}', encoding="utf-8")
        with self.assertRaisesRegex(MoomooSafetyError, "SOURCE_MANIFEST_VERIFICATION_FAILED"): self.case.prepare()
        with TemporaryDirectory() as temp:
            root = Path(temp); case = MoomooCase(root); case.connect_verify(); case.prepare(); case.submit()
            case.audit_path.write_text(case.audit_path.read_text(encoding="utf-8").replace('"order_type":"LIMIT"', '"order_type":"MARKET"', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Moomoo Paper audit trail integrity failure"):
                MoomooPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)


class MoomooLifecycleTests(unittest.TestCase):
    def test_unknown_acknowledgement_blocks_and_is_not_retried(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.transport.place_behavior = "AMBIGUOUS"; case.connect_verify(); case.prepare()
            with self.assertRaisesRegex(MoomooSafetyError, "UNKNOWN_BROKER_STATE"): case.submit()
            case.adapter.verify_simulate_account(); snapshot = case.adapter.reconcile()
            self.assertIn(case.intent.order_intent_id, snapshot.unresolved_intent_ids)
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)

    def test_restart_reconciles_lost_ack_without_resubmit(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = MoomooCase(root); case.transport.place_behavior = "RAISE_AFTER_ACCEPT"; case.connect_verify(); case.prepare()
            with self.assertRaisesRegex(MoomooSafetyError, "UNKNOWN_BROKER_STATE"): case.submit()
            count = case.transport.calls.count(("place_limit_order", "SIMULATE"))
            restarted = MoomooPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_simulate_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state(case.intent.order_intent_id), IntentState.ACKNOWLEDGED)
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), count)

    def test_partial_and_final_fill_reconciliation(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.order_id, 4, 99.8, fee=.10); case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.PARTIALLY_FILLED)
            case.transport.fill(ack.order_id, 6, 100, fee=.15); snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.FILLED)
            self.assertEqual(snapshot.deals, ())
            self.assertEqual(snapshot.historical_orders[-1].dealt_quantity, 10)
            self.assertEqual(snapshot.historical_orders[-1].dealt_average_price, 100)

    def test_cancel_and_restart_recovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = MoomooCase(root); case.connect_verify(); case.prepare(); case.submit()
            case.transport.cancel_behavior = "AMBIGUOUS"
            with self.assertRaisesRegex(MoomooSafetyError, "UNKNOWN_BROKER_STATE"):
                case.adapter.cancel_order(case.intent.order_intent_id, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            restarted = MoomooPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_simulate_account(); restarted.reconcile()
            self.assertEqual(restarted.journal.state(case.intent.order_intent_id), IntentState.ACKNOWLEDGED)
            restarted.resume_order_control(case.intent, case.risk, case.proposal, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            case.transport.cancel_behavior = "CANCEL"
            ack = restarted.cancel_order(case.intent.order_intent_id, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            self.assertEqual(ack.status, "CANCELLED_ALL")
            self.assertEqual(restarted.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)

    def test_real_cancel_response_is_forbidden_and_persisted_unknown(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); case.submit()
            case.transport.cancel_behavior = "REAL_RESPONSE"
            with self.assertRaisesRegex(MoomooSafetyError, MOOMOO_REAL_EXECUTION_FORBIDDEN):
                case.adapter.cancel_order(
                    case.intent.order_intent_id,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                )
            self.assertEqual(
                case.adapter.journal.state(case.intent.order_intent_id),
                IntentState.UNKNOWN_BROKER_STATE,
            )
            self.assertIn("MOOMOO_REAL_CANCEL_RESPONSE_REJECTED", case.audit_path.read_text("utf-8"))

    def test_read_models_query_only_simulate_environment(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify()
            case.adapter.account_balance(); case.adapter.positions(); case.adapter.current_orders(); case.adapter.historical_orders(); case.adapter.deals()
            envs = [env for name, env in case.transport.calls if name in {"account_balance", "positions", "current_orders", "historical_orders", "deals"}]
            self.assertTrue(envs); self.assertEqual(set(envs), {"SIMULATE"})


class MoomooSimulateReconciliationTests(unittest.TestCase):
    def test_capability_profile_disables_all_simulate_deal_interfaces(self):
        self.assertTrue(MOOMOO_SIMULATE_CAPABILITIES.supports_current_order_query)
        self.assertTrue(MOOMOO_SIMULATE_CAPABILITIES.supports_historical_order_query)
        self.assertTrue(MOOMOO_SIMULATE_CAPABILITIES.supports_position_query)
        self.assertFalse(MOOMOO_SIMULATE_CAPABILITIES.supports_current_deal_query)
        self.assertFalse(MOOMOO_SIMULATE_CAPABILITIES.supports_historical_deal_query)
        self.assertFalse(MOOMOO_SIMULATE_CAPABILITIES.supports_deal_push)

    def test_simulate_reconciliation_never_calls_unsupported_deal_query(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.adapter.reconcile()
            self.assertNotIn(("deals", "SIMULATE"), case.transport.calls)
            self.assertEqual(case.adapter.deals(), ())
            self.assertNotIn(("deals", "SIMULATE"), case.transport.calls)

    def test_unsupported_deal_provider_error_is_irrelevant_to_reconciliation(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify()
            def unsupported(*_args, **_kwargs):
                raise AssertionError("deal API must not be dispatched")
            case.transport.deals = unsupported  # type: ignore[method-assign]
            case.adapter.reconcile()
            self.assertNotIn(("deals", "SIMULATE"), case.transport.calls)

    def test_acknowledged_unfilled_order_uses_exact_order_query(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.ACKNOWLEDGED)
            self.assertIn(("order_status", "SIMULATE"), case.transport.calls)
            self.assertEqual(snapshot.deals, ())

    def test_historical_order_fallback_and_exact_id_matching(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            row = case.transport.current_order_rows.pop(ack.order_id)
            case.transport.history_order_rows[ack.order_id] = row
            original = case.transport.order_status
            case.transport.order_status = lambda _oid, _acc, *, trd_env: None  # type: ignore[method-assign]
            snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.ACKNOWLEDGED)
            self.assertEqual(snapshot.historical_orders[0].order_id, ack.order_id)
            case.transport.order_status = original  # type: ignore[method-assign]

    def test_wrong_order_id_with_same_symbol_never_resolves_intent(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            row = case.transport.current_order_rows[ack.order_id]
            case.transport.current_order_rows.pop(ack.order_id)
            case.transport.current_order_rows["other-order"] = replace(row, order_id="other-order", remark=case.intent.order_intent_id)
            snapshot = case.adapter.reconcile()
            self.assertIn(case.intent.order_intent_id, snapshot.unresolved_intent_ids)
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.UNKNOWN_BROKER_STATE)

    def test_unknown_order_after_restart_is_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); case = MoomooCase(root); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.current_order_rows.clear(); case.transport.history_order_rows.clear()
            restarted = MoomooPaperAdapter(config=case.config, transport=case.transport, audit_path=case.audit_path, artifact_root=root)
            restarted.connect(); restarted.verify_simulate_account(); snapshot = restarted.reconcile()
            self.assertIn(case.intent.order_intent_id, snapshot.unresolved_intent_ids)
            self.assertEqual(restarted.journal.state(case.intent.order_intent_id), IntentState.UNKNOWN_BROKER_STATE)

    def test_partial_full_and_cancelled_after_partial_use_order_quantities(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.order_id, 4, 99.8); case.adapter.reconcile()
            row = case.transport.current_order_rows.pop(ack.order_id)
            case.transport.history_order_rows[ack.order_id] = replace(row, status="CANCELLED_PART")
            snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)
            self.assertEqual(snapshot.historical_orders[0].dealt_quantity, 4)

        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.order_id, 10, 100); snapshot = case.adapter.reconcile()
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.FILLED)
            self.assertEqual(snapshot.historical_orders[0].dealt_quantity, 10)

    def test_inconsistent_dealt_quantity_and_position_mismatch_are_unknown(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            row = case.transport.current_order_rows[ack.order_id]
            case.transport.current_order_rows[ack.order_id] = replace(row, dealt_quantity=row.quantity + 1, dealt_average_price=100)
            snapshot = case.adapter.reconcile()
            self.assertIn(case.intent.order_intent_id, snapshot.unresolved_intent_ids)
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.UNKNOWN_BROKER_STATE)

        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.fill(ack.order_id, 4, 100); case.transport.position_rows.clear()
            snapshot = case.adapter.reconcile()
            self.assertIn(case.intent.order_intent_id, snapshot.unresolved_intent_ids)
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.UNKNOWN_BROKER_STATE)

    def test_cancel_ack_alone_does_not_transition_to_cancelled(self):
        with TemporaryDirectory() as temp:
            case = MoomooCase(Path(temp)); case.connect_verify(); case.prepare(); ack = case.submit()
            case.transport.cancel_behavior = "CANCEL_REQUESTED"
            original = case.transport.cancel_order
            def pending(order_id: str, acc_id: str, *, trd_env: str):
                case.transport.calls.append(("cancel_order", trd_env))
                return MoomooAcknowledgement(True, order_id, "SIMULATE", "CANCEL_REQUESTED", NOW)
            case.transport.cancel_order = pending  # type: ignore[method-assign]
            with self.assertRaisesRegex(MoomooSafetyError, "CANCEL_NOT_CONFIRMED_BY_BROKER|UNKNOWN_BROKER_STATE"):
                case.adapter.cancel_order(case.intent.order_intent_id, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER)
            self.assertNotEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)
            case.transport.cancel_order = original  # type: ignore[method-assign]


class OfficialMoomooContractTests(unittest.TestCase):
    def test_account_probe_fails_closed_on_sdk_initialization_error(self):
        with patch(
            "investment_agents.integrations.moomoo.probe.OfficialMoomooTransport",
            side_effect=PermissionError("sensitive local path"),
        ):
            result = run_account_discovery_probe()
        self.assertFalse(result.reachable)
        self.assertEqual(result.error_code, "PROBE_SDK_INITIALIZATION_PERMISSIONERROR")
        self.assertNotIn("sensitive", result.error_code.lower())

    def test_read_only_probe_reports_simulate_when_real_rows_are_also_returned(self):
        real = MoomooAccount("9000", "REAL", "MARGIN", "N/A", ("US",))
        fake_transport = SimpleNamespace(
            account_discovery_probe=lambda _host, _port: SimpleNamespace(
                open_d_version=None,
                accounts=(SIM_ACCOUNT, real),
            )
        )
        with patch(
            "investment_agents.integrations.moomoo.probe.OfficialMoomooTransport",
            return_value=fake_transport,
        ):
            result = run_account_discovery_probe()
        self.assertTrue(result.reachable)
        self.assertTrue(result.real_account_rows_observed)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.compatible_us_simulate_accounts[0]["acc_id"], ACC_ID)

    def test_official_sdk_mapping_is_simulate_limit_day_and_never_unlocks(self):
        trade_calls: list[tuple[str, dict[str, object]]] = []

        class FakeTradeContext:
            def get_acc_list(self):
                return 0, [{
                    "acc_id": ACC_ID,
                    "trd_env": "SIMULATE",
                    "acc_type": "MARGIN",
                    "sim_acc_type": "STOCK_AND_OPTION",
                    "trdmarket_auth": ["US"],
                }]

            def place_order(self, **kwargs):
                trade_calls.append(("place_order", kwargs))
                return 0, [{"order_id": "7000", "trd_env": "SIMULATE", "order_status": "SUBMITTING"}]

            def accinfo_query(self, **kwargs):
                trade_calls.append(("accinfo_query", kwargs))
                return 0, [{
                    "cash": 1000,
                    "us_cash": 1000,
                    "frozen_cash": 200,
                    "available_funds": "N/A",
                    "us_avl_withdrawal_cash": 0,
                    "usd_net_cash_power": "N/A",
                    "power": 2000,
                }]

            def modify_order(self, *args, **kwargs):
                trade_calls.append(("modify_order", {"args": args, **kwargs}))
                return 0, [{"order_id": "7000", "trd_env": "SIMULATE"}]

            def unlock_trade(self, *_args, **_kwargs):
                raise AssertionError("simulation adapter must never unlock trading")

            def close(self):
                trade_calls.append(("trade_close", {}))

        class FakeQuoteContext:
            def get_global_state(self):
                return 0, [{"server_ver": "FAKE-10.10"}]

            def close(self):
                trade_calls.append(("quote_close", {}))

        fake_trade = FakeTradeContext()
        fake_quote = FakeQuoteContext()
        sdk = ModuleType("moomoo")
        sdk.RET_OK = 0
        sdk.TrdMarket = SimpleNamespace(US="US")
        sdk.TrdEnv = SimpleNamespace(SIMULATE="SIMULATE")
        sdk.TrdSide = SimpleNamespace(BUY="BUY", SELL="SELL")
        sdk.OrderType = SimpleNamespace(NORMAL="NORMAL")
        sdk.TimeInForce = SimpleNamespace(DAY="DAY")
        sdk.Session = SimpleNamespace(NONE="NONE")
        sdk.ModifyOrderOp = SimpleNamespace(CANCEL="CANCEL")
        sdk.Currency = SimpleNamespace(USD="USD")
        sdk.OpenSecTradeContext = lambda **_kwargs: fake_trade
        sdk.OpenQuoteContext = lambda **_kwargs: fake_quote

        intent = MoomooOrderIntent(
            "official-decision", "official-intent", "official-run", "manifest",
            METHOD, "proposal", "risk", "AAPL", "US.AAPL",
            MoomooSecurityKind.STOCK, MoomooOrderSide.BUY, 1, "USD", 100,
            QUOTE, 100, NOW, NOW + timedelta(minutes=1),
        )
        with patch.dict(sys.modules, {"moomoo": sdk}):
            transport = OfficialMoomooTransport()
            probe_info = transport.account_discovery_probe("127.0.0.1", 11111)
            self.assertEqual(probe_info.accounts[0].trd_env, "SIMULATE")
            self.assertIsNone(probe_info.open_d_version)
            self.assertFalse(transport.is_connected())
            info = transport.connect("127.0.0.1", 11111)
            self.assertEqual(info.accounts[0].trd_env, "SIMULATE")
            self.assertEqual(info.accounts[0].sim_acc_type, "STOCK_AND_OPTION")
            balance = transport.account_balance(ACC_ID, trd_env="SIMULATE")
            self.assertEqual(balance.cash, 1000)
            self.assertEqual(balance.available_cash, 800)
            self.assertEqual(balance.values["cash_field"], "us_cash")
            self.assertIn("power", balance.values["excluded_fields"])
            transport.place_limit_order(intent, ACC_ID, trd_env="SIMULATE")
            transport.cancel_order("7000", ACC_ID, trd_env="SIMULATE")
            transport.disconnect()

        placed = dict(trade_calls)["place_order"]
        self.assertEqual(placed["trd_env"], "SIMULATE")
        self.assertEqual(placed["order_type"], "NORMAL")
        self.assertEqual(placed["time_in_force"], "DAY")
        self.assertEqual(placed["session"], "NONE")
        self.assertEqual(placed["remark"], "official-intent")
        self.assertNotIn("password", placed)
        cancelled = dict(trade_calls)["modify_order"]
        self.assertEqual(cancelled["args"][0], "CANCEL")
        self.assertEqual(cancelled["trd_env"], "SIMULATE")


if __name__ == "__main__":
    unittest.main()
