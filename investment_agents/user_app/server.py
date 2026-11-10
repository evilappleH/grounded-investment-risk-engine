"""Loopback-only HTTP server for the local risk control console."""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .broker_readonly import connector_for, discover_accounts, runtime_readiness
from .models import AI_PROVIDERS, AIConfig, BrokerConfig, BrokerKind, RiskLimits
from .risk_analysis import (
    PortfolioInput,
    PositionInput,
    RiskAnalyzer,
    TradePointAnalyzer,
    TradePointInput,
)
from .session import SessionStore

STATIC_DIR = Path(__file__).with_name("static")
MAX_BODY_BYTES = 64 * 1024


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc


class RiskConsoleServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]):
        super().__init__(address, RiskConsoleHandler)
        self.sessions = SessionStore()
        self.analyzer = RiskAnalyzer()
        self.trade_point_analyzer = TradePointAnalyzer()


class RiskConsoleHandler(BaseHTTPRequestHandler):
    server: RiskConsoleServer
    server_version = "RiskConsole/1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Never log request bodies, query strings, session tokens, or secrets.
        safe_path = urlparse(self.path).path
        print(f"{self.client_address[0]} {self.command} {safe_path} {args[1] if len(args) > 1 else ''}")

    def _security_headers(self, *, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self._security_headers(content_type="application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str, *, code: str = "INVALID_REQUEST") -> None:
        self._json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body size is invalid")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # permits local CLI clients; browsers send Origin on fetch POST.
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def _session(self):
        token = self.headers.get("X-Session-Token", "")
        return self.server.sessions.get(token) if token else None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True, "service": "risk-console", "real_account_read": "ENABLED", "broker_writes": "ABSENT"})
            return
        if path == "/api/capabilities":
            self._json(HTTPStatus.OK, {
                "ok": True,
                "brokers": [
                    {"value": "manual", "label": "手动输入持仓", "environment": "NONE"},
                    {"value": "moomoo_real_readonly", "label": "Moomoo 实仓（只读）", "environment": "REAL_READ_ONLY"},
                    {"value": "moomoo_simulate_readonly", "label": "Moomoo 模拟账户（只读）", "environment": "SIMULATE_READ_ONLY"},
                    {"value": "ibkr_real_readonly", "label": "IBKR 实仓（只读）", "environment": "REAL_READ_ONLY"},
                    {"value": "ibkr_paper_readonly", "label": "IBKR Paper（只读）", "environment": "PAPER_READ_ONLY"},
                ],
                "ai_providers": [
                    {"value": key, **value} for key, value in AI_PROVIDERS.items()
                ],
                "security": {
                    "persistence": "MEMORY_ONLY", "bind": "LOOPBACK_ONLY",
                    "real_account_read": "ENABLED", "broker_writes": "ABSENT",
                },
            })
            return
        if path == "/api/runtime/readiness":
            self._json(HTTPStatus.OK, {"ok": True, "readiness": runtime_readiness()})
            return
        static_map = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/trade.css": ("trade.css", "text/css; charset=utf-8"),
            "/readiness.css": ("readiness.css", "text/css; charset=utf-8"),
        }
        item = static_map.get(path)
        if item is None:
            self._error(HTTPStatus.NOT_FOUND, "not found", code="NOT_FOUND")
            return
        file_name, content_type = item
        body = (STATIC_DIR / file_name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers(content_type=content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._same_origin():
            self._error(HTTPStatus.FORBIDDEN, "cross-origin request rejected", code="ORIGIN_REJECTED")
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/session/configure":
                self._configure(payload)
            elif path == "/api/broker/discover":
                self._discover_accounts(payload)
            elif path == "/api/session/clear":
                token = self.headers.get("X-Session-Token", "")
                self._json(HTTPStatus.OK, {"ok": True, "cleared": self.server.sessions.clear(token)})
            elif path == "/api/risk/analyze":
                self._analyze(payload)
            elif path == "/api/portfolio/sync":
                self._sync_portfolio(payload)
            elif path == "/api/trade-point/analyze":
                self._analyze_trade_point(payload)
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found", code="NOT_FOUND")
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error", code="INTERNAL_ERROR")

    def _configure(self, payload: dict[str, Any]) -> None:
        broker_raw = payload.get("broker") or {}
        ai_raw = payload.get("ai") or {}
        if not isinstance(broker_raw, dict) or not isinstance(ai_raw, dict):
            raise ValueError("broker and ai configuration must be objects")
        kind = BrokerKind(str(broker_raw.get("kind", "manual")))
        defaults = {
            BrokerKind.MOOMOO_REAL_READONLY: 11111,
            BrokerKind.MOOMOO_SIMULATE_READONLY: 11111,
            BrokerKind.IBKR_REAL_READONLY: 7496,
            BrokerKind.IBKR_PAPER_READONLY: 7497,
        }
        port_raw = broker_raw.get("port")
        broker = BrokerConfig(
            kind=kind,
            host=str(broker_raw.get("host") or "127.0.0.1"),
            port=int(port_raw) if port_raw not in (None, "") else defaults.get(kind),
            account_id=(str(broker_raw.get("account_id") or "").strip() or None),
            client_id=int(broker_raw.get("client_id") or 71),
        )
        ai = AIConfig(
            provider=str(ai_raw.get("provider") or "disabled"),
            model=str(ai_raw.get("model") or ""),
            base_url=(str(ai_raw.get("base_url") or "").strip() or None),
            api_key=(str(ai_raw.get("api_key") or "").strip() or None),
        )
        token, summary = self.server.sessions.create(broker, ai)
        self._json(HTTPStatus.CREATED, {"ok": True, "session_token": token, "session": summary})

    @staticmethod
    def _broker_failure_message(exc: Exception) -> str:
        error_code = str(exc).upper()
        if isinstance(exc, PermissionError):
            return "网关未返回匹配的只读账户，请检查账户环境和账户 ID。"
        if isinstance(exc, (ConnectionRefusedError, TimeoutError, OSError)) or any(
            marker in error_code for marker in (
                "ACCOUNT_DISCOVERY_FAILED", "SOCKET CONNECTION FAILED", "CALLBACK TIMEOUT",
                "NOT CONNECTED", "CONNECTION FAILED",
            )
        ):
            return "无法连接本地券商网关；请确认 OpenD/TWS/IB Gateway 已启动且 API 端口正确。"
        if isinstance(exc, (ModuleNotFoundError, ImportError)):
            return "对应券商 SDK 未安装。"
        return f"券商只读操作失败（{type(exc).__name__}）。"

    def _discover_accounts(self, payload: dict[str, Any]) -> None:
        broker_raw = payload.get("broker") or {}
        if not isinstance(broker_raw, dict):
            raise ValueError("broker configuration must be an object")
        kind = BrokerKind(str(broker_raw.get("kind") or ""))
        defaults = {
            BrokerKind.MOOMOO_REAL_READONLY: 11111,
            BrokerKind.MOOMOO_SIMULATE_READONLY: 11111,
            BrokerKind.IBKR_REAL_READONLY: 7496,
            BrokerKind.IBKR_PAPER_READONLY: 7497,
        }
        try:
            accounts = discover_accounts(
                kind,
                host=str(broker_raw.get("host") or "127.0.0.1"),
                port=int(broker_raw.get("port") or defaults[kind]),
                client_id=int(broker_raw.get("client_id") or 71),
            )
        except Exception as exc:
            raise ValueError(self._broker_failure_message(exc)) from exc
        self._json(HTTPStatus.OK, {
            "ok": True, "accounts": accounts, "read_only": True, "broker_writes": "ABSENT",
        })

    @staticmethod
    def _limits_from(limits_raw: dict[str, Any]) -> RiskLimits:
        return RiskLimits(
            max_single_position=_number(limits_raw.get("max_single_position", 0.10), "max_single_position"),
            max_sector_exposure=_number(limits_raw.get("max_sector_exposure", 0.25), "max_sector_exposure"),
            max_gross_exposure=_number(limits_raw.get("max_gross_exposure", 0.95), "max_gross_exposure"),
            minimum_cash_buffer=_number(limits_raw.get("minimum_cash_buffer", 0.05), "minimum_cash_buffer"),
            max_positions=int(limits_raw.get("max_positions", 20)),
        )

    def _sync_portfolio(self, payload: dict[str, Any]) -> None:
        token = self.headers.get("X-Session-Token", "")
        session = self.server.sessions.get(token)
        if session is None:
            self._error(HTTPStatus.UNAUTHORIZED, "configure a local session first", code="SESSION_REQUIRED")
            return
        if session.broker.kind is BrokerKind.MANUAL:
            raise ValueError("manual mode cannot synchronize a broker portfolio")
        try:
            snapshot = connector_for(session.broker).snapshot()
        except Exception as exc:
            raise ValueError(self._broker_failure_message(exc)) from exc
        self.server.sessions.set_portfolio(token, snapshot.portfolio)
        limits_raw = payload.get("limits") or {}
        if not isinstance(limits_raw, dict):
            raise ValueError("limits must be an object")
        analysis = self.server.analyzer.analyze(snapshot.portfolio, self._limits_from(limits_raw))
        self._json(HTTPStatus.OK, {
            "ok": True, "snapshot": snapshot.public_payload(), "analysis": analysis,
        })

    def _analyze(self, payload: dict[str, Any]) -> None:
        if self._session() is None:
            self._error(HTTPStatus.UNAUTHORIZED, "configure a local session first", code="SESSION_REQUIRED")
            return
        portfolio_raw = payload.get("portfolio") or {}
        limits_raw = payload.get("limits") or {}
        if not isinstance(portfolio_raw, dict) or not isinstance(limits_raw, dict):
            raise ValueError("portfolio and limits must be objects")
        positions_raw = portfolio_raw.get("positions") or []
        if not isinstance(positions_raw, list):
            raise ValueError("positions must be an array")
        positions = tuple(PositionInput(
            symbol=str(row.get("symbol") or ""),
            quantity=_number(row.get("quantity"), "quantity"),
            price=_number(row.get("price"), "price"),
            sector=str(row.get("sector") or "UNKNOWN"),
        ) for row in positions_raw if isinstance(row, dict))
        as_of_raw = portfolio_raw.get("as_of")
        as_of = datetime.fromisoformat(str(as_of_raw).replace("Z", "+00:00")) if as_of_raw else datetime.now(timezone.utc)
        portfolio = PortfolioInput(_number(portfolio_raw.get("cash", 0), "cash"), positions, as_of)
        limits = self._limits_from(limits_raw)
        result = self.server.analyzer.analyze(portfolio, limits)
        self.server.sessions.set_portfolio(self.headers.get("X-Session-Token", ""), portfolio)
        self._json(HTTPStatus.OK, {"ok": True, "analysis": result})

    def _analyze_trade_point(self, payload: dict[str, Any]) -> None:
        session = self._session()
        if session is None:
            self._error(HTTPStatus.UNAUTHORIZED, "configure a local session first", code="SESSION_REQUIRED")
            return
        if session.portfolio is None:
            raise ValueError("synchronize or analyze a portfolio before reviewing a trade point")
        intent_raw = payload.get("intent") or {}
        limits_raw = payload.get("limits") or {}
        if not isinstance(intent_raw, dict) or not isinstance(limits_raw, dict):
            raise ValueError("intent and limits must be objects")
        intent = TradePointInput(
            side=str(intent_raw.get("side") or "").upper(),
            symbol=str(intent_raw.get("symbol") or ""),
            quantity=_number(intent_raw.get("quantity"), "quantity"),
            price=_number(intent_raw.get("price"), "price"),
            sector=str(intent_raw.get("sector") or "UNKNOWN"),
            planned_at=datetime.fromisoformat(str(intent_raw.get("planned_at") or "").replace("Z", "+00:00")),
            review_horizon_sessions=int(intent_raw.get("review_horizon_sessions", 20)),
        )
        limits = self._limits_from(limits_raw)
        result = self.server.trade_point_analyzer.analyze(session.portfolio, intent, limits)
        self._json(HTTPStatus.OK, {"ok": True, "review": result})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local risk control console")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    server = RiskConsoleServer((args.host, args.port))
    print(f"Risk console: http://{args.host}:{server.server_port}")
    print("Local memory only; real trading disabled. Press Ctrl+C to stop.")
    if args.open_browser:
        webbrowser.open(f"http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
