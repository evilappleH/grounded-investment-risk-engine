"""Permanent SIMULATE-only Moomoo OpenD account guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from investment_agents.integrations.ibkr.models import PaperKillSwitchState

from .models import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN, MoomooAccount, MoomooConnectionConfig,
    MoomooConnectionInfo, MoomooSessionState,
)


# Backward-compatible name for the single fail-closed account/environment code.
SIMULATE_ACCOUNT_UNAVAILABLE = MOOMOO_REAL_EXECUTION_FORBIDDEN
PRODUCTION_EXECUTION_PURPOSE = "PRODUCTION_PORTFOLIO_EXECUTION"
PAPER_BROKER_SMOKE_TEST = "PAPER_BROKER_SMOKE_TEST"


@dataclass
class MoomooSessionGuard:
    config: MoomooConnectionConfig
    host: str | None = None
    port: int | None = None
    connected_at: datetime | None = None
    open_d_version: str | None = None
    accounts: tuple[MoomooAccount, ...] = ()
    selected_acc_id: str | None = None
    selected_account: MoomooAccount | None = None
    verification_result: str = "NOT_VERIFIED"
    state: MoomooSessionState = MoomooSessionState.READ_ONLY_PROBE
    last_successful_heartbeat: datetime | None = None
    last_successful_reconciliation: datetime | None = None
    kill_switch_state: PaperKillSwitchState = PaperKillSwitchState.BLOCKED
    armed_decision_id: str | None = None
    armed_purpose: str | None = None
    disarm_reason: str | None = None

    def connected(self, info: MoomooConnectionInfo) -> None:
        if info.host != self.config.host or info.port != self.config.port:
            self.disarm("OPEND_ENDPOINT_IDENTITY_MISMATCH")
            raise PermissionError(SIMULATE_ACCOUNT_UNAVAILABLE)
        self.host = info.host
        self.port = info.port
        self.connected_at = info.connected_at
        self.open_d_version = info.open_d_version
        self.accounts = tuple(sorted(info.accounts, key=lambda row: row.acc_id))
        self.selected_acc_id = None
        self.selected_account = None
        self.verification_result = "NOT_VERIFIED"
        self.state = MoomooSessionState.READ_ONLY_PROBE
        self.kill_switch_state = PaperKillSwitchState.BLOCKED
        self.armed_decision_id = None
        self.armed_purpose = None
        self.disarm_reason = None
        self.last_successful_heartbeat = datetime.now(timezone.utc)

    def observe_accounts(self, accounts: tuple[MoomooAccount, ...]) -> None:
        observed = tuple(sorted(accounts, key=lambda row: row.acc_id))
        self.accounts = observed
        if self.selected_acc_id is None:
            return
        selected_rows = tuple(
            row for row in observed if row.acc_id == self.selected_acc_id
        )
        if (
            len(selected_rows) != 1
            or not selected_rows[0].is_compatible_us_simulation
            or self.config.expected_acc_id is None
            or selected_rows[0].acc_id != str(self.config.expected_acc_id)
        ):
            self.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            return
        self.selected_account = selected_rows[0]

    def reject_real_environment(self, trd_env: str | None) -> None:
        if trd_env is not None and str(trd_env).upper() != "SIMULATE":
            self.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise PermissionError(MOOMOO_REAL_EXECUTION_FORBIDDEN)

    def verify_simulate_account(self) -> MoomooAccount:
        expected = self.config.expected_acc_id
        selected_rows = tuple(
            account for account in self.accounts
            if expected is not None and account.acc_id == str(expected)
        )
        if (
            self.host not in self.config.allowed_hosts
            or self.port not in self.config.allowed_ports
            or expected is None
            or len(selected_rows) != 1
            or not selected_rows[0].is_compatible_us_simulation
            or selected_rows[0].acc_id not in set(map(str, self.config.simulate_account_allowlist))
        ):
            self.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise PermissionError(MOOMOO_REAL_EXECUTION_FORBIDDEN)
        self.selected_account = selected_rows[0]
        self.selected_acc_id = selected_rows[0].acc_id
        self.verification_result = "EXACT_OPEND_SIMULATE_ACCOUNT_ALLOWLIST_MATCH"
        self.state = MoomooSessionState.SIMULATE_VERIFIED
        self.disarm_reason = None
        return selected_rows[0]

    def heartbeat_ok(self) -> None:
        self.last_successful_heartbeat = datetime.now(timezone.utc)

    def reconciliation_ok(self) -> None:
        self.last_successful_reconciliation = datetime.now(timezone.utc)

    def arm(self, *, decision_id: str, risk_ok: bool, manifest_ok: bool, unused: bool, reconciled: bool, kill_switch_state: PaperKillSwitchState) -> None:
        self.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if (
            self.state is not MoomooSessionState.SIMULATE_VERIFIED
            or self.selected_account is None
            or self.selected_account.trd_env != "SIMULATE"
            or not risk_ok or not manifest_ok or not unused or not reconciled
            or self.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER
        ):
            self.disarm("MOOMOO_SIMULATE_ARMING_REJECTED")
            raise PermissionError("MOOMOO_SIMULATE_ARMING_REJECTED")
        self.state = MoomooSessionState.SIMULATE_EXECUTION_ARMED
        self.armed_decision_id = decision_id
        self.armed_purpose = PRODUCTION_EXECUTION_PURPOSE

    def arm_smoke(
        self,
        *,
        authorization_id: str,
        exact_account_ok: bool,
        safety_preflight_ok: bool,
        unused: bool,
        reconciled: bool,
        kill_switch_state: PaperKillSwitchState,
    ) -> None:
        """Arm only the isolated, hard-limited Paper broker smoke purpose."""
        self.kill_switch_state = PaperKillSwitchState(kill_switch_state)
        if (
            self.state is not MoomooSessionState.SIMULATE_VERIFIED
            or self.selected_account is None
            or self.config.expected_acc_id is None
            or self.selected_account.acc_id != str(self.config.expected_acc_id)
            or self.selected_account.trd_env != "SIMULATE"
            or not exact_account_ok
            or not safety_preflight_ok
            or not unused
            or not reconciled
            or self.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER
        ):
            self.disarm("MOOMOO_SMOKE_ARMING_REJECTED")
            raise PermissionError("MOOMOO_SMOKE_ARMING_REJECTED")
        self.state = MoomooSessionState.SIMULATE_EXECUTION_ARMED
        self.armed_decision_id = authorization_id
        self.armed_purpose = PAPER_BROKER_SMOKE_TEST

    def assert_submission_safe(
        self,
        *,
        decision_id: str,
        accounts: tuple[MoomooAccount, ...],
        required_purpose: str = PRODUCTION_EXECUTION_PURPOSE,
    ) -> None:
        self.observe_accounts(accounts)
        expected = self.config.expected_acc_id
        selected_rows = tuple(
            account for account in accounts
            if expected is not None and account.acc_id == str(expected)
        )
        if (
            self.state is not MoomooSessionState.SIMULATE_EXECUTION_ARMED
            or self.kill_switch_state is not PaperKillSwitchState.ARMED_FOR_PAPER
            or self.armed_decision_id != decision_id
            or self.armed_purpose != required_purpose
            or expected is None
            or len(selected_rows) != 1
            or not selected_rows[0].is_compatible_us_simulation
            or self.selected_account is None
            or self.selected_account.trd_env != "SIMULATE"
            or self.selected_acc_id != str(expected)
            or self.selected_acc_id not in set(map(str, self.config.simulate_account_allowlist))
        ):
            self.disarm(MOOMOO_REAL_EXECUTION_FORBIDDEN)
            raise PermissionError(MOOMOO_REAL_EXECUTION_FORBIDDEN)

    def disarm(self, reason: str) -> None:
        self.state = MoomooSessionState.DISARMED
        self.verification_result = "NOT_VERIFIED"
        self.kill_switch_state = PaperKillSwitchState.BLOCKED
        self.armed_decision_id = None
        self.armed_purpose = None
        self.disarm_reason = reason
