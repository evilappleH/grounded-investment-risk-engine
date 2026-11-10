"""Adversarial tests for the isolated Moomoo Paper broker smoke purpose."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from investment_agents.llm.runtime import InvestorResearchResult
from investment_agents.committee.synthesis import CommitteeResearchResult
from investment_agents.integrations.moomoo import (
    InMemoryMoomooTransport,
    IntentState,
    MoomooAccount,
    MoomooBalance,
    MoomooConnectionConfig,
    MoomooOrderSide,
    MoomooOrder,
    MoomooPaperAdapter,
    MoomooPaperSmokeHarness,
    MoomooPaperSmokeIntent,
    MoomooQuote,
    MoomooSafetyError,
    MoomooSecurityKind,
    PAPER_BROKER_SMOKE_TEST,
    PaperKillSwitchState,
)
from investment_agents.integrations.moomoo.models import MoomooAcknowledgement


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
ACC_ID = "1000001"
ACCOUNT = MoomooAccount(ACC_ID, "SIMULATE", "MARGIN", "STOCK_AND_OPTION", ("US",))
QUOTE = MoomooQuote("US.AAPL", 99.50, 100.00, 99.75, NOW, True)


class SmokeCase:
    def __init__(self, root: Path) -> None:
        self.transport = InMemoryMoomooTransport(
            accounts=(ACCOUNT,),
            balance=MoomooBalance(ACC_ID, "SIMULATE", "USD", 100_000, 100_000),
            quote=QUOTE,
        )
        self.adapter = MoomooPaperAdapter(
            config=MoomooConnectionConfig(
                expected_acc_id=ACC_ID,
                simulate_account_allowlist=(ACC_ID,),
            ),
            transport=self.transport,
            audit_path=root / "smoke-audit.jsonl",
            artifact_root=root,
        )
        self.adapter.connect()
        self.adapter.verify_simulate_account()
        self.harness = MoomooPaperSmokeHarness(self.adapter)
        self.intent = self.harness.build_intent(
            authorization_id="human-smoke-auth-1",
            order_intent_id="broker-smoke-intent-1",
            quote=QUOTE,
            created_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=1),
            explicitly_human_authorized=True,
        )

    def preflight(self, *, now: datetime = NOW):
        return self.harness.preflight(
            self.intent,
            kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
            now=now,
        )


class MoomooSmokeBoundaryTests(unittest.TestCase):
    def test_typed_boundary_rejects_real_unknown_wrong_account_and_no_human_authorization(self):
        invalid = (
            {"trd_env": "REAL"},
            {"trd_env": "UNKNOWN"},
            {"acc_id": "9999999"},
            {"explicitly_human_authorized": False},
        )
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "SMOKE_TEST_BOUNDARY"):
                    replace(case.intent, **changes)
        
    def test_typed_boundary_rejects_quantity_market_option_short_and_generalization(self):
        invalid = (
            {"quantity": 2},
            {"quantity": 0.5},
            {"order_type": "MARKET"},
            {"security_kind": "OPTION"},
            {"side": MoomooOrderSide.SELL_TO_REDUCE_EXISTING_LONG},
            {"ticker": "MSFT", "code": "US.MSFT"},
            {"time_in_force": "GTC"},
            {"auto_cancel": False},
        )
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaisesRegex((ValueError, TypeError), "SMOKE_TEST_BOUNDARY"):
                    replace(case.intent, **changes)

    def test_marketable_or_invalid_tick_price_is_rejected(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            for price, code in ((QUOTE.ask, "SMOKE_LIMIT_MUST_BE_NON_MARKETABLE"), (99.495, "INVALID_TICK_SIZE")):
                intent = replace(case.intent, limit_price=price)
                with self.subTest(price=price), self.assertRaisesRegex(MoomooSafetyError, code):
                    case.harness.preflight(intent, kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER, now=NOW)

    def test_stale_quote_outside_rth_insufficient_cash_and_kill_switch_are_rejected(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            case.transport.quote = replace(QUOTE, quoted_at=NOW - timedelta(minutes=1))
            with self.assertRaisesRegex(MoomooSafetyError, "STALE_OR_FUTURE_QUOTE"):
                case.preflight()
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            with self.assertRaisesRegex(MoomooSafetyError, "OUTSIDE_ALLOWED_TRADING_SESSION"):
                case.preflight(now=NOW.replace(hour=12))
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            case.transport.balance_row = MoomooBalance(ACC_ID, "SIMULATE", "USD", 50, 50)
            with self.assertRaisesRegex(MoomooSafetyError, "AVAILABLE_CASH_EXCEEDED"):
                case.preflight()
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            with self.assertRaisesRegex(MoomooSafetyError, "KILL_SWITCH_NOT_ARMED"):
                case.harness.preflight(case.intent, kill_switch_state=PaperKillSwitchState.BLOCKED, now=NOW)

    def test_wrong_configured_account_and_changed_environment_fail_closed(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            case.adapter.config = MoomooConnectionConfig(
                expected_acc_id="9999999", simulate_account_allowlist=("9999999",)
            )
            with self.assertRaisesRegex(MoomooSafetyError, "MOOMOO_REAL_EXECUTION_FORBIDDEN"):
                case.preflight()
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            case.transport.accounts = (MoomooAccount(ACC_ID, "REAL", "MARGIN", "N/A", ("US",)),)
            with self.assertRaisesRegex(MoomooSafetyError, "MOOMOO_REAL_EXECUTION_FORBIDDEN"):
                case.preflight()

    def test_duplicate_smoke_intent_is_rejected_and_fake_workflow_auto_cancels(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            result = case.harness.run_and_auto_cancel(
                case.intent,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                now=NOW,
            )
            self.assertTrue(result.acknowledgement.accepted)
            self.assertIsNotNone(result.cancellation)
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)
            self.assertFalse(result.final_reconciliation.current_orders)
            self.assertFalse(result.unexpected_fill)
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)
            case.adapter.verify_simulate_account()
            with self.assertRaisesRegex(MoomooSafetyError, "DUPLICATE_SMOKE_INTENT"):
                case.preflight()
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)

    def test_unknown_ack_is_not_retried_and_audit_chain_detects_tampering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = SmokeCase(root)
            case.transport.place_behavior = "AMBIGUOUS"
            with self.assertRaisesRegex(MoomooSafetyError, "UNKNOWN_BROKER_STATE"):
                case.harness.run_and_auto_cancel(
                    case.intent,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                )
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)
            with self.assertRaises(MoomooSafetyError):
                case.harness.run_and_auto_cancel(
                    case.intent,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                )
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = SmokeCase(root)
            case.harness.run_and_auto_cancel(
                case.intent,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                now=NOW,
            )
            audit_path = root / "smoke-audit.jsonl"
            audit_path.write_text(
                audit_path.read_text("utf-8").replace(
                    '"execution_purpose":"PAPER_BROKER_SMOKE_TEST"',
                    '"execution_purpose":"PRODUCTION_PORTFOLIO_EXECUTION"',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "audit trail integrity failure"):
                MoomooPaperAdapter(
                    config=case.adapter.config,
                    transport=case.transport,
                    audit_path=audit_path,
                    artifact_root=root,
                )

    def test_cancel_request_is_not_called_cancelled_until_broker_reconciliation_confirms(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))

            def pending_cancel(order_id: str, acc_id: str, *, trd_env: str):
                case.transport._require_simulate(trd_env)
                case.transport.calls.append(("cancel_order", trd_env))
                return MoomooAcknowledgement(
                    True, order_id, "SIMULATE", "CANCEL_REQUESTED", NOW
                )

            case.transport.cancel_order = pending_cancel  # type: ignore[method-assign]
            with self.assertRaisesRegex(MoomooSafetyError, "UNKNOWN_BROKER_STATE"):
                case.harness.run_and_auto_cancel(
                    case.intent,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                )
            self.assertEqual(
                case.adapter.journal.state(case.intent.order_intent_id),
                IntentState.UNKNOWN_BROKER_STATE,
            )
            self.assertEqual(case.transport.calls.count(("place_limit_order", "SIMULATE")), 1)
            self.assertEqual(case.transport.calls.count(("cancel_order", "SIMULATE")), 1)

    def test_terminal_cancelled_row_retained_in_current_orders_is_valid_confirmation(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            original = case.transport.cancel_order

            def terminal_current(order_id: str, acc_id: str, *, trd_env: str):
                ack = original(order_id, acc_id, trd_env=trd_env)
                case.transport.current_order_rows[order_id] = case.transport.history_order_rows[order_id]
                return ack

            case.transport.cancel_order = terminal_current  # type: ignore[method-assign]
            result = case.harness.run_and_auto_cancel(
                case.intent,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                now=NOW,
            )
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)
            self.assertFalse(result.unexpected_fill)

    def test_terminal_cancelled_current_row_does_not_block_new_smoke(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            case.transport.current_order_rows["old"] = MoomooOrder(
                order_id="old", acc_id=ACC_ID, trd_env="SIMULATE", code="US.AAPL",
                side="BUY", order_type="LIMIT", status="CANCELLED_ALL", quantity=1.0,
                price=case.intent.limit_price, dealt_quantity=0.0, updated_at=NOW,
            )
            case.transport.history_order_rows["old"] = case.transport.current_order_rows["old"]
            result = case.harness.run_and_auto_cancel(
                case.intent,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                now=NOW,
            )
            self.assertEqual(case.adapter.journal.state(case.intent.order_intent_id), IntentState.CANCELLED)
            self.assertFalse(result.unexpected_fill)

    def test_marketable_fill_wait_is_bounded_and_opt_in(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            with self.assertRaisesRegex(MoomooSafetyError, "SIMULATE_FILL_TEST_WAIT_OUT_OF_BOUNDS"):
                case.harness.run_and_auto_cancel(
                    case.intent,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                    marketable_fill=True,
                    fill_wait_seconds=5.0,
                )

    def test_default_phase_one_does_not_use_fill_wait(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            result = case.harness.run_and_auto_cancel(
                case.intent,
                kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                now=NOW,
            )
            self.assertFalse(result.unexpected_fill)

    def test_account_environment_change_between_preflight_and_dispatch_blocks(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            original_quote = case.transport.quote_snapshot
            calls = 0

            def changing_quote(code: str):
                nonlocal calls
                calls += 1
                result = original_quote(code)
                if calls == 2:
                    case.transport.accounts = (
                        MoomooAccount(ACC_ID, "REAL", "MARGIN", "N/A", ("US",)),
                    )
                return result

            case.transport.quote_snapshot = changing_quote  # type: ignore[method-assign]
            with self.assertRaisesRegex(MoomooSafetyError, "MOOMOO_REAL_EXECUTION_FORBIDDEN"):
                case.harness.run_and_auto_cancel(
                    case.intent,
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                )
            self.assertNotIn(("place_limit_order", "SIMULATE"), case.transport.calls)

    def test_production_path_rejects_smoke_type_and_still_requires_manifest(self):
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            with self.assertRaisesRegex(MoomooSafetyError, "PRODUCTION_EXECUTION_INTENT_REQUIRED"):
                case.adapter.prepare_order(
                    case.intent, object(), object(),
                    kill_switch_state=PaperKillSwitchState.ARMED_FOR_PAPER,
                    now=NOW,
                )
            self.assertNotIn(("place_limit_order", "SIMULATE"), case.transport.calls)

    def test_agent_and_committee_output_schemas_cannot_emit_smoke_purpose(self):
        for schema in (InvestorResearchResult, CommitteeResearchResult):
            names = {item.name for item in fields(schema)}
            self.assertNotIn("purpose", names)
            self.assertNotIn("order_intent_id", names)
            self.assertNotIn("trd_env", names)
        self.assertEqual(PAPER_BROKER_SMOKE_TEST, "PAPER_BROKER_SMOKE_TEST")

    def test_smoke_has_no_automatic_promotion_into_production(self):
        names = {item.name for item in fields(MoomooPaperSmokeIntent)}
        for production_binding in (
            "source_run_id", "source_manifest_hash", "methodology_version",
            "portfolio_proposal_hash", "risk_result_hash", "decision_id",
        ):
            self.assertNotIn(production_binding, names)
        with TemporaryDirectory() as temp:
            case = SmokeCase(Path(temp))
            self.assertFalse(hasattr(case.intent, "source_manifest_hash"))
            self.assertNotIsInstance(case.intent, __import__(
                "investment_agents.integrations.moomoo.models",
                fromlist=["MoomooOrderIntent"],
            ).MoomooOrderIntent)


if __name__ == "__main__":
    unittest.main()
