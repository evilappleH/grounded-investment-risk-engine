"""Validated configuration models for the local risk console."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from urllib.parse import urlparse

AI_PROVIDERS: dict[str, dict[str, object]] = {
    "disabled": {"label": "不使用 AI", "key_required": False},
    "openai": {"label": "OpenAI", "key_required": True},
    "anthropic": {"label": "Anthropic", "key_required": True},
    "google": {"label": "Google Gemini", "key_required": True},
    "azure": {"label": "Azure OpenAI", "key_required": True},
    "openrouter": {"label": "OpenRouter", "key_required": True},
    "deepseek": {"label": "DeepSeek", "key_required": True},
    "qwen": {"label": "Qwen", "key_required": True},
    "ollama": {"label": "Ollama（本地）", "key_required": False},
    "openai_compatible": {"label": "OpenAI 兼容接口", "key_required": False},
}


class BrokerKind(StrEnum):
    MANUAL = "manual"
    MOOMOO_REAL_READONLY = "moomoo_real_readonly"
    MOOMOO_SIMULATE_READONLY = "moomoo_simulate_readonly"
    IBKR_REAL_READONLY = "ibkr_real_readonly"
    IBKR_PAPER_READONLY = "ibkr_paper_readonly"


@dataclass(frozen=True)
class AIConfig:
    provider: str = "disabled"
    model: str = ""
    base_url: str | None = None
    api_key: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        object.__setattr__(self, "provider", provider)
        if provider not in AI_PROVIDERS:
            raise ValueError("unsupported AI provider")
        if provider == "disabled":
            object.__setattr__(self, "model", "")
            object.__setattr__(self, "base_url", None)
            object.__setattr__(self, "api_key", None)
            return
        if provider != "disabled" and not self.model.strip():
            raise ValueError("AI model is required")
        if AI_PROVIDERS[provider]["key_required"] and not (self.api_key or "").strip():
            raise ValueError("selected AI provider requires an API key")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("AI base URL must be an absolute http(s) URL")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("unencrypted AI endpoints are allowed only on loopback")

    def public_summary(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
        }


@dataclass(frozen=True)
class BrokerConfig:
    kind: BrokerKind = BrokerKind.MANUAL
    host: str = "127.0.0.1"
    port: int | None = None
    account_id: str | None = None
    client_id: int = 71

    def __post_init__(self) -> None:
        kind = BrokerKind(self.kind)
        object.__setattr__(self, "kind", kind)
        if kind is BrokerKind.MANUAL:
            if self.account_id:
                raise ValueError("manual mode does not accept a broker account ID")
            return
        account_id = (self.account_id or "").strip()
        if not account_id or len(account_id) > 64:
            raise ValueError("paper/simulate account ID is required and must be at most 64 characters")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("broker host must be a loopback address")
        if kind in {BrokerKind.MOOMOO_REAL_READONLY, BrokerKind.MOOMOO_SIMULATE_READONLY}:
            port = self.port if self.port is not None else 11111
            if port != 11111:
                raise ValueError("Moomoo OpenD read-only connection requires port 11111")
            object.__setattr__(self, "port", port)
        elif kind in {BrokerKind.IBKR_REAL_READONLY, BrokerKind.IBKR_PAPER_READONLY}:
            allowed = {7496, 4001} if kind is BrokerKind.IBKR_REAL_READONLY else {7497, 4002}
            default = 7496 if kind is BrokerKind.IBKR_REAL_READONLY else 7497
            port = self.port if self.port is not None else default
            if port not in allowed:
                raise ValueError("IBKR port does not match the selected real/paper read-only environment")
            if not isinstance(self.client_id, int) or self.client_id < 0:
                raise ValueError("IBKR client_id must be a non-negative integer")
            object.__setattr__(self, "port", port)

    def public_summary(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "host": self.host if self.kind is not BrokerKind.MANUAL else None,
            "port": self.port,
            "account_id": self.account_id,
            "client_id": self.client_id if self.kind in {
                BrokerKind.IBKR_REAL_READONLY, BrokerKind.IBKR_PAPER_READONLY,
            } else None,
            "account_environment": {
                BrokerKind.MANUAL: "NONE",
                BrokerKind.MOOMOO_REAL_READONLY: "REAL_READ_ONLY",
                BrokerKind.MOOMOO_SIMULATE_READONLY: "SIMULATE_READ_ONLY",
                BrokerKind.IBKR_REAL_READONLY: "REAL_READ_ONLY",
                BrokerKind.IBKR_PAPER_READONLY: "PAPER_READ_ONLY",
            }[self.kind],
            "connection_status": "CONFIGURED_NOT_CONNECTED",
            "order_capability": "ABSENT",
        }


@dataclass(frozen=True)
class RiskLimits:
    max_single_position: float = 0.10
    max_sector_exposure: float = 0.25
    max_gross_exposure: float = 0.95
    minimum_cash_buffer: float = 0.05
    max_positions: int = 20

    def __post_init__(self) -> None:
        for name in (
            "max_single_position", "max_sector_exposure", "max_gross_exposure",
            "minimum_cash_buffer",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(float(value)) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be within [0, 1]")
        if not isinstance(self.max_positions, int) or self.max_positions <= 0:
            raise ValueError("max_positions must be a positive integer")
        if self.max_single_position > self.max_gross_exposure:
            raise ValueError("single-position limit cannot exceed gross-exposure limit")
