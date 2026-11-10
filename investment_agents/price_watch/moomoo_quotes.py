"""Quote-only Moomoo OpenD client; this module has no trade context."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from investment_agents.integrations.moomoo.official import OfficialMoomooTransport

from .models import PriceQuote


def _number(value: object) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: object) -> datetime | None:
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


class MoomooQuoteProvider:
    """The only SDK object created here is OpenQuoteContext."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111) -> None:
        self.host = host
        self.port = port
        self.sdk = OfficialMoomooTransport._import_sdk_with_writable_log()
        self.context: Any = None

    def connect(self) -> None:
        self.context = self.sdk.OpenQuoteContext(host=self.host, port=self.port)

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        self.context = None

    def snapshots(self, tickers: tuple[str, ...]) -> dict[str, PriceQuote]:
        if self.context is None:
            raise RuntimeError("MOOMOO_QUOTE_PROVIDER_NOT_CONNECTED")
        if not tickers:
            return {}
        codes = [ticker if ticker.startswith("US.") else f"US.{ticker}" for ticker in tickers]
        ret, data = self.context.get_market_snapshot(codes)
        if ret != self.sdk.RET_OK:
            return {ticker: PriceQuote(ticker, None, None, "MOOMOO_OPEND", "PROVIDER_ERROR") for ticker in tickers}
        rows = data.to_dict("records") if hasattr(data, "to_dict") else []
        by_code = {str(row.get("code")): row for row in rows}
        output: dict[str, PriceQuote] = {}
        for ticker, code in zip(tickers, codes, strict=True):
            row = by_code.get(code, {})
            last = _number(row.get("last_price"))
            quoted_at = _timestamp(row.get("update_time"))
            output[ticker] = PriceQuote(ticker, last, quoted_at, "MOOMOO_OPEND", "AVAILABLE" if last and quoted_at else "UNAVAILABLE")
        return output

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()
