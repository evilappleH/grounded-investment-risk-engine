"""Explicit localhost READ_ONLY_PROBE helper; it contains no order calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from investment_agents.path_safety import sanitized_provider_error

from .adapter import IBKRPaperAdapter
from .models import IBKRConnectionConfig
from .official import OfficialIBAPIUnavailable, OfficialIBKRTransport


@dataclass(frozen=True)
class ReadOnlyProbeResult:
    host: str
    port: int
    connected: bool
    server_version: int | None = None
    account_ids: tuple[str, ...] = ()
    account_summary_available: bool = False
    positions_available: bool = False
    open_orders_available: bool = False
    completed_orders_available: bool = False
    executions_available: bool = False
    contract_details_capability: str = "NOT_TESTED"
    market_data_capability: str = "NOT_TESTED"
    error_code: str | None = None


def run_local_read_only_probe(
    *,
    host: str,
    port: int,
    client_id: int,
    audit_path: Path,
    artifact_root: Path,
    test_symbol: str = "AAPL",
) -> ReadOnlyProbeResult:
    """Connect and inspect only; empty allowlist ensures execution cannot arm."""
    config = IBKRConnectionConfig(
        host=host, port=port, client_id=client_id,
        paper_account_allowlist=(), expected_account_id=None,
    )
    try:
        transport = OfficialIBKRTransport(timeout_seconds=config.request_timeout_seconds)
    except OfficialIBAPIUnavailable as exc:
        return ReadOnlyProbeResult(host, port, False, error_code=sanitized_provider_error(exc)["code"])
    adapter = IBKRPaperAdapter(
        config=config, transport=transport, audit_path=audit_path,
        artifact_root=artifact_root,
    )
    try:
        adapter.connect()
        accounts = tuple(adapter.guard.broker_reported_account_ids)
        summary_ok = positions_ok = open_ok = completed_ok = executions_ok = False
        contract_capability = market_capability = "NOT_TESTED"
        if len(accounts) == 1:
            try:
                adapter.account_summary(); summary_ok = True
            except Exception:
                pass
        try:
            adapter.positions(); positions_ok = True
        except Exception:
            pass
        try:
            adapter.open_orders(); open_ok = True
        except Exception:
            pass
        try:
            adapter.completed_orders(); completed_ok = True
        except Exception:
            pass
        try:
            adapter.executions(); executions_ok = True
        except Exception:
            pass
        try:
            contract = adapter.resolve_contract(test_symbol)
            contract_capability = "AVAILABLE"
            try:
                quote = adapter.quote_snapshot(contract)
                market_capability = quote.status
            except Exception:
                market_capability = "UNAVAILABLE"
        except Exception:
            contract_capability = "UNAVAILABLE"
        return ReadOnlyProbeResult(
            host, port, True, adapter.guard.broker_server_version, accounts,
            summary_ok, positions_ok, open_ok, completed_ok, executions_ok,
            contract_capability, market_capability,
        )
    except Exception as exc:
        return ReadOnlyProbeResult(host, port, False, error_code=sanitized_provider_error(exc)["code"])
    finally:
        if transport.is_connected():
            adapter.disconnect()

