"""Fail-closed Paper account/session identity guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    BrokerConnectionInfo, IBKRConnectionConfig, PaperKillSwitchState, SessionState,
)


LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN = "LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN"


@dataclass
class BrokerSessionGuard:
    config: IBKRConnectionConfig
    host: str | None = None
    port: int | None = None
    client_id: int | None = None
    connected_at: datetime | None = None
    broker_server_version: int | None = None
    broker_reported_account_ids: tuple[str, ...] = ()
    selected_account: str | None = None
    paper_verification_result: str = "NOT_VERIFIED"
    current_session_state: SessionState = SessionState.READ_ONLY_PROBE
    last_successful_heartbeat: datetime | None = None
    last_successful_reconciliation: datetime | None = None
    kill_switch_state: PaperKillSwitchState = PaperKillSwitchState.BLOCKED
    armed_decision_id: str | None = None
    disarm_reason: str | None = None

    def connected(self, info: BrokerConnectionInfo) -> None:
        if info.host != self.config.host or info.port != self.config.port or info.client_id != self.config.client_id:
            self.disarm("CONNECTION_IDENTITY_MISMATCH")
            raise PermissionError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
        self.host = info.host
        self.port = info.port
        self.client_id = info.client_id
        self.connected_at = info.connected_at
        self.broker_server_version = info.server_version
        self.broker_reported_account_ids = tuple(sorted(set(info.account_ids)))
        self.selected_account = None
        self.paper_verification_result = "NOT_VERIFIED"
        self.current_session_state = SessionState.READ_ONLY_PROBE
        self.armed_decision_id = None
        self.disarm_reason = None
        self.last_successful_heartbeat = datetime.now(timezone.utc)

    def observe_accounts(self, account_ids: tuple[str, ...]) -> None:
        observed = tuple(sorted(set(account_ids)))
        if self.broker_reported_account_ids and observed != self.broker_reported_account_ids:
            self.broker_reported_account_ids = observed
            self.disarm("SESSION_ACCOUNT_IDENTITY_CHANGED")
            return
        self.broker_reported_account_ids = observed

    def heartbeat_ok(self) -> None:
        self.last_successful_heartbeat = datetime.now(timezone.utc)

    def reconciliation_ok(self) -> None:
        self.last_successful_reconciliation = datetime.now(timezone.utc)

    def verify_paper_account(self) -> str:
        accounts = self.broker_reported_account_ids
        expected = self.config.expected_account_id
        valid = (
            self.host in self.config.allowed_hosts
            and self.port in self.config.allowed_paper_ports
            and len(accounts) == 1
            and expected is not None
            and accounts[0] == expected
            and accounts[0] in self.config.paper_account_allowlist
            and accounts[0] not in self.config.live_account_denylist
        )
        if not valid:
            self.disarm(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
            raise PermissionError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
        self.selected_account = accounts[0]
        self.paper_verification_result = "EXACT_BROKER_ACCOUNT_ALLOWLIST_MATCH"
        self.current_session_state = SessionState.PAPER_VERIFIED
        self.disarm_reason = None
        return accounts[0]

    def arm_for_paper(
        self,
        *,
        decision_id: str,
        risk_passed: bool,
        manifest_verified: bool,
        decision_unused: bool,
        reconciliation_complete: bool,
        kill_switch_state: PaperKillSwitchState,
    ) -> None:
        self.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if (
            self.current_session_state is not SessionState.PAPER_VERIFIED
            or self.selected_account is None
            or not risk_passed
            or not manifest_verified
            or not decision_unused
            or not reconciliation_complete
            or self.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER
        ):
            self.disarm("PAPER_ARMING_REQUIREMENT_FAILED")
            raise PermissionError("PAPER_EXECUTION_ARMING_REJECTED")
        self.current_session_state = SessionState.PAPER_EXECUTION_ARMED
        self.armed_decision_id = decision_id
        self.disarm_reason = None

    def assert_submission_safe(self, *, decision_id: str, account_ids: tuple[str, ...]) -> None:
        self.observe_accounts(account_ids)
        if (
            self.current_session_state is not SessionState.PAPER_EXECUTION_ARMED
            or self.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER
            or self.armed_decision_id != decision_id
            or self.selected_account is None
            or self.broker_reported_account_ids != (self.selected_account,)
            or self.selected_account not in self.config.paper_account_allowlist
            or self.selected_account in self.config.live_account_denylist
        ):
            self.disarm(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)
            raise PermissionError(LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN)

    def disarm(self, reason: str) -> None:
        self.current_session_state = SessionState.DISARMED
        self.paper_verification_result = "NOT_VERIFIED"
        self.kill_switch_state = PaperKillSwitchState.BLOCKED
        self.armed_decision_id = None
        self.disarm_reason = reason
