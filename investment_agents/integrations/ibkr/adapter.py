"""Paper-only IBKR adapter with deterministic preflight and reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from math import floor, isclose, isfinite
from pathlib import Path
from typing import Mapping

from investment_agents.path_safety import sanitized_provider_error
from investment_agents.portfolio import ProposedAllocation, ProposalStatus, verify_proposed_allocation_hash
from investment_agents.research import verify_run_manifest
from investment_agents.risk import RiskDecision, RiskStatus, verify_risk_decision_hash

from .audit import PaperExecutionJournal, jsonable
from .backend import IBKRTransport
from .models import (
    AccountSummary, BrokerAcknowledgement, BrokerExecution, BrokerOrder,
    BrokerPosition, ExecutionSafetyPolicy, IBKRConnectionConfig, IBKRQuote,
    IntentState, OrderSide, PaperKillSwitchState, PaperOrderIntent,
    ReconciliationSnapshot, ResolvedContract, SessionState,
)
from .session import BrokerSessionGuard, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN


class PaperSafetyError(PermissionError):
    """Machine-readable fail-closed Paper safety rejection."""

    def __init__(self, *reasons: str) -> None:
        self.reasons = tuple(dict.fromkeys(reasons or ("PAPER_SAFETY_REJECTED",)))
        super().__init__(";".join(self.reasons))


def _status_from_order(order: BrokerOrder) -> IntentState:
    normalized = order.status.replace(" ", "").upper()
    if normalized in {"FILLED"} or (order.remaining_quantity == 0 and order.filled_quantity > 0):
        return IntentState.FILLED
    if normalized in {"CANCELLED", "APICANCELLED"}:
        return IntentState.CANCELLED
    if normalized in {"REJECTED", "INACTIVE"}:
        return IntentState.REJECTED
    if order.filled_quantity > 0:
        return IntentState.PARTIALLY_FILLED
    return IntentState.ACKNOWLEDGED


class IBKRPaperAdapter:
    """The only order-capable boundary; it has no live transition or override."""

    def __init__(
        self,
        *,
        config: IBKRConnectionConfig,
        transport: IBKRTransport,
        audit_path: Path,
        artifact_root: Path,
        policy: ExecutionSafetyPolicy | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.policy = policy or ExecutionSafetyPolicy()
        self.artifact_root = artifact_root.resolve()
        self.guard = BrokerSessionGuard(config)
        self.journal = PaperExecutionJournal(audit_path)
        self.last_reconciliation: ReconciliationSnapshot | None = None

    def connect(self) -> None:
        """Connect in READ_ONLY_PROBE only; never implicitly verify or arm."""
        info = self.transport.connect(
            self.config.host, self.config.port, self.config.client_id, read_only=True,
        )
        self.guard.connected(info)
        self.journal.append("READ_ONLY_CONNECTED", links={"session": self.session_identity})

    def disconnect(self) -> None:
        self.transport.disconnect()
        self.guard.disarm("DISCONNECTED")
        self.journal.append("DISCONNECTED", links={"session": self.session_identity})

    def reconnect(self) -> None:
        """Reconnect only to READ_ONLY_PROBE; verification/arming never carries over."""
        if self.transport.is_connected():
            self.transport.disconnect()
        self.guard.disarm("RECONNECTING")
        info = self.transport.connect(
            self.config.host, self.config.port, self.config.client_id, read_only=True,
        )
        self.guard.connected(info)
        self.journal.append("READ_ONLY_RECONNECTED", links={"session": self.session_identity})

    @property
    def session_identity(self) -> Mapping[str, object]:
        return {
            "host": self.guard.host,
            "port": self.guard.port,
            "client_id": self.guard.client_id,
            "connected_at": self.guard.connected_at,
            "broker_server_version": self.guard.broker_server_version,
            "broker_reported_account_ids": self.guard.broker_reported_account_ids,
            "selected_account": self.guard.selected_account,
            "paper_verification_result": self.guard.paper_verification_result,
            "session_state": self.guard.current_session_state,
            "last_successful_heartbeat": self.guard.last_successful_heartbeat,
            "last_successful_reconciliation": self.guard.last_successful_reconciliation,
            "kill_switch_state": self.guard.kill_switch_state,
        }

    def _refresh_session(self) -> tuple[str, ...]:
        if not self.transport.is_connected() or not self.transport.heartbeat():
            self.guard.disarm("HEARTBEAT_FAILED")
            raise PaperSafetyError("BROKER_SESSION_UNAVAILABLE")
        self.guard.heartbeat_ok()
        accounts = tuple(sorted(set(self.transport.discover_accounts())))
        self.guard.observe_accounts(accounts)
        return accounts

    def verify_paper_account(self) -> str:
        self._refresh_session()
        try:
            account = self.guard.verify_paper_account()
        except PermissionError as exc:
            self.journal.append("PAPER_ACCOUNT_REJECTED", links={"session": self.session_identity}, payload={"code": LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN})
            raise PaperSafetyError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN) from exc
        self.journal.append("PAPER_ACCOUNT_VERIFIED", links={"session": self.session_identity})
        return account

    # READ_ONLY_PROBE operations.
    def account_summary(self) -> AccountSummary:
        accounts = self._refresh_session()
        if len(accounts) != 1:
            raise PaperSafetyError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
        return self.transport.account_summary(accounts[0])

    def positions(self) -> tuple[BrokerPosition, ...]:
        self._refresh_session()
        return self.transport.positions()

    def open_orders(self) -> tuple[BrokerOrder, ...]:
        self._refresh_session()
        return self.transport.open_orders()

    def completed_orders(self) -> tuple[BrokerOrder, ...]:
        self._refresh_session()
        return self.transport.completed_orders()

    def executions(self) -> tuple[BrokerExecution, ...]:
        self._refresh_session()
        return self.transport.executions()

    def order_status(self, order_intent_id: str) -> BrokerOrder | None:
        """Inspect only an order already bound in the local Paper journal."""
        self._refresh_session()
        broker_order_id = self.journal.broker_order_ids.get(order_intent_id)
        if broker_order_id is None:
            raise PaperSafetyError("BROKER_ORDER_ID_UNAVAILABLE")
        return self.transport.order_status(broker_order_id)

    def resolve_contract(self, ticker: str, *, security_type: str = "STK", currency: str = "USD", exchange: str = "SMART") -> ResolvedContract:
        self._refresh_session()
        if security_type != "STK" or currency != "USD" or exchange.upper() in {"BAG", "IDEALPRO"}:
            raise PaperSafetyError("UNSUPPORTED_INSTRUMENT")
        matches = self.transport.contract_details(
            symbol=ticker, security_type=security_type, currency=currency, exchange=exchange,
        )
        compatible = tuple(row for row in matches if row.security_type == "STK" and row.currency == "USD" and row.con_id > 0)
        if len(compatible) != 1:
            raise PaperSafetyError("CONTRACT_RESOLUTION_AMBIGUOUS_OR_UNAVAILABLE")
        return compatible[0]

    def quote_snapshot(self, contract: ResolvedContract) -> IBKRQuote:
        self._refresh_session()
        if contract.security_type != "STK" or contract.currency != "USD":
            raise PaperSafetyError("UNSUPPORTED_INSTRUMENT")
        return self.transport.quote_snapshot(contract)

    def _verified_manifest(self, intent: PaperOrderIntent):
        try:
            manifest = verify_run_manifest(root=self.artifact_root, run_id=intent.source_run_id)
        except Exception as exc:
            raise PaperSafetyError("SOURCE_MANIFEST_VERIFICATION_FAILED") from exc
        if (
            manifest.manifest_hash != intent.source_manifest_hash
            or manifest.methodology_version != intent.methodology_version
        ):
            raise PaperSafetyError("SOURCE_MANIFEST_BINDING_MISMATCH")
        return manifest

    def _base_links(
        self,
        intent: PaperOrderIntent,
        risk: RiskDecision,
        proposal: ProposedAllocation,
        contract: ResolvedContract,
        verified_quote: IBKRQuote,
    ) -> Mapping[str, object]:
        return {
            "source_artifact_manifest": {
                "run_id": intent.source_run_id,
                "manifest_hash": intent.source_manifest_hash,
                "methodology_version": intent.methodology_version,
            },
            "decision": {"decision_id": risk.decision_id, "decision_hash": risk.decision_hash, "checked_at": risk.checked_at},
            "portfolio_proposal": {"proposal_id": proposal.proposal_id, "proposal_hash": proposal.proposal_hash},
            "risk_gate_output": jsonable(risk),
            "session_identity": self.session_identity,
            "quote": jsonable(verified_quote),
            "contract_details": jsonable(contract),
            "order_request": jsonable(intent),
        }

    def reconcile(self) -> ReconciliationSnapshot:
        accounts = self._refresh_session()
        if len(accounts) != 1 or self.guard.selected_account not in {None, accounts[0]}:
            self.guard.disarm(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
            raise PaperSafetyError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
        account = accounts[0]
        try:
            summary = self.transport.account_summary(account)
            positions = self.transport.positions()
            open_orders = self.transport.open_orders()
            completed = self.transport.completed_orders()
            executions = self.transport.executions()
        except Exception as exc:
            self.guard.disarm("RECONCILIATION_FAILED")
            self.journal.append(
                "RECONCILIATION_FAILED", links={"session": self.session_identity},
                payload={"error": sanitized_provider_error(exc)},
            )
            raise PaperSafetyError("RECONCILIATION_FAILED") from exc
        broker_orders = {row.order_ref: row for row in (*open_orders, *completed) if row.order_ref}
        executions_by_ref: dict[str, list[BrokerExecution]] = {}
        for execution in executions:
            if execution.order_ref:
                executions_by_ref.setdefault(execution.order_ref, []).append(execution)

        unresolved: list[str] = []
        for intent_id in self.journal.active_intent_ids:
            local_state = self.journal.state(intent_id)
            if local_state in {IntentState.CREATED, IntentState.PREFLIGHT_PASSED}:
                continue
            order = broker_orders.get(intent_id)
            fills = executions_by_ref.get(intent_id, [])
            target_state = _status_from_order(order) if order is not None else None
            if target_state is None and fills:
                target_state = IntentState.PARTIALLY_FILLED
            if target_state is None:
                if local_state is not IntentState.UNKNOWN_BROKER_STATE:
                    self.journal.append(
                        "RECONCILIATION_UNRESOLVED", order_intent_id=intent_id,
                        decision_id=self.journal.intent_decisions.get(intent_id),
                        intent_state=IntentState.UNKNOWN_BROKER_STATE,
                        broker_order_id=self.journal.broker_order_ids.get(intent_id),
                        links=self.journal.intent_links.get(intent_id, {}),
                        payload={"code": "BROKER_ORDER_NOT_PROVABLY_RESOLVED"},
                    )
                unresolved.append(intent_id)
                continue
            if target_state is IntentState.ACKNOWLEDGED and local_state is IntentState.CANCEL_PENDING:
                unresolved.append(intent_id)
                continue
            if target_state != local_state:
                self.journal.append(
                    "RECONCILED_STATUS", order_intent_id=intent_id,
                    decision_id=self.journal.intent_decisions.get(intent_id), intent_state=target_state,
                    broker_order_id=order.broker_order_id if order else self.journal.broker_order_ids.get(intent_id),
                    links=self.journal.intent_links.get(intent_id, {}),
                    payload={"broker_order": jsonable(order) if order else None, "fills": jsonable(fills)},
                )
        snapshot = ReconciliationSnapshot(
            datetime.now(timezone.utc), summary, tuple(positions), tuple(open_orders),
            tuple(completed), tuple(executions), tuple(sorted(set(unresolved) | set(self.journal.unresolved_intent_ids))),
        )
        self.last_reconciliation = snapshot
        self.guard.reconciliation_ok()
        self.journal.append(
            "RECONCILIATION_COMPLETED", links={"session": self.session_identity},
            payload={"result": jsonable(snapshot)},
        )
        return snapshot

    def _validate_order(
        self,
        intent: PaperOrderIntent,
        risk: RiskDecision,
        proposal: ProposedAllocation,
        *,
        now: datetime,
        compare_intent_quote: bool,
    ) -> tuple[ResolvedContract, IBKRQuote, ReconciliationSnapshot]:
        reasons: list[str] = []
        try:
            self._verified_manifest(intent)
        except PaperSafetyError as exc:
            reasons.extend(exc.reasons)
        if risk.status is not RiskStatus.APPROVED or not verify_risk_decision_hash(risk):
            reasons.append("RISK_GATE_NOT_PASSED")
        if proposal.status is not ProposalStatus.ACCEPTED or not verify_proposed_allocation_hash(proposal):
            reasons.append("PORTFOLIO_PROPOSAL_INVALID")
        if risk.decision_id != intent.decision_id or risk.decision_hash != intent.risk_result_hash:
            reasons.append("RISK_DECISION_BINDING_MISMATCH")
        if proposal.proposal_hash != intent.portfolio_proposal_hash or risk.proposal_hash != proposal.proposal_hash:
            reasons.append("PORTFOLIO_PROPOSAL_BINDING_MISMATCH")
        if risk.source_manifest_hash != intent.source_manifest_hash or proposal.source_manifest_hash != intent.source_manifest_hash:
            reasons.append("SOURCE_MANIFEST_BINDING_MISMATCH")
        if risk.checked_at > now or now - risk.checked_at > self.policy.max_decision_age:
            reasons.append("STALE_OR_FUTURE_DECISION")
        if intent.created_at > now or intent.expires_at <= now:
            reasons.append("ORDER_INTENT_EXPIRED_OR_FUTURE")
        if now.weekday() not in self.policy.allowed_weekdays or not (
            self.policy.allowed_session_start_utc <= now.astimezone(timezone.utc).time().replace(tzinfo=None) <= self.policy.allowed_session_end_utc
        ):
            reasons.append("OUTSIDE_ALLOWED_TRADING_SESSION")
        if intent.time_in_force not in self.policy.allowed_time_in_force:
            reasons.append("UNSUPPORTED_TIME_IN_FORCE")
        if intent.security_type != "STK" or intent.currency != "USD" or intent.order_type != "LMT":
            reasons.append("UNSUPPORTED_INSTRUMENT_OR_ORDER_TYPE")
        fractional = not isclose(intent.quantity, floor(intent.quantity), abs_tol=1e-12)
        if fractional and not self.config.fractional_quantities_supported:
            reasons.append("FRACTIONAL_QUANTITY_NOT_CONFIGURED")

        contract: ResolvedContract | None = None
        quote: IBKRQuote | None = None
        try:
            contract = self.resolve_contract(intent.ticker, security_type=intent.security_type, currency=intent.currency, exchange=intent.exchange)
            if contract.con_id != intent.con_id:
                reasons.append("CONTRACT_ID_MISMATCH")
            if fractional and not contract.fractional_quantities_supported:
                reasons.append("CONTRACT_FRACTIONAL_QUANTITY_UNSUPPORTED")
            quote = self.quote_snapshot(contract)
        except PaperSafetyError as exc:
            reasons.extend(exc.reasons)
        if quote is not None:
            if quote.status != "AVAILABLE" or quote.executable_reference(intent.side) is None:
                reasons.append("QUOTE_UNAVAILABLE")
            if quote.quoted_at > now or now - quote.quoted_at > self.policy.max_quote_age:
                reasons.append("STALE_OR_FUTURE_QUOTE")
            spread = quote.spread_fraction
            if spread is None or spread > self.policy.max_quote_spread:
                reasons.append("QUOTE_SPREAD_UNAVAILABLE_OR_TOO_WIDE")
            reference = quote.executable_reference(intent.side)
            if reference is not None and abs(intent.limit_price - reference) / reference > self.policy.maximum_limit_price_deviation:
                reasons.append("LIMIT_PRICE_DEVIATION_EXCEEDED")
            if compare_intent_quote and quote != intent.quote:
                reasons.append("ORDER_INTENT_QUOTE_NOT_VERIFIED")

        snapshot = self.reconcile()
        if snapshot.unresolved_intent_ids:
            reasons.append("UNKNOWN_BROKER_STATE")
        other_active = tuple(item for item in self.journal.active_intent_ids if item != intent.order_intent_id)
        if other_active:
            reasons.append("OUTSTANDING_ORDER_BLOCKS_NEW_SUBMISSION")
        if self.journal.decision_used(intent.decision_id, except_intent_id=intent.order_intent_id):
            reasons.append("DUPLICATE_DECISION")
        current_state = self.journal.state(intent.order_intent_id)
        if current_state not in {None, IntentState.CREATED, IntentState.PREFLIGHT_PASSED}:
            reasons.append("DUPLICATE_ORDER_INTENT")

        trade_side = "BUY" if intent.side is OrderSide.BUY else "SELL"
        matching = tuple(
            trade for trade in risk.trades
            if trade.symbol == intent.ticker and trade.side == trade_side
            and isclose(trade.quantity, intent.quantity, rel_tol=0, abs_tol=1e-9)
            and isclose(trade.quote_price, intent.decision_price, rel_tol=0, abs_tol=1e-9)
        )
        if len(matching) != 1:
            reasons.append("ORDER_INTENT_NOT_BOUND_TO_RISK_TRADE")
        if intent.side is OrderSide.SELL_TO_REDUCE_EXISTING_LONG:
            long_quantity = sum(
                row.quantity for row in snapshot.positions
                if row.account_id == self.guard.selected_account and row.con_id == intent.con_id
                and row.security_type == "STK" and row.currency == "USD" and row.quantity > 0
            )
            if intent.quantity > long_quantity + 1e-9:
                reasons.append("SELL_WOULD_CREATE_SHORT")
        elif snapshot.account_summary.currency != "USD":
            reasons.append("USD_CASH_BALANCE_UNAVAILABLE")
        elif (
            snapshot.account_summary.settled_cash is None
            or snapshot.account_summary.available_funds is None
            or not isfinite(snapshot.account_summary.settled_cash)
            or not isfinite(snapshot.account_summary.available_funds)
            or snapshot.account_summary.settled_cash < 0
            or snapshot.account_summary.available_funds < 0
        ):
            reasons.append("SETTLED_OR_AVAILABLE_CASH_UNAVAILABLE")
        elif (
            intent.quantity * intent.limit_price > snapshot.account_summary.settled_cash + 1e-9
            or intent.quantity * intent.limit_price > snapshot.account_summary.available_funds + 1e-9
        ):
            reasons.append("SETTLED_OR_AVAILABLE_CASH_EXCEEDED")
        if reasons or contract is None or quote is None:
            raise PaperSafetyError(*reasons)
        return contract, quote, snapshot

    def prepare_order(
        self,
        intent: PaperOrderIntent,
        risk: RiskDecision,
        proposal: ProposedAllocation,
        *,
        kill_switch_state: PaperKillSwitchState,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        if self.guard.current_session_state is not SessionState.PAPER_VERIFIED:
            raise PaperSafetyError("PAPER_ACCOUNT_NOT_EXPLICITLY_VERIFIED")
        try:
            contract, quote, snapshot = self._validate_order(intent, risk, proposal, now=now, compare_intent_quote=True)
        except PaperSafetyError as exc:
            if self.journal.state(intent.order_intent_id) is None:
                links = {
                    "source_artifact_manifest": {"run_id": intent.source_run_id, "manifest_hash": intent.source_manifest_hash, "methodology_version": intent.methodology_version},
                    "order_request": jsonable(intent), "session_identity": self.session_identity,
                }
                self.journal.append("ORDER_CREATED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.CREATED, links=links)
                self.journal.append("PREFLIGHT_REJECTED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.REJECTED, links=links, payload={"rejection_reasons": exc.reasons})
            raise
        links = self._base_links(intent, risk, proposal, contract, quote)
        current = self.journal.state(intent.order_intent_id)
        if current is None:
            self.journal.append("ORDER_CREATED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.CREATED, links=links)
        if self.journal.state(intent.order_intent_id) is IntentState.CREATED:
            self.journal.append("PREFLIGHT_PASSED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.PREFLIGHT_PASSED, links=links, payload={"reconciliation": jsonable(snapshot)})
        try:
            self.guard.arm_for_paper(
                decision_id=intent.decision_id,
                risk_passed=True,
                manifest_verified=True,
                decision_unused=not self.journal.decision_used(intent.decision_id, except_intent_id=intent.order_intent_id),
                reconciliation_complete=not snapshot.unresolved_intent_ids,
                kill_switch_state=kill_switch_state,
            )
        except PermissionError as exc:
            self.journal.append(
                "PAPER_EXECUTION_ARMING_REJECTED", order_intent_id=intent.order_intent_id,
                decision_id=intent.decision_id, links=links,
                payload={"code": "PAPER_EXECUTION_ARMING_REJECTED"},
            )
            raise PaperSafetyError("PAPER_EXECUTION_ARMING_REJECTED") from exc
        self.journal.append("PAPER_EXECUTION_ARMED", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, links=links)

    def submit_order(
        self,
        intent: PaperOrderIntent,
        risk: RiskDecision,
        proposal: ProposedAllocation,
        *,
        kill_switch_state: PaperKillSwitchState,
        now: datetime | None = None,
    ) -> BrokerAcknowledgement:
        now = now or datetime.now(timezone.utc)
        accounts = self._refresh_session()
        self.guard.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if self.guard.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER:
            self.guard.disarm("KILL_SWITCH_NOT_ARMED")
            raise PaperSafetyError("KILL_SWITCH_NOT_ARMED")
        try:
            self.guard.assert_submission_safe(decision_id=intent.decision_id, account_ids=accounts)
        except PermissionError as exc:
            raise PaperSafetyError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN) from exc
        if self.journal.state(intent.order_intent_id) is not IntentState.PREFLIGHT_PASSED:
            raise PaperSafetyError("ORDER_INTENT_NOT_PREFLIGHT_PASSED")
        contract, quote, _ = self._validate_order(intent, risk, proposal, now=now, compare_intent_quote=False)
        links = self._base_links(intent, risk, proposal, contract, quote)
        self.guard.assert_submission_safe(decision_id=intent.decision_id, account_ids=tuple(sorted(set(self.transport.discover_accounts()))))
        self.journal.append("SUBMITTING", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.SUBMITTING, links=links)
        try:
            acknowledgement = self.transport.place_limit_order(intent, contract, self.guard.selected_account or "")
        except Exception as exc:
            error = sanitized_provider_error(exc)
            self.journal.append("ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"error": error})
            self.guard.disarm("UNKNOWN_BROKER_STATE")
            raise PaperSafetyError("UNKNOWN_BROKER_STATE") from exc
        if acknowledgement.ambiguous or acknowledgement.broker_order_id is None:
            self.journal.append("ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, links=links, payload={"broker_acknowledgement": jsonable(acknowledgement)})
            self.guard.disarm("UNKNOWN_BROKER_STATE")
            raise PaperSafetyError("UNKNOWN_BROKER_STATE")
        state = IntentState.ACKNOWLEDGED if acknowledgement.accepted else IntentState.REJECTED
        self.journal.append("BROKER_ACKNOWLEDGEMENT", order_intent_id=intent.order_intent_id, decision_id=intent.decision_id, intent_state=state, broker_order_id=acknowledgement.broker_order_id, links=links, payload={"broker_acknowledgement": jsonable(acknowledgement)})
        if not acknowledgement.accepted:
            self.guard.disarm("BROKER_REJECTED")
        return acknowledgement

    def resume_order_control(
        self,
        intent: PaperOrderIntent,
        risk: RiskDecision,
        proposal: ProposedAllocation,
        *,
        kill_switch_state: PaperKillSwitchState,
    ) -> None:
        """Re-arm control of an existing Paper order after restart/reconciliation.

        This cannot submit another order. It exists only to reconcile or cancel
        the exact already-bound broker order.
        """
        if self.guard.current_session_state is not SessionState.PAPER_VERIFIED:
            raise PaperSafetyError("PAPER_ACCOUNT_NOT_EXPLICITLY_VERIFIED")
        self._verified_manifest(intent)
        if (
            risk.status is not RiskStatus.APPROVED
            or not verify_risk_decision_hash(risk)
            or proposal.status is not ProposalStatus.ACCEPTED
            or not verify_proposed_allocation_hash(proposal)
            or risk.decision_id != intent.decision_id
            or risk.decision_hash != intent.risk_result_hash
            or proposal.proposal_hash != intent.portfolio_proposal_hash
            or risk.proposal_hash != proposal.proposal_hash
        ):
            raise PaperSafetyError("EXISTING_ORDER_BINDING_INVALID")
        snapshot = self.reconcile()
        state = self.journal.state(intent.order_intent_id)
        if snapshot.unresolved_intent_ids or state not in {
            IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED, IntentState.CANCEL_PENDING,
        }:
            raise PaperSafetyError("EXISTING_ORDER_NOT_PROVABLY_CONTROLLABLE")
        self.guard.arm_for_paper(
            decision_id=intent.decision_id, risk_passed=True, manifest_verified=True,
            decision_unused=True, reconciliation_complete=True,
            kill_switch_state=kill_switch_state,
        )
        self.journal.append(
            "EXISTING_ORDER_CONTROL_ARMED", order_intent_id=intent.order_intent_id,
            decision_id=intent.decision_id, broker_order_id=self.journal.broker_order_ids.get(intent.order_intent_id),
            links=self.journal.intent_links.get(intent.order_intent_id, {}),
        )

    def cancel_order(
        self,
        order_intent_id: str,
        *,
        kill_switch_state: PaperKillSwitchState,
    ) -> BrokerAcknowledgement:
        state = self.journal.state(order_intent_id)
        if state not in {IntentState.ACKNOWLEDGED, IntentState.PARTIALLY_FILLED}:
            raise PaperSafetyError("ORDER_NOT_CANCELLABLE")
        decision_id = self.journal.intent_decisions[order_intent_id]
        accounts = self._refresh_session()
        self.guard.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if self.guard.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER:
            self.guard.disarm("KILL_SWITCH_NOT_ARMED")
            raise PaperSafetyError("KILL_SWITCH_NOT_ARMED")
        self.guard.assert_submission_safe(decision_id=decision_id, account_ids=accounts)
        broker_order_id = self.journal.broker_order_ids.get(order_intent_id)
        if broker_order_id is None:
            raise PaperSafetyError("BROKER_ORDER_ID_UNAVAILABLE")
        links = self.journal.intent_links.get(order_intent_id, {})
        self.journal.append("CANCEL_PENDING", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.CANCEL_PENDING, broker_order_id=broker_order_id, links=links)
        try:
            acknowledgement = self.transport.cancel_order(broker_order_id)
        except Exception as exc:
            self.journal.append("CANCEL_ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=broker_order_id, links=links, payload={"error": sanitized_provider_error(exc)})
            self.guard.disarm("UNKNOWN_BROKER_STATE")
            raise PaperSafetyError("UNKNOWN_BROKER_STATE") from exc
        if acknowledgement.ambiguous:
            self.journal.append("CANCEL_ACKNOWLEDGEMENT_UNCERTAIN", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.UNKNOWN_BROKER_STATE, broker_order_id=broker_order_id, links=links, payload={"broker_acknowledgement": jsonable(acknowledgement)})
            self.guard.disarm("UNKNOWN_BROKER_STATE")
            raise PaperSafetyError("UNKNOWN_BROKER_STATE")
        if acknowledgement.status.replace(" ", "").upper() in {"CANCELLED", "APICANCELLED"}:
            self.journal.append("CANCELLED", order_intent_id=order_intent_id, decision_id=decision_id, intent_state=IntentState.CANCELLED, broker_order_id=broker_order_id, links=links, payload={"broker_acknowledgement": jsonable(acknowledgement)})
        return acknowledgement
