"""Small provider boundary; SDK calls never appear in persona modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping
import os


class LLMProviderNotConfigured(RuntimeError):
    """Raised only when an explicit execution has no provider credentials."""


class LLMProviderFailure(RuntimeError):
    """Sanitized provider failure; never carries prompts, keys, or headers."""
    def __init__(self, code: str, *, http_status: int | None = None, retryable: bool = False,
                 model: str | None = None, request_id: str | None = None, timeout: bool = False):
        super().__init__(code)
        self.code, self.http_status, self.retryable = code, http_status, retryable
        self.model, self.request_id, self.timeout = model, request_id, timeout


def classify_provider_exception(exc: Exception, *, model: str) -> LLMProviderFailure:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error = body.get("error", {}) if isinstance(body, Mapping) else {}
    code = str(error.get("code") or "").lower()
    typ = str(error.get("type") or "").lower()
    request_id = getattr(exc, "request_id", None)
    name = type(exc).__name__.lower()
    if status == 401 or "authentication" in name or code in {"invalid_api_key", "authentication_error"}:
        kind = "OPENAI_AUTH_ERROR"
    elif status == 404 or "model" in code and "not" in code:
        kind = "OPENAI_MODEL_NOT_FOUND"
    elif status == 403 or "permission" in typ or "access" in code:
        kind = "OPENAI_MODEL_ACCESS_DENIED"
    elif status == 400:
        kind = "OPENAI_SCHEMA_ERROR"
    elif status == 429:
        if "insufficient_quota" in code or "credit" in code:
            kind = "OPENAI_CREDIT_BALANCE_EXHAUSTED"
        elif "spend" in code:
            kind = "OPENAI_ORG_SPEND_LIMIT"
        else:
            kind = "OPENAI_RATE_LIMIT"
    elif isinstance(exc, PermissionError) or "10013" in str(exc):
        kind = "TCP_CONNECT_FAILURE"
    elif isinstance(exc, ConnectionResetError) or "reset" in name:
        kind = "CONNECTION_RESET"
    elif "ssl" in name or "certificate" in str(exc).lower():
        kind = "TLS_FAILURE"
    elif "timeout" in name:
        kind = "OPENAI_TIMEOUT"
    elif "connection" in name or "network" in name:
        kind = "OPENAI_NETWORK_ERROR"
    else:
        kind = "OPENAI_UNKNOWN_PROVIDER_ERROR"
    return LLMProviderFailure(kind, http_status=status if isinstance(status, int) else None,
        retryable=kind in {"OPENAI_RATE_LIMIT", "OPENAI_TIMEOUT", "OPENAI_NETWORK_ERROR"},
        model=model, request_id=str(request_id) if request_id else None,
        timeout=kind == "OPENAI_TIMEOUT")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: Mapping[str, Any] | None = None
    raw_metadata: Mapping[str, Any] | None = None


class LLMProvider(ABC):
    provider_name: str

    @abstractmethod
    def generate(self, *, system_prompt: str, user_prompt: str, model: str, temperature: float | None = None, max_output_tokens: int | None = None, response_format: Mapping[str, Any] | None = None) -> LLMResponse:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    """Lazy OpenAI adapter using only ``OPENAI_API_KEY`` or an injected key."""

    provider_name = "openai"

    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self._client = client

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key or self._client is not None)

    def generate(self, *, system_prompt: str, user_prompt: str, model: str, temperature: float | None = None, max_output_tokens: int | None = None, response_format: Mapping[str, Any] | None = None) -> LLMResponse:
        if not self._api_key and self._client is None:
            raise LLMProviderNotConfigured("OPENAI_API_KEY is not configured")
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMProviderNotConfigured("OpenAI SDK is not installed") from exc
            self._client = OpenAI(api_key=self._api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": response_format or {"type": "json_object"},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            kwargs["max_completion_tokens"] = max_output_tokens
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise classify_provider_exception(exc, model=model) from None
        choice = response.choices[0]
        usage_obj = getattr(response, "usage", None)
        usage = None
        if usage_obj is not None:
            usage = {
                "input_tokens": getattr(usage_obj, "prompt_tokens", None),
                "output_tokens": getattr(usage_obj, "completion_tokens", None),
                "total_tokens": getattr(usage_obj, "total_tokens", None),
            }
        return LLMResponse(
            text=str(choice.message.content or ""), provider=self.provider_name,
            model=model, usage=usage, raw_metadata={"finish_reason": getattr(choice, "finish_reason", None)},
        )
