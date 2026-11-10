"""Permanent SIMULATE-only Moomoo OpenD adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from math import floor, isclose, isfinite
from pathlib import Path
from typing import Mapping

from investment_agents.integrations.ibkr.adapter import PaperSafetyError
from investment_agents.integrations.ibkr.audit import PaperExecutionJournal, jsonable
from investment_agents.integrations.ibkr.models import (
    ExecutionSafetyPolicy, IntentState, PaperKillSwitchState,
)
from investment_agents.path_safety import sanitized_provider_error
from investment_agents.portfolio import ProposedAllocation, ProposalStatus, verify_proposed_allocation_hash
from investment_agents.research import verify_run_manifest
from investment_agents.risk import RiskDecision, RiskStatus, verify_risk_decision_hash

from .backend import MoomooTransport
from .models import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN, MoomooAccount, MoomooAcknowledgement,
    MoomooBalance, MoomooConnectionConfig, MoomooDeal, MoomooOrder,
    MoomooOrderIntent, MoomooOrderSide, MoomooPosition, MoomooQuote,
    MoomooReconciliation, MoomooSecurityKind, MoomooSessionState,
)
from .session import MoomooSessionGuard, SIMULATE_ACCOUNT_UNAVAILABLE


class MoomooSafetyError(PaperSafetyError):
    pass


def _intent_state(order: MoomooOrder) -> IntentState:
    """Map order evidence conservatively; never invent a fill."""
    status = order.status.replace(" ", "").upper()
    qty = float(order.quantity)
    dealt = float(order.dealt_quantity)
    if not isfinite(qty) or qty <= 0 or not isfinite(dealt) or dealt < 0 or dealt > qty + 1e-9:
        return IntentState.UNKNOWN_BROKER_STATE
    if dealt > 0 and (order.dealt_average_price is None or not isfinite(order.dealt_average_price) or order.dealt_average_price <= 0):
        return IntentState.UNKNOWN_BROKER_STATE
    if status in {"FAILED", "DISABLED", "DELETED", "REJECTED"}:
        return IntentState.REJECTED if dealt == 0 else IntentState.UNKNOWN_BROKER_STATE
    if status in {"CANCELLED_ALL", "CANCELLEDALL", "CANCELLED_PART", "CANCELLEDPART"}:
        # A cancelled order with a partial dealt quantity is still terminally
        # cancelled, while the journal payload preserves dealt_qty/avg price.
        return IntentState.CANCELLED if dealt < qty else IntentState.UNKNOWN_BROKER_STATE
    if status in {"FILLED_ALL", "FILLEDALL"}:
        return IntentState.FILLED if isclose(dealt, qty, abs_tol=1e-9) else IntentState.UNKNOWN_BROKER_STATE
    if status in {"FILLED_PART", "FILLEDPART"}:
        return IntentState.PARTIALLY_FILLED if 0 < dealt < qty else IntentState.UNKNOWN_BROKER_STATE
    if status in {"SUBMITTED", "SUBMITTING", "WAITING_SUBMIT", "WAITINGSUBMIT", "FILLING", "CANCELING", "CANCELINGPART"}:
        if dealt == 0:
            return IntentState.ACKNOWLEDGED
        if dealt < qty:
            return IntentState.PARTIALLY_FILLED
        return IntentState.UNKNOWN_BROKER_STATE
    return IntentState.UNKNOWN_BROKER_STATE


class MoomooPaperAdapter:
    """Supported Moomoo surface; no method accepts or infers REAL execution."""

    def __init__(
        self,
        *,
        config: MoomooConnectionConfig,
        transport: MoomooTransport,
        audit_path: Path,
        artifact_root: Path,
        policy: ExecutionSafetyPolicy | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.policy = policy or ExecutionSafetyPolicy()
        self.artifact_root = artifact_root.resolve()
        self.guard = MoomooSessionGuard(config)
        self.journal = PaperExecutionJournal(audit_path, journal_label="Moomoo Paper")
        self.last_reconciliation: MoomooReconciliation | None = None

    @property
    def session_identity(self) -> Mapping[str, object]:
        account = self.guard.selected_account
        return {
            "host": self.guard.host,
            "port": self.guard.port,
            "connected_at": self.guard.connected_at,
            "open_d_version": self.guard.open_d_version,
            "simulate_accounts": tuple({
                "acc_id": row.acc_id, "trd_env": row.trd_env, "acc_type": row.acc_type,
                "sim_acc_type": row.sim_acc_type, "trdmarket_auth": row.trdmarket_auth,
            } for row in self.guard.accounts if row.trd_env == "SIMULATE"),
            "selected_account": ({
                "acc_id": account.acc_id, "trd_env": account.trd_env,
                "acc_type": account.acc_type, "sim_acc_type": account.sim_acc_type,
                "trdmarket_auth": account.trdmarket_auth,
            } if account else None),
            "verification_result": self.guard.verification_result,
            "session_state": self.guard.state,
            "last_successful_heartbeat": self.guard.last_successful_heartbeat,
            "last_successful_reconciliation": self.guard.last_successful_reconciliation,
            "kill_switch_state": self.guard.kill_switch_state,
        }

    def connect(self) -> None:
        info = self.transport.connect(self.config.host, self.config.port)
        self.guard.connected(info)
        self.journal.append("MOOMOO_READ_ONLY_CONNECTED", links={"session_identity": self.session_identity})

    def disconnect(self) -> None:
        self.transport.disconnect()
        self.guard.disarm("DISCONNECTED")
        self.journal.append("MOOMOO_DISCONNECTED", links={"session_identity": self.session_identity})

    def reconnect(self) -> None:
        if self.transport.is_connected():
            self.transport.disconnect()
        self.guard.disarm("RECONNECTING")
        info = self.transport.connect(self.config.host, self.config.port)
        self.guard.connected(info)
        self.journal.append("MOOMOO_READ_ONLY_RECONNECTED", links={"session_identity": self.session_identity})

    def _refresh(self) -> tuple[MoomooAccount, ...]:
        if not self.transport.is_connected() or not self.transport.heartbeat():
            self.guard.disarm("OPEND_SESSION_UNAVAILABLE")
            raise MoomooSafetyError("OPEND_SESSION_UNAVAILABLE")
        self.guard.heartbeat_ok()
        accounts = self.transport.discover_accounts()
        selected_before = self.guard.selected_acc_id
        self.guard.observe_accounts(accounts)
        if selected_before is not None:
            still_exact = tuple(
                row for row in accounts
                if row.acc_id == selected_before and row.is_compatible_us_simulation
            )
            if len(still_exact) != 1:
                raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        return accounts

    def discover_accounts(self) -> tuple[MoomooAccount, ...]:
        """Read-only discovery returns only non-sensitive documented account fields."""
        return self._refresh()

    def verify_simulate_account(self) -> MoomooAccount:
        try:
            self._refresh()
            account = self.guard.verify_simulate_account()
        except PermissionError as exc:
            code = MOOMOO_REAL_EXECUTION_FORBIDDEN
            self.journal.append("MOOMOO_ACCOUNT_REJECTED", links={"session_identity": self.session_identity}, payload={"code": code})
            raise MoomooSafetyError(code) from exc
        except MoomooSafetyError:
            self.journal.append("MOOMOO_ACCOUNT_REJECTED", links={"session_identity": self.session_identity}, payload={"code": self.guard.disarm_reason})
            raise
        self.journal.append("MOOMOO_SIMULATE_ACCOUNT_VERIFIED", links={"session_identity": self.session_identity})
        return account

    def _selected(self) -> str:
        if (
            self.guard.selected_acc_id is None
            or self.guard.selected_account is None
            or self.guard.selected_account.trd_env != "SIMULATE"
            or self.config.expected_acc_id is None
            or self.guard.selected_acc_id != str(self.config.expected_acc_id)
            or self.guard.state is MoomooSessionState.DISARMED
        ):
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        return self.guard.selected_acc_id

    def _ensure_simulate(self, rows: object) -> None:
        values: list[str] = []
        if isinstance(rows, (tuple, list)):
            values.extend(str(getattr(row, "trd_env", "")).upper() for row in rows)
        else:
            values.append(str(getattr(rows, "trd_env", "")).upper())
        if any(value not in {"SIMULATE", ""} for value in values):
            self.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)

    def account_balance(self) -> MoomooBalance:
        self._refresh(); row = self.transport.account_balance(self._selected(), trd_env="SIMULATE"); self._ensure_simulate(row); return row

    def positions(self) -> tuple[MoomooPosition, ...]:
        self._refresh(); rows = self.transport.positions(self._selected(), trd_env="SIMULATE"); self._ensure_simulate(rows); return rows

    def current_orders(self) -> tuple[MoomooOrder, ...]:
        self._refresh(); rows = self.transport.current_orders(self._selected(), trd_env="SIMULATE"); self._ensure_simulate(rows); return rows

    def historical_orders(self) -> tuple[MoomooOrder, ...]:
        self._refresh(); rows = self.transport.historical_orders(self._selected(), trd_env="SIMULATE"); self._ensure_simulate(rows); return rows

    def deals(self) -> tuple[MoomooDeal, ...]:
        self._refresh()
        if not self.transport.capabilities.supports_current_deal_query:
            return ()
        rows = self.transport.deals(self._selected(), trd_env="SIMULATE")
        self._ensure_simulate(rows)
        return rows

    def quote_snapshot(self, code: str) -> MoomooQuote:
        self._refresh()
        if not code.startswith("US."):
            raise MoomooSafetyError("UNSUPPORTED_SECURITY")
        return self.transport.quote_snapshot(code)

    def order_status(self, order_intent_id: str) -> MoomooOrder | None:
        self._refresh(); order_id = self.journal.broker_order_ids.get(order_intent_id)
        if order_id is None:
            raise MoomooSafetyError("BROKER_ORDER_ID_UNAVAILABLE")
        row = self.transport.order_status(str(order_id), self._selected(), trd_env="SIMULATE")
        if row is not None: self._ensure_simulate(row)
        return row

    def _manifest(self, intent: MoomooOrderIntent):
        try:
            manifest = verify_run_manifest(root=self.artifact_root, run_id=intent.source_run_id)
        except Exception as exc:
            raise MoomooSafetyError("SOURCE_MANIFEST_VERIFICATION_FAILED") from exc
        if manifest.manifest_hash != intent.source_manifest_hash or manifest.methodology_version != intent.methodology_version:
            raise MoomooSafetyError("SOURCE_MANIFEST_BINDING_MISMATCH")
        return manifest

    def _links(self, intent: MoomooOrderIntent, risk: RiskDecision, proposal: ProposedAllocation, quote: MoomooQuote) -> Mapping[str, object]:
        return {
            "source_artifact_manifest": {"run_id": intent.source_run_id, "manifest_hash": intent.source_manifest_hash, "methodology_version": intent.methodology_version},
            "decision": {"decision_id": risk.decision_id, "decision_hash": risk.decision_hash, "checked_at": risk.checked_at},
            "portfolio_proposal": {"proposal_id": proposal.proposal_id, "proposal_hash": proposal.proposal_hash},
            "risk_gate_output": jsonable(risk), "session_identity": self.session_identity,
            "quote": jsonable(quote), "order_request": jsonable(intent),
        }

    def reconcile(self) -> MoomooReconciliation:
        self._refresh(); acc_id = self._selected()
        try:
            balance = self.transport.account_balance(acc_id, trd_env="SIMULATE")
            positions = self.transport.positions(acc_id, trd_env="SIMULATE")
            current = self.transport.current_orders(acc_id, trd_env="SIMULATE")
            historical = self.transport.historical_orders(acc_id, trd_env="SIMULATE")
            deals: tuple[MoomooDeal, ...] = ()
            if self.transport.capabilities.supports_current_deal_query:
                deals = self.transport.deals(acc_id, trd_env="SIMULATE")
            for value in (balance, positions, current, historical, deals): self._ensure_simulate(value)
        except MoomooSafetyError:
            raise
        except Exception as exc:
            self.guard.disarm("RECONCILIATION_FAILED")
            self.journal.append("MOOMOO_RECONCILIATION_FAILED", links={"session_identity": self.session_identity}, payload={"error": sanitized_provider_error(exc)})
            raise MoomooSafetyError("RECONCILIATION_FAILED") from exc
        orders_by_ref = {row.remark: row for row in (*current, *historical) if row.remark}
        orders_by_id = {row.order_id: row for row in (*current, *historical)}
        unresolved: list[str] = []
        for intent_id in self.journal.active_intent_ids:
            state = self.journal.state(intent_id)
            if state in {IntentState.CREATED, IntentState.PREFLIGHT_PASSED}: continue
            stored_id = self.journal.broker_order_ids.get(intent_id)
            order: MoomooOrder | None = None
            if stored_id is not None:
                # The exact order-id query is primary. Bulk current/history
                # rows are only fallback evidence and are matched by ID.
                order = self.transport.order_status(str(stored_id), acc_id, trd_env="SIMULATE")
                if order is not None:
                    self._ensure_simulate(order)
                    if order.order_id != str(stored_id):
                        order = None
                if order is None:
                    order = orders_by_id.get(str(stored_id))
            else:
                # A lost acknowledgement may leave no local broker ID. The
                # durable remark is the exact intent reference; symbol-only
                # matching is never used.
                order = orders_by_ref.get(intent_id)
            target = _intent_state(order) if order else None
            if target in {IntentState.PARTIALLY_FILLED, IntentState.FILLED, IntentState.CANCELLED} and order is not None and order.dealt_quantity > 0 and order.side.upper() == "BUY":
                position_qty = sum(
                    row.quantity for row in positions
                    if row.acc_id == acc_id and row.trd_env == "SIMULATE" and row.code == order.code
                )
                if position_qty + 1e-9 < order.dealt_quantity:
                    target = IntentState.UNKNOWN_BROKER_STATE
            if target is None:
                if state is not IntentState.UNKNOWN_BROKER_STATE:
                    self.journal.append("MOOMOO_RECONCILIATION_UNRESOLVED", order_intent_id=intent_id, decision_id=self.journal.intent_decisions.get(intent_id), intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=stored_id, links=self.journal.intent_links.get(intent_id, {}), payload={"code": "ORDER_NOT_PROVABLY_RESOLVED"})
                unresolved.append(intent_id); continue
            if target is IntentState.ACKNOWLEDGED and state is IntentState.CANCEL_PENDING:
                unresolved.append(intent_id); continue
            if target != state:
                self.journal.append("MOOMOO_RECONCILED_STATUS", order_intent_id=intent_id, decision_id=self.journal.intent_decisions.get(intent_id), intent_state=target, broker_order_id=order.order_id if order else stored_id, links=self.journal.intent_links.get(intent_id, {}), payload={"broker_order": jsonable(order) if order else None, "deals": []})
        snapshot = MoomooReconciliation(datetime.now(timezone.utc), balance, tuple(positions), tuple(current), tuple(historical), tuple(deals), tuple(sorted(set(unresolved) | set(self.journal.unresolved_intent_ids))))
        self.last_reconciliation = snapshot; self.guard.reconciliation_ok()
        self.journal.append("MOOMOO_RECONCILIATION_COMPLETED", links={"session_identity": self.session_identity}, payload={"result": jsonable(snapshot)})
        return snapshot

    def _validate(self, intent: MoomooOrderIntent, risk: RiskDecision, proposal: ProposedAllocation, *, now: datetime, compare_quote: bool) -> tuple[MoomooQuote, MoomooReconciliation]:
        reasons: list[str] = []
        try: self._manifest(intent)
        except MoomooSafetyError as exc: reasons.extend(exc.reasons)
        if risk.status is not RiskStatus.APPROVED or not verify_risk_decision_hash(risk): reasons.append("RISK_GATE_NOT_PASSED")
        if proposal.status is not ProposalStatus.ACCEPTED or not verify_proposed_allocation_hash(proposal): reasons.append("PORTFOLIO_PROPOSAL_INVALID")
        if risk.decision_id != intent.decision_id or risk.decision_hash != intent.risk_result_hash: reasons.append("RISK_DECISION_BINDING_MISMATCH")
        if proposal.proposal_hash != intent.portfolio_proposal_hash or risk.proposal_hash != proposal.proposal_hash: reasons.append("PORTFOLIO_PROPOSAL_BINDING_MISMATCH")
        if risk.source_manifest_hash != intent.source_manifest_hash or proposal.source_manifest_hash != intent.source_manifest_hash: reasons.append("SOURCE_MANIFEST_BINDING_MISMATCH")
        if risk.checked_at > now or now - risk.checked_at > self.policy.max_decision_age: reasons.append("STALE_OR_FUTURE_DECISION")
        if intent.created_at > now or intent.expires_at <= now: reasons.append("ORDER_INTENT_EXPIRED_OR_FUTURE")
        utc_time = now.astimezone(timezone.utc).time().replace(tzinfo=None)
        if now.weekday() not in self.policy.allowed_weekdays or not (self.policy.allowed_session_start_utc <= utc_time <= self.policy.allowed_session_end_utc): reasons.append("OUTSIDE_ALLOWED_TRADING_SESSION")
        if intent.currency != "USD" or intent.order_type != "LIMIT" or intent.time_in_force != "DAY" or intent.security_kind not in {MoomooSecurityKind.STOCK, MoomooSecurityKind.ETF}: reasons.append("UNSUPPORTED_SECURITY_OR_ORDER_TYPE")
        if not isclose(intent.quantity, floor(intent.quantity), abs_tol=1e-12): reasons.append("FRACTIONAL_QUANTITY_UNSUPPORTED")
        quote = self.quote_snapshot(intent.code)
        if quote.status != "AVAILABLE" or quote.quoted_at is None or quote.executable_reference(intent.side) is None: reasons.append("QUOTE_UNAVAILABLE")
        if not quote.v1_eligible_security: reasons.append("UNSUPPORTED_SECURITY")
        if quote.quoted_at is not None and (quote.quoted_at > now or now - quote.quoted_at > self.policy.max_quote_age): reasons.append("STALE_OR_FUTURE_QUOTE")
        spread = quote.spread_fraction
        if spread is None or spread > self.policy.max_quote_spread: reasons.append("QUOTE_SPREAD_UNAVAILABLE_OR_TOO_WIDE")
        reference = quote.executable_reference(intent.side)
        if reference is not None and abs(intent.limit_price - reference) / reference > self.policy.maximum_limit_price_deviation: reasons.append("LIMIT_PRICE_DEVIATION_EXCEEDED")
        if compare_quote and quote != intent.quote: reasons.append("ORDER_INTENT_QUOTE_NOT_VERIFIED")
        snapshot = self.reconcile()
        if snapshot.unresolved_intent_ids: reasons.append("UNKNOWN_BROKER_STATE")
        if any(item != intent.order_intent_id for item in self.journal.active_intent_ids): reasons.append("OUTSTANDING_ORDER_BLOCKS_NEW_SUBMISSION")
        if self.journal.decision_used(intent.decision_id, except_intent_id=intent.order_intent_id): reasons.append("DUPLICATE_DECISION")
        if self.journal.state(intent.order_intent_id) not in {None, IntentState.CREATED, IntentState.PREFLIGHT_PASSED}: reasons.append("DUPLICATE_ORDER_INTENT")
        side = "BUY" if intent.side is MoomooOrderSide.BUY else "SELL"
        matching = tuple(trade for trade in risk.trades if trade.symbol == intent.ticker and trade.side == side and isclose(trade.quantity, intent.quantity, abs_tol=1e-9) and isclose(trade.quote_price, intent.decision_price, abs_tol=1e-9))
        if len(matching) != 1: reasons.append("ORDER_INTENT_NOT_BOUND_TO_RISK_TRADE")
        if intent.side is MoomooOrderSide.SELL_TO_REDUCE_EXISTING_LONG:
            sellable = sum(row.can_sell_quantity for row in snapshot.positions if row.acc_id == self.guard.selected_acc_id and row.trd_env == "SIMULATE" and row.code == intent.code and row.quantity > 0)
            if intent.quantity > sellable + 1e-9: reasons.append("SELL_WOULD_CREATE_SHORT")
        else:
            balance = snapshot.balance
            if balance.currency != "USD" or balance.cash is None or balance.available_cash is None or not isfinite(balance.cash) or not isfinite(balance.available_cash) or min(balance.cash, balance.available_cash) < 0: reasons.append("USD_AVAILABLE_CASH_UNAVAILABLE")
            elif intent.quantity * intent.limit_price > min(balance.cash, balance.available_cash) + 1e-9: reasons.append("AVAILABLE_CASH_EXCEEDED")
        if reasons: raise MoomooSafetyError(*reasons)
        return quote, snapshot

    def prepare_order(self, intent: MoomooOrderIntent, risk: RiskDecision, proposal: ProposedAllocation, *, kill_switch_state: PaperKillSwitchState, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        if not isinstance(intent, MoomooOrderIntent):
            raise MoomooSafetyError("PRODUCTION_EXECUTION_INTENT_REQUIRED")
        if self.guard.state is not MoomooSessionState.SIMULATE_VERIFIED: raise MoomooSafetyError("SIMULATE_ACCOUNT_NOT_EXPLICITLY_VERIFIED")
        try: quote, snapshot = self._validate(intent, risk, proposal, now=now, compare_quote=True)
        except MoomooSafetyError as exc:
            if self.journal.state(intent.order_intent_id) is None:
                links = {"source_artifact_manifest": {"run_id": intent.source_run_id, "manifest_hash": intent.source_manifest_hash, "methodology_version": intent.methodology_version}, "order_request": jsonable(intent), "session_identity": self.session_identity}
                self.journal.append("MOOMOO_ORDER_CREATED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.CREATED, links=links)
                self.journal.append("MOOMOO_PREFLIGHT_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.REJECTED, links=links, payload={"rejection_reasons": exc.reasons})
            raise
        links = self._links(intent, risk, proposal, quote)
        if self.journal.state(intent.order_intent_id) is None: self.journal.append("MOOMOO_ORDER_CREATED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.CREATED, links=links)
        if self.journal.state(intent.order_intent_id) is IntentState.CREATED: self.journal.append("MOOMOO_PREFLIGHT_PASSED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.PREFLIGHT_PASSED, links=links, payload={"reconciliation": jsonable(snapshot)})
        try: self.guard.arm(decision_id=intent.decision_id, risk_ok=True, manifest_ok=True, unused=not self.journal.decision_used(intent.decision_id, except_intent_id=intent.order_intent_id), reconciled=not snapshot.unresolved_intent_ids, kill_switch_state=kill_switch_state)
        except PermissionError as exc:
            self.journal.append("MOOMOO_ARMING_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, links=links, payload={"code": "MOOMOO_SIMULATE_ARMING_REJECTED"})
            raise MoomooSafetyError("MOOMOO_SIMULATE_ARMING_REJECTED") from exc
        self.journal.append("MOOMOO_SIMULATE_EXECUTION_ARMED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, links=links)

    def submit_order(self, intent: MoomooOrderIntent, risk: RiskDecision, proposal: ProposedAllocation, *, kill_switch_state: PaperKillSwitchState, now: datetime | None = None) -> MoomooAcknowledgement:
        if not isinstance(intent, MoomooOrderIntent):
            raise MoomooSafetyError("PRODUCTION_EXECUTION_INTENT_REQUIRED")
        now = now or datetime.now(timezone.utc); accounts = self._refresh(); self.guard.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if self.guard.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER: self.guard.disarm("KILL_SWITCH_NOT_ARMED"); raise MoomooSafetyError("KILL_SWITCH_NOT_ARMED")
        try: self.guard.assert_submission_safe(decision_id=intent.decision_id, accounts=accounts)
        except PermissionError as exc: raise MoomooSafetyError(self.guard.disarm_reason or SIMULATE_ACCOUNT_UNAVAILABLE) from exc
        if self.journal.state(intent.order_intent_id) is not IntentState.PREFLIGHT_PASSED: raise MoomooSafetyError("ORDER_INTENT_NOT_PREFLIGHT_PASSED")
        quote, _ = self._validate(intent, risk, proposal, now=now, compare_quote=False); links = self._links(intent, risk, proposal, quote)
        try:
            self.guard.assert_submission_safe(
                decision_id=intent.decision_id,
                accounts=self.transport.discover_accounts(),
            )
        except PermissionError as exc:
            code = self.guard.disarm_reason or SIMULATE_ACCOUNT_UNAVAILABLE
            raise MoomooSafetyError(code) from exc
        self.journal.append("MOOMOO_SUBMITTING", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.SUBMITTING, links=links)
        try: ack = self.transport.place_limit_order(intent, self._selected(), trd_env="SIMULATE")
        except Exception as exc:
            self.journal.append("MOOMOO_ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"error": sanitized_provider_error(exc)}); self.guard.disarm("UNKNOWN_BROKER_STATE"); raise MoomooSafetyError("UNKNOWN_BROKER_STATE") from exc
        if ack.trd_env is not None and str(ack.trd_env).upper() != "SIMULATE":
            self.journal.append("MOOMOO_REAL_RESPONSE_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=ack.order_id, links=links, payload={"code": MOOMOO_REAL_EXECUTION_FORBIDDEN}); self.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN); raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        if ack.ambiguous or ack.order_id is None:
            self.journal.append("MOOMOO_ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"broker_acknowledgement": jsonable(ack)}); self.guard.disarm("UNKNOWN_BROKER_STATE"); raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        state = IntentState.ACKNOWLEDGED if ack.accepted else IntentState.REJECTED
        self.journal.append("MOOMOO_BROKER_ACKNOWLEDGEMENT", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=state, broker_order_id=ack.order_id, links=links, payload={"broker_acknowledgement": jsonable(ack)})
        if not ack.accepted: self.guard.disarm("BROKER_REJECTED")
        return ack

    def resume_order_control(self, intent: MoomooOrderIntent, risk: RiskDecision, proposal: ProposedAllocation, *, kill_switch_state: PaperKillSwitchState) -> None:
        if self.guard.state is not MoomooSessionState.SIMULATE_VERIFIED: raise MoomooSafetyError("SIMULATE_ACCOUNT_NOT_EXPLICITLY_VERIFIED")
        self._manifest(intent)
        if risk.status is not RiskStatus.APPROVED or not verify_risk_decision_hash(risk) or proposal.status is not ProposalStatus.ACCEPTED or not verify_proposed_allocation_hash(proposal) or risk.decision_id != intent.decision_id or risk.decision_hash != intent.risk_result_hash or proposal.proposal_hash != intent.portfolio_proposal_hash: raise MoomooSafetyError("EXISTING_ORDER_BINDING_INVALID")
        snapshot = self.reconcile(); state = self.journal.state(intent.order_intent_id)
        if snapshot.unresolved_intent_ids or state not in {IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED, IntentState.CANCEL_PENDING}: raise MoomooSafetyError("EXISTING_ORDER_NOT_PROVABLY_CONTROLLABLE")
        self.guard.arm(decision_id=intent.decision_id, risk_ok=True, manifest_ok=True, unused=True, reconciled=True, kill_switch_state=kill_switch_state)
        self.journal.append("MOOMOO_EXISTING_ORDER_CONTROL_ARMED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, broker_order_id=self.journal.broker_order_ids.get(intent.order_intent_id), links=self.journal.intent_links.get(intent.order_intent_id, {}))

    def cancel_order(self, order_intent_id: str, *, kill_switch_state: PaperKillSwitchState) -> MoomooAcknowledgement:
        if self.journal.state(order_intent_id) not in {IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED}: raise MoomooSafetyError("ORDER_NOT_CANCELLABLE")
        decision_id = self.journal.intent_decisions[order_intent_id]; accounts = self._refresh(); self.guard.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if self.guard.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER: self.guard.disarm("KILL_SWITCH_NOT_ARMED"); raise MoomooSafetyError("KILL_SWITCH_NOT_ARMED")
        try:
            self.guard.assert_submission_safe(decision_id=decision_id, accounts=accounts)
        except PermissionError as exc:
            code = self.guard.disarm_reason or SIMULATE_ACCOUNT_UNAVAILABLE
            raise MoomooSafetyError(code) from exc
        order_id = self.journal.broker_order_ids.get(order_intent_id)
        if order_id is None: raise MoomooSafetyError("BROKER_ORDER_ID_UNAVAILABLE")
        links = self.journal.intent_links.get(order_intent_id, {}); self.journal.append("MOOMOO_CANCEL_PENDING", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.CANCEL_PENDING, broker_order_id=order_id, links=links)
        try: ack = self.transport.cancel_order(str(order_id), self._selected(), trd_env="SIMULATE")
        except Exception as exc:
            self.journal.append("MOOMOO_CANCEL_UNCERTAIN", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=order_id, links=links, payload={"error": sanitized_provider_error(exc)}); self.guard.disarm("UNKNOWN_BROKER_STATE"); raise MoomooSafetyError("UNKNOWN_BROKER_STATE") from exc
        if ack.trd_env is not None and str(ack.trd_env).upper() != "SIMULATE":
            self.journal.append(
                "MOOMOO_REAL_CANCEL_RESPONSE_REJECTED",
                order_intent_id=order_intent_id,
                decision_id=decision_id,
                intent_state=IntentState.UNKNOWN_BROKER_STATE,
                broker_order_id=order_id,
                links=links,
                payload={"code": MOOMOO_REAL_EXECUTION_FORBIDDEN},
            )
            self.guard.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise MoomooSafetyError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        if ack.ambiguous:
            self.journal.append("MOOMOO_CANCEL_UNCERTAIN", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=order_id, links=links, payload={"broker_acknowledgement": jsonable(ack)}); self.guard.disarm("UNKNOWN_BROKER_STATE"); raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
        snapshot = self.reconcile()
        if self.journal.state(order_intent_id) is not IntentState.CANCELLED:
            if self.journal.state(order_intent_id) is IntentState.UNKNOWN_BROKER_STATE or order_intent_id in snapshot.unresolved_intent_ids:
                self.guard.disarm("UNKNOWN_BROKER_STATE")
                raise MoomooSafetyError("UNKNOWN_BROKER_STATE")
            raise MoomooSafetyError("CANCEL_NOT_CONFIRMED_BY_BROKER")
        return ack
