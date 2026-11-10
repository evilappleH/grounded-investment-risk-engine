"""Offline tests for the local read-only risk console."""

from __future__ import annotations

import json
import re
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from types import SimpleNamespace

from investment_agents.user_app.broker_readonly import (
    IBKRReadOnlyPortfolioConnector,
    MoomooReadOnlyPortfolioConnector,
    discover_accounts,
    runtime_readiness,
)
from investment_agents.user_app.models import AIConfig, BrokerConfig, BrokerKind, RiskLimits
from investment_agents.user_app.risk_analysis import (
    PortfolioInput,
    PositionInput,
    RiskAnalyzer,
    TradePointAnalyzer,
    TradePointInput,
)
from investment_agents.user_app.server import RiskConsoleServer
from investment_agents.user_app.session import SessionStore

NOW = datetime.now(timezone.utc)


class ConfigurationTests(unittest.TestCase):
    def test_real_account_configs_are_loopback_and_read_only(self) -> None:
        moomoo = BrokerConfig(BrokerKind.MOOMOO_REAL_READONLY, account_id="123")
        self.assertEqual(moomoo.public_summary()["account_environment"], "REAL_READ_ONLY")
        self.assertEqual(moomoo.public_summary()["order_capability"], "ABSENT")
        ibkr = BrokerConfig(BrokerKind.IBKR_REAL_READONLY, port=7496, account_id="U123")
        self.assertEqual(ibkr.port, 7496)
        with self.assertRaisesRegex(ValueError, "does not match"):
            BrokerConfig(BrokerKind.IBKR_REAL_READONLY, port=7497, account_id="U123")
        with self.assertRaisesRegex(ValueError, "loopback"):
            BrokerConfig(BrokerKind.IBKR_REAL_READONLY, host="broker.example", port=7496, account_id="U123")

    def test_api_key_is_never_in_public_session_summary(self) -> None:
        store = SessionStore()
        token, summary = store.create(
            BrokerConfig(), AIConfig("openai", "gpt-test", api_key="TOP-SECRET"),
        )
        self.assertTrue(token)
        encoded = json.dumps(summary)
        self.assertNotIn("TOP-SECRET", encoded)
        self.assertTrue(summary["ai"]["api_key_configured"])
        self.assertTrue(store.clear(token))


class RiskAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolio = PortfolioInput(
            20_000,
            (PositionInput("AAPL", 100, 200, "TECHNOLOGY"), PositionInput("KO", 100, 60, "CONSUMER")),
            NOW,
        )

    def test_concentration_is_blocked_deterministically(self) -> None:
        result = RiskAnalyzer().analyze(self.portfolio, RiskLimits(max_single_position=0.30), now=NOW)
        self.assertEqual(result["overall_status"], "BLOCKED")
        self.assertIn("MAX_SINGLE_POSITION_EXCEEDED", {row["code"] for row in result["findings"]})
        self.assertEqual(result["execution_authorization"], "DENIED")

    def test_user_buy_point_reports_delta_but_creates_no_order(self) -> None:
        result = TradePointAnalyzer().analyze(
            self.portfolio, TradePointInput(
                "BUY", "MSFT", 10, 100, "TECHNOLOGY", NOW + timedelta(minutes=10), 20,
            ),
            RiskLimits(max_single_position=0.80, max_sector_exposure=0.90),
            now=NOW,
        )
        self.assertFalse(result["order_created"])
        self.assertFalse(result["broker_write"])
        self.assertEqual(result["user_decision"], "REQUIRED")
        self.assertIsNotNone(result["projected_risk"])

    def test_sell_cannot_exceed_live_long_position(self) -> None:
        result = TradePointAnalyzer().analyze(
            self.portfolio, TradePointInput(
                "SELL", "KO", 101, 60, "CONSUMER", NOW + timedelta(minutes=10), 20,
            ), RiskLimits(), now=NOW,
        )
        self.assertEqual(result["review_status"], "BLOCKED")
        self.assertIn("SELL_QUANTITY_EXCEEDS_CURRENT_LONG_POSITION", result["blockers"])
        self.assertIsNone(result["projected_risk"])

    def test_future_trade_point_requires_fresh_portfolio_at_planned_time(self) -> None:
        result = TradePointAnalyzer().analyze(
            self.portfolio,
            TradePointInput("BUY", "MSFT", 1, 100, "TECHNOLOGY", NOW + timedelta(days=1), 60),
            RiskLimits(max_single_position=0.80, max_sector_exposure=0.90), now=NOW,
        )
        self.assertTrue(result["time_review"]["refresh_required"])
        self.assertIn("PORTFOLIO_REFRESH_REQUIRED_AT_PLANNED_TIME", result["time_review"]["warnings"])
        self.assertIn(result["review_status"], {"RECHECK_REQUIRED", "BLOCKED"})


