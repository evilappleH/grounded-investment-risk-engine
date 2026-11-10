"""Shared validation for immutable artifact identities and paths."""

from __future__ import annotations

from pathlib import Path
import re

from investment_agents.data.universe import normalize_us_ticker


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token)", re.I)


def validate_run_id(run_id: str) -> str:
    """Return a filesystem-safe run identifier or fail closed."""
    if not isinstance(run_id, str) or run_id in {".", ".."} or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a non-empty filesystem-safe identifier")
    return run_id


def validate_artifact_ticker(ticker: str) -> str:
    """Use the package's canonical ticker validation at persistence boundaries."""
    return normalize_us_ticker(ticker)


def safe_artifact_path(root: Path, *parts: str) -> Path:
    """Resolve a path and prove that it remains below ``root``."""
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*parts).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("artifact path escapes configured root")
    return target


def redact_secrets(value: object) -> object:
    """Recursively redact values whose field names identify credentials."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact_secrets(item) for item in value]
    return value


def sanitized_provider_error(exc: BaseException) -> dict[str, str]:
    """Return a bounded non-secret provider error record.

    Raw exception text is intentionally not retained because third-party
    clients sometimes embed request headers, URLs, or credentials in it.
    """
    error_type = type(exc).__name__
    code = re.sub(r"[^A-Z0-9_]", "_", error_type.upper())[:64] or "PROVIDER_ERROR"
    return {
        "code": f"PROVIDER_{code}",
        "message": f"Provider operation failed ({error_type[:64]})"[:160],
    }


def sanitized_failure_record(value: object) -> dict[str, str]:
    """Normalize an already-captured provider failure without retaining raw text."""
    raw_code = value.get("code") if isinstance(value, dict) else value
    code = re.sub(r"[^A-Z0-9_]", "_", str(raw_code).upper())[:96].strip("_")
    if not code:
        code = "PROVIDER_FAILURE"
    return {"code": code, "message": "Provider operation failed"}
