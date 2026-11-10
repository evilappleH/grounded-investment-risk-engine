"""Read-only real/paper portfolio connectors.

These classes intentionally expose no order, modify, cancel, or authorization
method. Connections are short-lived and disconnected after each snapshot.
"""

from __future__ import annotations

import importlib.util
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import BrokerConfig, BrokerKind
from .risk_analysis import PortfolioInput, PositionInput


@dataclass(frozen=True)
class BrokerPortfolioSnapshot:
    portfolio: PortfolioInput
    source: str
    account_environment: str
    account_id: str
    warnings: tuple[str, ...] = ()

    def public_payload(self) -> dict[str, object]:
        return {
            "portfolio": {
                "cash": self.portfolio.cash,
                "as_of": self.portfolio.as_of.isoformat(),
                "positions": [
                    {
                        "symbol": position.symbol,
                        "quantity": position.quantity,
                        "price": position.price,
                        "sector": position.sector,
                        "currency": position.currency,
                    }
                    for position in self.portfolio.positions
                ],
            },
            "source": self.source,
            "account_environment": self.account_environment,
            "account_id": self.account_id,
            "warnings": self.warnings,
            "read_only": True,
            "order_capability": "ABSENT",
        }


def _records(data: Any) -> list[dict[str, Any]]:
    if hasattr(data, "to_dict"):
        rows = data.to_dict("records")
        return [dict(row) for row in rows]
    if isinstance(data, list):
        return [dict(row) for row in data]
    raise RuntimeError("broker returned an unsupported table shape")


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


class MoomooReadOnlyPortfolioConnector:
    def __init__(self, config: BrokerConfig, *, sdk: Any | None = None) -> None:
        if config.kind not in {BrokerKind.MOOMOO_REAL_READONLY, BrokerKind.MOOMOO_SIMULATE_READONLY}:
            raise ValueError("Moomoo read-only connector received the wrong broker kind")
        self.config = config
        self._sdk = sdk

    def _load_sdk(self):
        if self._sdk is not None:
            return self._sdk
        from investment_agents.integrations.moomoo.official import OfficialMoomooTransport
        return OfficialMoomooTransport._import_sdk_with_writable_log()

    def snapshot(self) -> BrokerPortfolioSnapshot:
        sdk = self._load_sdk()
        context = None
        environment_name = "REAL" if self.config.kind is BrokerKind.MOOMOO_REAL_READONLY else "SIMULATE"
        environment = sdk.TrdEnv.REAL if environment_name == "REAL" else sdk.TrdEnv.SIMULATE
        try:
            context = sdk.OpenSecTradeContext(
                filter_trdmarket=sdk.TrdMarket.US,
                host=self.config.host,
                port=self.config.port,
            )
            ret, data = context.get_acc_list()
            if ret != sdk.RET_OK:
                raise RuntimeError("MOOMOO_ACCOUNT_DISCOVERY_FAILED")
            matches = [row for row in _records(data)
                       if str(row.get("acc_id", "")) == self.config.account_id
                       and str(row.get("trd_env", "")).upper() == environment_name]
            if len(matches) != 1:
                raise PermissionError("configured Moomoo account/environment was not discovered")
            ret, balance_data = context.accinfo_query(
                trd_env=environment, acc_id=int(self.config.account_id),
                refresh_cache=True, currency=sdk.Currency.USD,
            )
            if ret != sdk.RET_OK:
                raise RuntimeError("MOOMOO_BALANCE_READ_FAILED")
            balance_rows = _records(balance_data)
            if len(balance_rows) != 1:
                raise RuntimeError("MOOMOO_BALANCE_UNAVAILABLE")
            balance = balance_rows[0]
            cash = _float(balance.get("us_cash"))
            if cash is None:
                cash = _float(balance.get("cash"))
            if cash is None or cash < 0:
                raise RuntimeError("MOOMOO_USD_CASH_UNAVAILABLE")
            ret, position_data = context.position_list_query(
                trd_env=environment, acc_id=int(self.config.account_id), refresh_cache=True,
            )
            if ret != sdk.RET_OK:
                raise RuntimeError("MOOMOO_POSITION_READ_FAILED")
            positions: list[PositionInput] = []
            for row in _records(position_data):
                code = str(row.get("code", ""))
                quantity = _float(row.get("qty"))
                market_value = _float(row.get("market_val"))
                if not code.startswith("US."):
                    continue
                if quantity is None or quantity <= 0:
                    raise RuntimeError("short/zero Moomoo positions are unsupported in V1")
                if market_value is None or market_value <= 0:
                    raise RuntimeError(f"Moomoo market value unavailable for {code}")
                positions.append(PositionInput(code.removeprefix("US."), quantity, market_value / quantity))
            return BrokerPortfolioSnapshot(
                PortfolioInput(cash, tuple(positions), datetime.now(timezone.utc)),
                "MOOMOO_OPEND", f"{environment_name}_READ_ONLY", self.config.account_id,
                ("SECTOR_CLASSIFICATION_UNAVAILABLE",),
            )
        finally:
            if context is not None:
                context.close()