class FakeMoomooSDK:
    RET_OK = 0
    TrdMarket = SimpleNamespace(US="US")
    TrdEnv = SimpleNamespace(REAL="REAL", SIMULATE="SIMULATE")
    Currency = SimpleNamespace(USD="USD")

    class OpenSecTradeContext:
        last = None
        def __init__(self, **kwargs):
            self.closed = False
            self.write_calls = 0
            FakeMoomooSDK.OpenSecTradeContext.last = self
        def get_acc_list(self): return 0, [{"acc_id":"123", "trd_env":"REAL"}]
        def accinfo_query(self, **kwargs): return 0, [{"us_cash": 1000}]
        def position_list_query(self, **kwargs): return 0, [{"code":"US.AAPL", "qty":2, "market_val":400}]
        def close(self): self.closed = True


class ReadOnlyConnectorTests(unittest.TestCase):
    def test_runtime_readiness_never_claims_broker_write_capability(self) -> None:
        readiness = runtime_readiness()
        self.assertIn("moomoo", readiness["modules"])
        self.assertIn("11111", readiness["loopback_ports"])
        self.assertEqual(readiness["broker_writes"], "ABSENT")

    def test_moomoo_account_discovery_filters_requested_environment_and_closes(self) -> None:
        accounts = discover_accounts(
            BrokerKind.MOOMOO_REAL_READONLY, host="127.0.0.1", port=11111,
            moomoo_sdk=FakeMoomooSDK,
        )
        self.assertEqual(accounts[0]["account_id"], "123")
        self.assertEqual(accounts[0]["environment"], "REAL_READ_ONLY")
        self.assertTrue(FakeMoomooSDK.OpenSecTradeContext.last.closed)

    def test_moomoo_real_snapshot_reads_and_disconnects_without_write_surface(self) -> None:
        connector = MoomooReadOnlyPortfolioConnector(
            BrokerConfig(BrokerKind.MOOMOO_REAL_READONLY, account_id="123"), sdk=FakeMoomooSDK,
        )
        snapshot = connector.snapshot()
        self.assertEqual(snapshot.account_environment, "REAL_READ_ONLY")
        self.assertEqual(snapshot.portfolio.cash, 1000)
        self.assertEqual(snapshot.portfolio.positions[0].price, 200)
        self.assertTrue(FakeMoomooSDK.OpenSecTradeContext.last.closed)
        self.assertFalse(hasattr(connector, "place_order"))
        self.assertFalse(hasattr(connector, "cancel_order"))

    def test_ibkr_real_snapshot_connects_read_only_and_filters_account(self) -> None:
        state = {"disconnected": False, "read_only": None}
        contract = SimpleNamespace(con_id=7)
        class Transport:
            def __init__(self, **kwargs): pass
            def connect(self, host, port, client_id, *, read_only):
                state["read_only"] = read_only
                return SimpleNamespace(account_ids=("U123",))
            def account_summary(self, account): return SimpleNamespace(total_cash=5000, currency="USD")
            def positions(self): return (SimpleNamespace(account_id="U123", currency="USD", security_type="STK", quantity=2, symbol="MSFT", con_id=7),)
            def contract_details(self, **kwargs): return (contract,)
            def quote_snapshot(self, value): return SimpleNamespace(last=300, bid=299, ask=301, status="AVAILABLE")
            def disconnect(self): state["disconnected"] = True
        connector = IBKRReadOnlyPortfolioConnector(
            BrokerConfig(BrokerKind.IBKR_REAL_READONLY, port=7496, account_id="U123"),
            transport_factory=Transport,
        )
        snapshot = connector.snapshot()
        self.assertTrue(state["read_only"])
        self.assertTrue(state["disconnected"])
        self.assertEqual(snapshot.portfolio.positions[0].price, 300)
        self.assertFalse(hasattr(connector, "place_order"))

    def test_ibkr_account_discovery_uses_read_only_handshake(self) -> None:
        state = {"read_only": None, "disconnected": False}
        class Transport:
            def __init__(self, **kwargs): pass
            def connect(self, host, port, client_id, *, read_only):
                state["read_only"] = read_only
                return SimpleNamespace(account_ids=("U1", "U2"))
            def disconnect(self): state["disconnected"] = True
        accounts = discover_accounts(
            BrokerKind.IBKR_REAL_READONLY, host="127.0.0.1", port=7496,
            ibkr_transport_factory=Transport,
        )
        self.assertEqual([row["account_id"] for row in accounts], ["U1", "U2"])
        self.assertTrue(state["read_only"])
        self.assertTrue(state["disconnected"])


class HttpConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RiskConsoleServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _post(self, path, payload, token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Session-Token"] = token
        if origin:
            headers["Origin"] = origin
        self.connection.request("POST", path, json.dumps(payload), headers)
        response = self.connection.getresponse()
        return response.status, json.loads(response.read())

    def test_manual_session_and_risk_api_never_return_secret(self) -> None:
        status, configured = self._post("/api/session/configure", {
            "broker":{"kind":"manual"},
            "ai":{"provider":"openai","model":"fixture","api_key":"SECRET"},
        })
        self.assertEqual(status, 201)
        self.assertNotIn("SECRET", json.dumps(configured))
        token = configured["session_token"]
        status, result = self._post("/api/risk/analyze", {
            "portfolio":{"cash":1000,"as_of":NOW.isoformat(),"positions":[]}, "limits":{},
        }, token)
        self.assertEqual(status, 200)
        self.assertEqual(result["analysis"]["execution_authorization"], "NOT_REQUESTED")

        status, review = self._post("/api/trade-point/analyze", {
            "intent":{"side":"BUY","symbol":"AAPL","quantity":1,"price":100,"sector":"TECHNOLOGY","planned_at":(NOW + timedelta(minutes=5)).isoformat(),"review_horizon_sessions":20},
            "limits":{"max_single_position":0.8,"max_sector_exposure":0.9},
        }, token)
        self.assertEqual(status, 200)
        self.assertFalse(review["review"]["order_created"])
        self.assertFalse(review["review"]["broker_write"])
        self.assertEqual(review["review"]["user_decision"], "REQUIRED")

    def test_cross_origin_post_is_rejected(self) -> None:
        status, body = self._post("/api/session/configure", {}, origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "ORIGIN_REJECTED")

    def test_capabilities_advertise_real_read_only_not_real_writes(self) -> None:
        self.connection.request("GET", "/api/capabilities")
        response = self.connection.getresponse()
        body = json.loads(response.read())
        environments = {row["environment"] for row in body["brokers"]}
        self.assertIn("REAL_READ_ONLY", environments)
        self.assertEqual(body["security"]["broker_writes"], "ABSENT")

        self.connection.request("GET", "/api/runtime/readiness")
        response = self.connection.getresponse()
        readiness = json.loads(response.read())["readiness"]
        self.assertEqual(readiness["broker_writes"], "ABSENT")

    def test_console_exposes_persistent_chinese_english_language_switch(self) -> None:
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        html = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn('id="language-select"', html)
        self.assertIn('<html lang="en">', html)
        self.assertIn('<option value="en" selected>English</option>', html)
        self.assertIn('<option value="zh-CN">中文</option>', html)
        self.assertIn('data-i18n="trade.title"', html)
        self.assertIn('href="./styles.css"', html)
        self.assertIn('src="./app.js"', html)

        self.connection.request("GET", "/styles.css")
        response = self.connection.getresponse()
        stylesheet = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get_content_type(), "text/css")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertIn("--green-deep", stylesheet)

        self.connection.request("GET", "/app.js")
        response = self.connection.getresponse()
        javascript = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn('localStorage.getItem("sentinel-language")', javascript)
        self.assertIn('localStorage.getItem("sentinel-language") || "en"', javascript)
        self.assertNotIn("navigator.language", javascript)
        self.assertIn('"trade.user_decides": "User decision required"', javascript)
        self.assertIn('window.location.protocol !== "file:"', javascript)
        zh_section, english_tail = javascript.split('\n  },\n  en: {', 1)
        english_section = english_tail.split('\n  }\n};', 1)[0]
        key_pattern = re.compile(r'"([^"]+)":')
        zh_keys = set(key_pattern.findall(zh_section))
        zh_keys.discard("zh-CN")
        english_keys = set(key_pattern.findall(english_section))
        html_keys = set(re.findall(r'data-i18n(?:-placeholder|-aria-label)?="([^"]+)"', html))
        self.assertTrue(html_keys <= zh_keys)
        self.assertTrue(html_keys <= english_keys)
        self.assertEqual(zh_keys, english_keys)


if __name__ == "__main__":
    unittest.main()
