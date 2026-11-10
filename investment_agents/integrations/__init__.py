"""Local simulator plus an isolated, fail-closed IBKR Paper boundary."""

from .simulator import (
    DuplicateSubmissionError, LocalBrokerSimulator, SimulatedDisconnect,
    SimulatedOrder, SimulatedOrderRequest, SimulatorOrderStatus,
)
from .ibkr import IBKRPaperAdapter, BrokerSessionGuard, PaperSafetyError
from .moomoo import MoomooPaperAdapter, MoomooSessionGuard, MoomooSafetyError

__all__ = [
    "DuplicateSubmissionError", "LocalBrokerSimulator", "SimulatedDisconnect",
    "SimulatedOrder", "SimulatedOrderRequest", "SimulatorOrderStatus",
    "IBKRPaperAdapter", "BrokerSessionGuard", "PaperSafetyError",
    "MoomooPaperAdapter", "MoomooSessionGuard", "MoomooSafetyError",
]
