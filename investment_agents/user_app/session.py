"""In-memory session settings; secrets are never serialized."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

from .models import AIConfig, BrokerConfig
from .risk_analysis import PortfolioInput


@dataclass
class UserSession:
    broker: BrokerConfig
    ai: AIConfig
    created_at: datetime
    portfolio: PortfolioInput | None = None

    def public_summary(self) -> dict[str, object]:
        return {
            "broker": self.broker.public_summary(),
            "ai": self.ai.public_summary(),
            "created_at": self.created_at.isoformat(),
            "persistence": "MEMORY_ONLY",
            "real_account_read": "ENABLED_WHEN_CONFIGURED",
            "broker_writes": "ABSENT",
        }


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, UserSession] = {}
        self._lock = RLock()

    def create(self, broker: BrokerConfig, ai: AIConfig) -> tuple[str, dict[str, object]]:
        token = secrets.token_urlsafe(32)
        session = UserSession(broker, ai, datetime.now(timezone.utc))
        with self._lock:
            self._sessions[token] = session
        return token, session.public_summary()

    def get(self, token: str) -> UserSession | None:
        with self._lock:
            return self._sessions.get(token)

    def set_portfolio(self, token: str, portfolio: PortfolioInput) -> bool:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return False
            session.portfolio = portfolio
            return True

    def clear(self, token: str) -> bool:
        with self._lock:
            session = self._sessions.pop(token, None)
        if session is not None:
            session.ai = AIConfig()
            return True
        return False
