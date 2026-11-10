from datetime import datetime, timezone
import json

from investment_agents.data.sec_provider import SECFirstFundamentalsProvider, SECProviderConfig
from investment_agents.data.models import FundamentalsAnalysisMode, FundamentalsStatus


class _Response:
    def __init__(self, payload, status_code=200): self._payload, self.status_code = payload, status_code
    def json(self): return self._payload
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(self.status_code)


def _facts():
    def rows(concept, vals):
        return {concept: {"units": {"USD": [
            {"val": v, "fy": y, "fp": "FY", "form": "10-K", "filed": f"{y+1}-02-01", "start": f"{y}-01-01", "end": f"{y}-12-31"}
            for y, v in vals]}}}
    facts = {}
    for concept, vals in {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [(2024, 100), (2023, 90)],
        "NetIncomeLoss": [(2024, 10), (2023, 9)], "Assets": [(2024, 200), (2023, 180)],
        "StockholdersEquity": [(2024, 80), (2023, 70)], "CashAndCashEquivalentsAtCarryingValue": [(2024, 20), (2023, 18)],
    }.items(): facts.update(rows(concept, vals))
    return {"entityName": "Example", "facts": {"us-gaap": facts}}


def test_sec_companyfacts_normalizes_annual_records_and_provenance(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if "company_tickers" in url: return _Response({"0": {"ticker": "ABC", "cik_str": 123}})
        return _Response(_facts())
    p = SECFirstFundamentalsProvider(config=SECProviderConfig("Audit contact@example.com", cache_dir=tmp_path, min_request_interval=0), get=get)
    result = p.get_fundamentals("ABC", datetime.now(timezone.utc), FundamentalsAnalysisMode.CURRENT_ANALYSIS)
    assert result.status is FundamentalsStatus.AVAILABLE
    assert result.current_history.annual[0].revenue == 100
    assert result.current_history.annual[0].field_sources["revenue"].startswith("SEC:")
    assert any("companyfacts" in c for c in calls)


def test_sec_provider_fails_closed_for_pit(tmp_path):
    p = SECFirstFundamentalsProvider(config=SECProviderConfig("Audit contact@example.com", cache_dir=tmp_path, min_request_interval=0), cik_map={"ABC": "1"}, get=lambda *a, **k: _Response(_facts()))
    result = p.get_fundamentals("ABC", datetime.now(timezone.utc), FundamentalsAnalysisMode.BACKTEST_PIT)
    assert result.status is FundamentalsStatus.PIT_DATA_UNAVAILABLE


def test_sec_cache_reused(tmp_path):
    count = {"n": 0}
    def get(url, **kwargs):
        count["n"] += 1
        return _Response(_facts())
    p = SECFirstFundamentalsProvider(config=SECProviderConfig("Audit contact@example.com", cache_dir=tmp_path, min_request_interval=0), cik_map={"ABC": "1"}, get=get)
    now = datetime.now(timezone.utc)
    assert p.get_fundamentals("ABC", now).status is FundamentalsStatus.AVAILABLE
    assert p.get_fundamentals("ABC", now).status is FundamentalsStatus.AVAILABLE
    assert count["n"] == 1


def test_sec_ticker_cik_map_is_persisted_and_reused(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return _Response({"0": {"ticker": "ABC", "cik_str": 123}})
    config = SECProviderConfig("Audit contact@example.com", cache_dir=tmp_path, min_request_interval=0)
    first = SECFirstFundamentalsProvider(config=config, get=get)
    assert first.cik_map["ABC"] == "0000000123"
    assert (tmp_path / "ticker_cik_map.json").exists()
    second = SECFirstFundamentalsProvider(config=config, get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert second.cik_map["ABC"] == "0000000123"
