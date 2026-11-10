"""Permanent SIMULATE-only Moomoo OpenD integration."""

from investment_agents.integrations.ibkr.models import ExecutionSafetyPolicy, IntentState, PaperKillSwitchState

from .adapter import MoomooPaperAdapter, MoomooSafetyError
from .backend import MoomooTransport
from .fake import InMemoryMoomooTransport
from .models import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN, MOOMOO_SIMULATE_CAPABILITIES,
    MoomooCapabilityProfile, MoomooAccount, MoomooAcknowledgement,
    MoomooBalance, MoomooConnectionConfig, MoomooConnectionInfo, MoomooDeal,
    MoomooOrder, MoomooOrderIntent, MoomooOrderSide, MoomooPosition, MoomooQuote,
    MoomooReconciliation, MoomooSecurityKind, MoomooSessionState,
)
from .probe import MoomooReadOnlyProbeResult, run_account_discovery_probe
from .pre_auto_test import PreAutoPaperTestDecision, evaluate_pre_auto_paper_test
from .session import MoomooSessionGuard, SIMULATE_ACCOUNT_UNAVAILABLE
from .smoke import (
    PAPER_BROKER_SMOKE_TEST, MoomooPaperSmokeHarness, MoomooPaperSmokeIntent,
    MoomooSmokePreflight, MoomooSmokeResult, non_marketable_smoke_limit,
)

__all__ = [
    "MOOMOO_REAL_EXECUTION_FORBIDDEN", "MOOMOO_SIMULATE_CAPABILITIES",
    "MoomooCapabilityProfile", "ExecutionSafetyPolicy", "IntentState",
    "PaperKillSwitchState", "MoomooAccount", "MoomooAcknowledgement",
    "MoomooBalance", "MoomooConnectionConfig", "MoomooConnectionInfo",
    "MoomooDeal", "MoomooOrder", "MoomooOrderIntent", "MoomooOrderSide",
    "MoomooPaperAdapter", "MoomooPosition", "MoomooQuote",
    "MoomooReadOnlyProbeResult", "MoomooReconciliation", "MoomooSafetyError",
    "MoomooSecurityKind", "MoomooSessionGuard", "MoomooSessionState",
    "MoomooTransport", "InMemoryMoomooTransport", "SIMULATE_ACCOUNT_UNAVAILABLE",
    "run_account_discovery_probe",
    "PreAutoPaperTestDecision", "evaluate_pre_auto_paper_test",
    "PAPER_BROKER_SMOKE_TEST", "MoomooPaperSmokeHarness",
    "MoomooPaperSmokeIntent", "MoomooSmokePreflight", "MoomooSmokeResult",
    "non_marketable_smoke_limit",
]
