"""Independent, strategy-free foundation for future investment-agent work.

This package has no market-data, LLM, TradingAgents, broker, or order-execution
integration.  Its only purpose in Step 2 is to establish module boundaries and
shared contracts.
"""

from .models import AgentSignal, AgentSignalStatus, Candidate

__all__ = ["AgentSignal", "AgentSignalStatus", "Candidate"]
