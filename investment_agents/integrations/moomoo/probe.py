"""Account-discovery-only local OpenD probe."""

from __future__ import annotations

from dataclasses import dataclass

from .official import OfficialMoomooSDKUnavailable, OfficialMoomooTransport


@dataclass(frozen=True)
class MoomooReadOnlyProbeResult:
    host: str
    port: int
    reachable: bool
    open_d_version: str | None = None
    compatible_us_simulate_accounts: tuple[dict[str, object], ...] = ()
    real_account_rows_observed: bool = False
    error_code: str | None = None


def run_account_discovery_probe(*, host: str = "127.0.0.1", port: int = 11111) -> MoomooReadOnlyProbeResult:
    """Call only get_acc_list; never unlock, place, modify, or cancel."""
    try:
        transport = OfficialMoomooTransport()
    except OfficialMoomooSDKUnavailable:
        return MoomooReadOnlyProbeResult(host, port, False, error_code="OFFICIAL_MOOMOO_SDK_UNAVAILABLE")
    except Exception as exc:
        code = f"PROBE_SDK_INITIALIZATION_{type(exc).__name__.upper()}"[:96]
        return MoomooReadOnlyProbeResult(host, port, False, error_code=code)
    try:
        info = transport.account_discovery_probe(host, port)
        accounts = info.accounts
        compatible = tuple({
            "acc_id": row.acc_id, "trd_env": row.trd_env, "acc_type": row.acc_type,
            "sim_acc_type": row.sim_acc_type, "trdmarket_auth": row.trdmarket_auth,
        } for row in accounts if row.is_compatible_us_simulation)
        real_observed = any(row.trd_env == "REAL" for row in accounts)
        return MoomooReadOnlyProbeResult(
            host,
            port,
            True,
            info.open_d_version,
            compatible,
            real_observed,
            None,
        )
    except Exception as exc:
        return MoomooReadOnlyProbeResult(host, port, False, error_code=f"PROBE_{type(exc).__name__.upper()}"[:96])