class IBKRReadOnlyPortfolioConnector:
    def __init__(
        self,
        config: BrokerConfig,
        *,
        transport_factory: Callable[..., Any] | None = None,
    ) -> None:
        if config.kind not in {BrokerKind.IBKR_REAL_READONLY, BrokerKind.IBKR_PAPER_READONLY}:
            raise ValueError("IBKR read-only connector received the wrong broker kind")
        self.config = config
        self._transport_factory = transport_factory

    def _transport(self):
        if self._transport_factory is not None:
            return self._transport_factory(timeout_seconds=5.0)
        from investment_agents.integrations.ibkr.official import OfficialIBKRTransport
        return OfficialIBKRTransport(timeout_seconds=5.0)

    def snapshot(self) -> BrokerPortfolioSnapshot:
        transport = self._transport()
        try:
            info = transport.connect(
                self.config.host, self.config.port, self.config.client_id, read_only=True,
            )
            if self.config.account_id not in info.account_ids:
                raise PermissionError("configured IBKR account was not discovered")
            summary = transport.account_summary(self.config.account_id)
            cash = summary.total_cash
            if cash is None or cash < 0 or summary.currency != "USD":
                raise RuntimeError("IBKR USD cash is unavailable")
            positions: list[PositionInput] = []
            for row in transport.positions():
                if row.account_id != self.config.account_id:
                    continue
                if row.currency != "USD" or row.security_type not in {"STK", "ETF"}:
                    continue
                if row.quantity <= 0:
                    raise RuntimeError("short/zero IBKR positions are unsupported in V1")
                contracts = transport.contract_details(
                    symbol=row.symbol, security_type=row.security_type,
                    currency=row.currency, exchange="SMART",
                )
                match = next((contract for contract in contracts if contract.con_id == row.con_id), None)
                if match is None:
                    raise RuntimeError(f"IBKR contract resolution failed for {row.symbol}")
                quote = transport.quote_snapshot(match)
                price = quote.last
                if price is None and quote.bid is not None and quote.ask is not None:
                    price = (quote.bid + quote.ask) / 2.0
                if quote.status != "AVAILABLE" or price is None or price <= 0:
                    raise RuntimeError(f"IBKR quote unavailable for {row.symbol}")
                positions.append(PositionInput(row.symbol, row.quantity, price))
            environment = "REAL_READ_ONLY" if self.config.kind is BrokerKind.IBKR_REAL_READONLY else "PAPER_READ_ONLY"
            return BrokerPortfolioSnapshot(
                PortfolioInput(cash, tuple(positions), datetime.now(timezone.utc)),
                "IBKR_TWS_API", environment, self.config.account_id,
                ("SECTOR_CLASSIFICATION_UNAVAILABLE",),
            )
        finally:
            transport.disconnect()


