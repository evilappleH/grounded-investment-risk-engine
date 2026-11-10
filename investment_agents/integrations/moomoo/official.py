"""Lazy official Moomoo OpenAPI/OpenD Python transport."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

from .models import (
    MOOMOO_REAL_EXECUTION_FORBIDDEN, MOOMOO_SIMULATE_CAPABILITIES,
    MoomooAccount, MoomooAcknowledgement, MoomooBalance, MoomooCapabilityProfile,
    MoomooConnectionInfo, MoomooDeal, MoomooOrder,
    MoomooOrderIntent, MoomooOrderSide, MoomooPosition, MoomooQuote,
)

if TYPE_CHECKING:
    from .smoke import MoomooPaperSmokeIntent


class OfficialMoomooSDKUnavailable(RuntimeError):
    pass


class MoomooProviderError(RuntimeError):
    """Contains only a bounded operation code, never raw SDK response text."""


def _text(value: object) -> str:
    text = str(value)
    return text.rsplit(".", 1)[-1].upper()


def _float(value: object) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _bool(value: object) -> bool:
    return value is True or str(value).strip().upper() in {"TRUE", "1", "YES"}


def _records(data: object) -> list[dict[str, Any]]:
    if hasattr(data, "to_dict"):
        rows = data.to_dict("records")
        return [dict(row) for row in rows]
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [dict(data)]
    return []


def _market_auth(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(_text(item) for item in value)
    cleaned = str(value).strip().strip("[]")
    return tuple(_text(item.strip().strip("'\"")) for item in cleaned.split(",") if item.strip())


def _eastern_timestamp(value: object) -> datetime | None:
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NAN", "NONE"}:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


class OfficialMoomooTransport:
    """Thin synchronous facade over official ``moomoo`` SDK contexts."""

    capabilities = MOOMOO_SIMULATE_CAPABILITIES

    def __init__(self) -> None:
        try:
            sdk = self._import_sdk_with_writable_log()
        except ImportError as exc:
            raise OfficialMoomooSDKUnavailable("Official Moomoo OpenAPI Python SDK is not installed") from exc
        self.sdk = sdk
        self.trade_ctx: Any = None
        self.quote_ctx: Any = None
        self.info: MoomooConnectionInfo | None = None

    @staticmethod
    def _import_sdk_with_writable_log() -> Any:
        """Import the SDK without requiring a protected user AppData path.

        The official SDK creates its logger during import and hard-codes the
        Windows ``appdata`` environment variable.  Keep that SDK behavior
        intact while directing only this process-local initialization to the
        project cache.  The environment is restored immediately after import;
        no order or account behavior is changed.
        """
        import importlib

        log_root = Path(__file__).resolve().parents[2] / "data_cache" / "moomoo_sdk"
        log_root.mkdir(parents=True, exist_ok=True)
        old_appdata = os.environ.get("appdata")
        os.environ["appdata"] = str(log_root)
        try:
            return importlib.import_module("moomoo")
        finally:
            if old_appdata is None:
                os.environ.pop("appdata", None)
            else:
                os.environ["appdata"] = old_appdata

    def _call(self, operation: str, result: tuple[object, object]) -> list[dict[str, Any]]:
        ret, data = result
        if ret != self.sdk.RET_OK:
            raise MoomooProviderError(f"MOOMOO_{operation.upper()}_FAILED"[:96])
        return _records(data)

    @staticmethod
    def _require_simulate(trd_env: str) -> None:
        if trd_env != "SIMULATE":
            raise PermissionError(MOOMOO_REAL_EXECUTION_FORBIDDEN)

    def account_discovery_probe(self, host: str, port: int) -> MoomooConnectionInfo:
        """Open only a US trade context, call get_acc_list, and close it."""
        probe_ctx: Any = None
        try:
            probe_ctx = self.sdk.OpenSecTradeContext(
                filter_trdmarket=self.sdk.TrdMarket.US,
                host=host,
                port=port,
            )
            ret, data = probe_ctx.get_acc_list()
            rows = self._call("get_acc_list", (ret, data))
            accounts = tuple(MoomooAccount(
                str(row.get("acc_id", "")), _text(row.get("trd_env", "")),
                _text(row.get("acc_type", "")), _text(row.get("sim_acc_type", "")),
                _market_auth(row.get("trdmarket_auth", ())),
            ) for row in rows)
            return MoomooConnectionInfo(
                host,
                port,
                datetime.now(timezone.utc),
                None,
                accounts,
            )
        finally:
            if probe_ctx is not None:
                probe_ctx.close()

    def connect(self, host: str, port: int) -> MoomooConnectionInfo:
        try:
            self.trade_ctx = self.sdk.OpenSecTradeContext(
                filter_trdmarket=self.sdk.TrdMarket.US,
                host=host,
                port=port,
            )
            self.quote_ctx = self.sdk.OpenQuoteContext(host=host, port=port)
            accounts = self.discover_accounts()
            version = None
            try:
                rows = self._call("global_state", self.quote_ctx.get_global_state())
                if rows:
                    version = str(rows[0].get("server_ver") or rows[0].get("version") or "") or None
            except Exception:
                version = None
            self.info = MoomooConnectionInfo(host, port, datetime.now(timezone.utc), version, accounts)
            return self.info
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        if self.trade_ctx is not None:
            self.trade_ctx.close()
        if self.quote_ctx is not None:
            self.quote_ctx.close()
        self.trade_ctx = self.quote_ctx = None
        self.info = None

    def is_connected(self) -> bool:
        return self.trade_ctx is not None and self.quote_ctx is not None

    def heartbeat(self) -> bool:
        if not self.is_connected():
            return False
        try:
            self._call("heartbeat", self.trade_ctx.get_acc_list())
            return True
        except Exception:
            return False

    def discover_accounts(self) -> tuple[MoomooAccount, ...]:
        if self.trade_ctx is None:
            raise MoomooProviderError("MOOMOO_NOT_CONNECTED")
        rows = self._call("get_acc_list", self.trade_ctx.get_acc_list())
        return tuple(MoomooAccount(
            str(row.get("acc_id", "")), _text(row.get("trd_env", "")),
            _text(row.get("acc_type", "")), _text(row.get("sim_acc_type", "")),
            _market_auth(row.get("trdmarket_auth", ())),
        ) for row in rows)

    def account_balance(self, acc_id: str, *, trd_env: str) -> MoomooBalance:
        self._require_simulate(trd_env)
        rows = self._call("accinfo_query", self.trade_ctx.accinfo_query(
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id), refresh_cache=True,
            currency=self.sdk.Currency.USD,
        ))
        if len(rows) != 1:
            raise MoomooProviderError("MOOMOO_BALANCE_UNAVAILABLE")
        row = rows[0]
        returned_env = _text(row.get("trd_env", "SIMULATE"))
        # Moomoo documents ``available_funds`` for futures accounts and
        # ``us_avl_withdrawal_cash`` as withdrawable cash. Neither is the
        # available-cash boundary for a US cash-equity purchase. Prefer the
        # current per-currency cash field, subtract frozen cash, and cap it by
        # USD cash buying power when that field is supplied. Never use the
        # generic ``power`` field because it may include margin capacity.
        cash_field = "us_cash"
        cash = _float(row.get(cash_field))
        if cash is None:
            cash_field = "cash_legacy"
            cash = _float(row.get("cash"))
        frozen_cash = _float(row.get("frozen_cash"))
        if frozen_cash is None:
            frozen_cash = 0.0
        usd_cash_power = _float(row.get("usd_net_cash_power"))
        available_cash = None if cash is None else cash - frozen_cash
        availability_formula = f"{cash_field}-frozen_cash"
        if available_cash is not None and usd_cash_power is not None:
            available_cash = min(available_cash, usd_cash_power)
            availability_formula = f"min({availability_formula},usd_net_cash_power)"
        provenance = {
            "broker_reported_currency": _text(row.get("currency", "")) or None,
            "cash_field": cash_field if cash is not None else None,
            "available_cash_formula": availability_formula if cash is not None else None,
            "excluded_fields": ("available_funds", "us_avl_withdrawal_cash", "power"),
        }
        return MoomooBalance(acc_id, returned_env, "USD", cash, available_cash, provenance)

    def positions(self, acc_id: str, *, trd_env: str) -> tuple[MoomooPosition, ...]:
        self._require_simulate(trd_env)
        rows = self._call("position_list_query", self.trade_ctx.position_list_query(
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id), refresh_cache=True,
        ))
        return tuple(MoomooPosition(
            acc_id, _text(row.get("trd_env", "SIMULATE")), str(row.get("code", "")),
            _float(row.get("qty")) or 0.0, _float(row.get("can_sell_qty")) or 0.0,
            _float(row.get("market_val")),
        ) for row in rows)

    def _order(self, row: dict[str, Any], acc_id: str) -> MoomooOrder:
        return MoomooOrder(
            str(row.get("order_id", "")), str(row.get("acc_id", acc_id)),
            _text(row.get("trd_env", "SIMULATE")), str(row.get("code", "")),
            _text(row.get("trd_side", "")), _text(row.get("order_type", "")),
            _text(row.get("order_status", "")), _float(row.get("qty")) or 0.0,
            _float(row.get("price")) or 0.0, _float(row.get("dealt_qty")) or 0.0,
            _float(row.get("dealt_avg_price")), str(row.get("remark", "")) or None,
            _text(row.get("time_in_force", "")) or None, _eastern_timestamp(row.get("updated_time")),
        )

    def current_orders(self, acc_id: str, *, trd_env: str) -> tuple[MoomooOrder, ...]:
        self._require_simulate(trd_env)
        rows = self._call("order_list_query", self.trade_ctx.order_list_query(
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id), refresh_cache=True,
        ))
        return tuple(self._order(row, acc_id) for row in rows)

    def historical_orders(self, acc_id: str, *, trd_env: str) -> tuple[MoomooOrder, ...]:
        self._require_simulate(trd_env)
        rows = self._call("history_order_list_query", self.trade_ctx.history_order_list_query(
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id),
        ))
        return tuple(self._order(row, acc_id) for row in rows)

    def deals(self, acc_id: str, *, trd_env: str) -> tuple[MoomooDeal, ...]:
        self._require_simulate(trd_env)
        # SIMULATE officially has no deal-list capability. Keep this method a
        # harmless empty result so callers cannot accidentally dispatch the
        # unsupported SDK endpoint.
        if trd_env == "SIMULATE":
            return ()
        rows = self._call("deal_list_query", self.trade_ctx.deal_list_query(
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id), refresh_cache=True,
        ))
        return tuple(MoomooDeal(
            str(row.get("deal_id", "")), str(row.get("order_id", "")),
            str(row.get("acc_id", acc_id)), _text(row.get("trd_env", "SIMULATE")),
            str(row.get("code", "")), _text(row.get("trd_side", "")),
            _float(row.get("qty")) or 0.0, _float(row.get("price")) or 0.0,
            _eastern_timestamp(row.get("create_time")), _float(row.get("fee_amount")),
        ) for row in rows)

    def quote_snapshot(self, code: str) -> MoomooQuote:
        rows = self._call("get_market_snapshot", self.quote_ctx.get_market_snapshot([code]))
        if len(rows) != 1 or str(rows[0].get("code", "")) != code:
            return MoomooQuote(code, None, None, None, None, False, "UNAVAILABLE")
        row = rows[0]
        derivative = _bool(row.get("future_valid")) or _bool(row.get("wrt_valid")) or _text(row.get("option_type", "N/A")) not in {"N/A", "NONE", ""}
        eligible = code.startswith("US.") and not derivative and (_bool(row.get("equity_valid")) or _bool(row.get("trust_valid")))
        quoted_at = _eastern_timestamp(row.get("update_time"))
        bid, ask, last = _float(row.get("bid_price")), _float(row.get("ask_price")), _float(row.get("last_price"))
        status = "AVAILABLE" if quoted_at is not None and (bid or ask or last) else "UNAVAILABLE"
        return MoomooQuote(code, bid, ask, last, quoted_at, eligible, status)

    def place_limit_order(self, intent: MoomooOrderIntent | MoomooPaperSmokeIntent, acc_id: str, *, trd_env: str) -> MoomooAcknowledgement:
        self._require_simulate(trd_env)
        side = self.sdk.TrdSide.BUY if intent.side is MoomooOrderSide.BUY else self.sdk.TrdSide.SELL
        rows = self._call("place_order", self.trade_ctx.place_order(
            price=intent.limit_price, qty=intent.quantity, code=intent.code,
            trd_side=side, order_type=self.sdk.OrderType.NORMAL,
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id),
            remark=intent.order_intent_id, time_in_force=self.sdk.TimeInForce.DAY,
            fill_outside_rth=False, session=self.sdk.Session.NONE,
        ))
        if len(rows) != 1:
            return MoomooAcknowledgement(False, None, None, "UNKNOWN", datetime.now(timezone.utc), True)
        row = rows[0]
        env = _text(row["trd_env"]) if "trd_env" in row else None
        status = _text(row.get("order_status", "SUBMITTING"))
        accepted = status not in {"FAILED", "DISABLED", "DELETED"}
        return MoomooAcknowledgement(accepted, str(row.get("order_id", "")) or None, env, status, datetime.now(timezone.utc), False, None if accepted else "MOOMOO_ORDER_REJECTED")

    def cancel_order(self, order_id: str, acc_id: str, *, trd_env: str) -> MoomooAcknowledgement:
        self._require_simulate(trd_env)
        rows = self._call("modify_order_cancel", self.trade_ctx.modify_order(
            self.sdk.ModifyOrderOp.CANCEL, order_id, 0, 0,
            trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id),
        ))
        if len(rows) != 1:
            return MoomooAcknowledgement(False, order_id, None, "UNKNOWN", datetime.now(timezone.utc), True)
        row = rows[0]
        env = _text(row["trd_env"]) if "trd_env" in row else None
        return MoomooAcknowledgement(True, str(row.get("order_id", order_id)), env, "CANCEL_REQUESTED", datetime.now(timezone.utc))

    def order_status(self, order_id: str, acc_id: str, *, trd_env: str) -> MoomooOrder | None:
        self._require_simulate(trd_env)
        rows = self._call("order_status", self.trade_ctx.order_list_query(
            order_id=order_id, trd_env=self.sdk.TrdEnv.SIMULATE,
            acc_id=int(acc_id), refresh_cache=True,
        ))
        if not rows:
            rows = self._call("history_order_status", self.trade_ctx.history_order_list_query(
                order_id=order_id, trd_env=self.sdk.TrdEnv.SIMULATE, acc_id=int(acc_id),
            ))
        return self._order(rows[0], acc_id) if len(rows) == 1 else None
