"""Official-TWS-API-shaped, Paper-only integration boundary."""

from .adapter import IBKRPaperAdapter, PaperSafetyError
from .audit import PaperExecutionJournal
from .backend import IBKRTransport
from .fake import InMemoryIBKRTransport
from .models import (
    EXECUTION_POLICY_CLASSIFICATION, AccountSummary, BrokerAcknowledgement,
    BrokerConnectionInfo, BrokerExecution, BrokerOrder, BrokerPosition,
    ExecutionSafetyPolicy, IBKRConnectionConfig, IBKRQuote, IntentState,
    OrderSide, PaperKillSwitchState, PaperOrderIntent,
    ReconciliationSnapshot, ResolvedContract, SessionState,
)
from .probe import ReadOnlyProbeResult, run_local_read_only_probe
from .session import BrokerSessionGuard, LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN

__all__ = [
    "EXECUTION_POLICY_CLASSIFICATION", "AccountSummary", "BrokerAcknowledgement",
    "BrokerConnectionInfo", "BrokerExecution", "BrokerOrder", "BrokerPosition",
    "BrokerSessionGuard", "ExecutionSafetyPolicy", "IBKRConnectionConfig",
    "IBKRPaperAdapter", "IBKRQuote", "IBKRTransport", "InMemoryIBKRTransport",
    "IntentState", "LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN", "OrderSide", "PaperExecutionJournal",
    "PaperKillSwitchState", "PaperOrderIntent", "PaperSafetyError",
    "ReadOnlyProbeResult", "ReconciliationSnapshot", "ResolvedContract",
    "SessionState", "run_local_read_only_probe",
]