def connector_for(config: BrokerConfig):
    if config.kind in {BrokerKind.MOOMOO_REAL_READONLY, BrokerKind.MOOMOO_SIMULATE_READONLY}:
        return MoomooReadOnlyPortfolioConnector(config)
    if config.kind in {BrokerKind.IBKR_REAL_READONLY, BrokerKind.IBKR_PAPER_READONLY}:
        return IBKRReadOnlyPortfolioConnector(config)
    raise ValueError("manual mode has no broker connector")


def runtime_readiness() -> dict[str, object]:
    """Report local SDK and gateway-port readiness without authenticating."""
    modules = {
        "moomoo": importlib.util.find_spec("moomoo") is not None,
        "ibapi": importlib.util.find_spec("ibapi") is not None,
        "sklearn": importlib.util.find_spec("sklearn") is not None,
    }
    ports: dict[str, bool] = {}
    for port in (11111, 7496, 7497, 4001, 4002):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(0.2)
        try:
            ports[str(port)] = connection.connect_ex(("127.0.0.1", port)) == 0
        finally:
            connection.close()
    return {
        "modules": modules,
        "loopback_ports": ports,
        "broker_writes": "ABSENT",
    }


def discover_accounts(
    kind: BrokerKind,
    *,
    host: str,
    port: int,
    client_id: int = 71,
    moomoo_sdk: Any | None = None,
    ibkr_transport_factory: Callable[..., Any] | None = None,
) -> tuple[dict[str, str], ...]:
    """Discover account IDs through a short-lived read-only connection."""
    kind = BrokerKind(kind)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("broker discovery host must be loopback")
    if kind in {BrokerKind.MOOMOO_REAL_READONLY, BrokerKind.MOOMOO_SIMULATE_READONLY}:
        if port != 11111:
            raise ValueError("Moomoo discovery requires port 11111")
        sdk = moomoo_sdk
        if sdk is None:
            from investment_agents.integrations.moomoo.official import OfficialMoomooTransport
            sdk = OfficialMoomooTransport._import_sdk_with_writable_log()
        context = None
        wanted = "REAL" if kind is BrokerKind.MOOMOO_REAL_READONLY else "SIMULATE"
        try:
            context = sdk.OpenSecTradeContext(
                filter_trdmarket=sdk.TrdMarket.US, host=host, port=port,
            )
            ret, data = context.get_acc_list()
            if ret != sdk.RET_OK:
                raise RuntimeError("MOOMOO_ACCOUNT_DISCOVERY_FAILED")
            results = []
            for row in _records(data):
                environment = str(row.get("trd_env", "")).upper()
                account_id = str(row.get("acc_id", "")).strip()
                if environment == wanted and account_id:
                    results.append({
                        "account_id": account_id,
                        "environment": f"{environment}_READ_ONLY",
                        "label": f"{account_id} · {environment} · 只读",
                    })
            return tuple(results)
        finally:
            if context is not None:
                context.close()
    if kind in {BrokerKind.IBKR_REAL_READONLY, BrokerKind.IBKR_PAPER_READONLY}:
        allowed = {7496, 4001} if kind is BrokerKind.IBKR_REAL_READONLY else {7497, 4002}
        if port not in allowed:
            raise ValueError("IBKR discovery port does not match selected environment")
        if ibkr_transport_factory is None:
            from investment_agents.integrations.ibkr.official import OfficialIBKRTransport
            transport = OfficialIBKRTransport(timeout_seconds=5.0)
        else:
            transport = ibkr_transport_factory(timeout_seconds=5.0)
        try:
            info = transport.connect(host, port, client_id, read_only=True)
            environment = "REAL_READ_ONLY" if kind is BrokerKind.IBKR_REAL_READONLY else "PAPER_READ_ONLY"
            return tuple({
                "account_id": account_id,
                "environment": environment,
                "label": f"{account_id} · {environment.replace('_', ' ')}",
            } for account_id in info.account_ids)
        finally:
            transport.disconnect()
    raise ValueError("manual mode has no account discovery")
