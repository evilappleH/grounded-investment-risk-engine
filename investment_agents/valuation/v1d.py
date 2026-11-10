"""CURRENT_ANALYSIS V1D data-plumbing audit.

This module is intentionally limited to SEC identity/share selection and
read-only daily-price diagnostics.  It never calls an order API and does not
make an investment decision.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from investment_agents.data.sec_provider import SECFirstFundamentalsProvider, SECProviderConfig
from investment_agents.data.universe import normalize_us_ticker
from investment_agents.valuation.current import (
    build_current_valuation_snapshot, resolve_sec_cik, select_sec_shares,
)


def load_sec_identity_map(cache_dir: Path, *, user_agent: str) -> Mapping[str, str]:
    """Load the existing official SEC map; never create a second identity map."""
    return SECFirstFundamentalsProvider(
        config=SECProviderConfig(user_agent=user_agent, cache_dir=cache_dir)
    ).cik_map


def load_sec_shares(cache_dir: Path, ticker: str, cik: str | None, *, as_of: date) -> dict[str, Any]:
    """Select current shares from cached official companyfacts only."""
    if not cik:
        return {"status": "UNAVAILABLE", "reason": "CIK_MISSING"}
    path = cache_dir / f"CIK{str(cik).zfill(10)}.json"
    if not path.exists():
        return {"status": "UNAVAILABLE", "reason": "CACHE_LOOKUP_FAILURE", "cik": cik}
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "UNAVAILABLE", "reason": "CACHE_LOOKUP_FAILURE", "cik": cik}
    # Multi-class identities remain distinct.  The SEC issuer-level fact is
    # not silently applied to BRK.B/BF.B without a class-specific basis.
    class_identity = "ISSUER_LEVEL_AMBIGUOUS" if ticker.upper() in {"BRK.B", "BF.B", "BRK-B", "BF-B"} else None
    result = select_sec_shares(
        facts, as_of=as_of, security_identity=ticker, share_class_identity=class_identity
    )
    result["cik"] = str(cik).zfill(10)
    return result


def latest_normalized_annual(normalized: Mapping[str, Any]) -> dict[str, Any] | None:
    history = normalized.get("current_history") or {}
    annual = history.get("annual", []) if isinstance(history, Mapping) else []
    rows = [r for r in annual if isinstance(r, Mapping) and r.get("report_period")]
    if not rows:
        return None
    return max(rows, key=lambda r: str(r.get("report_period")))


def read_sec_raw(cache_dir: Path, cik: str | None) -> Mapping[str, Any] | None:
    if not cik:
        return None
    path = cache_dir / f"CIK{str(cik).zfill(10)}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def capex_concept_inventory(facts_by_ticker: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Inventory, but do not select or combine, possible CapEx concepts."""
    out: list[dict[str, Any]] = []
    counts: dict[str, set[str]] = {}
    for ticker, payload in facts_by_ticker.items():
        for taxonomy, concepts in (payload.get("facts", {}) or {}).items():
            for concept in concepts:
                text = concept.lower()
                if any(term in text for term in ("propertyplant", "capitalexpenditure", "productiveasset")):
                    counts.setdefault(f"{taxonomy}:{concept}", set()).add(ticker)
    for concept, tickers in sorted(counts.items()):
        out.append({"concept": concept, "ticker_count": len(tickers), "tickers": sorted(tickers)})
    return out


def snapshot_from_normalized(
    ticker: str, normalized: Mapping[str, Any], *, market: Mapping[str, Any] | None,
    as_of: datetime,
) -> Any:
    row = latest_normalized_annual(normalized) or {}
    fundamentals = {
        "net_income_ttm": row.get("net_income"),
        "net_income_ttm_metadata": row.get("field_metadata", {}).get("net_income", {}),
        "operating_cash_flow_ttm": row.get("operating_cash_flow"),
        "operating_cash_flow_ttm_metadata": row.get("field_metadata", {}).get("operating_cash_flow", {}),
        "capital_expenditures_ttm": None,
        "ebitda_ttm": None,
        "cash": row.get("cash"), "cash_metadata": row.get("field_metadata", {}).get("cash", {}),
        "total_debt": row.get("total_debt"), "total_debt_metadata": row.get("field_metadata", {}).get("total_debt", {}),
        "equity": row.get("equity"), "equity_metadata": row.get("field_metadata", {}).get("equity", {}),
        "roe_percent": row.get("roe_percent"),
    }
    return build_current_valuation_snapshot(ticker, as_of=as_of, market=market, fundamentals=fundamentals)


def run_sec_input_audit(
    *, run_path: Path, sec_cache: Path, valuation_cache: Path,
    user_agent: str, as_of: date,
) -> dict[str, Any]:
    """Run the population SEC identity/share/fundamentals pass."""
    payload = json.loads((run_path / "scan_result.json").read_text(encoding="utf-8"))
    records = payload["payload"]["records"]
    records = [r for r in records if r.get("ticker") and r.get("scan_status") == "ANALYZED"]
    cmap = load_sec_identity_map(sec_cache, user_agent=user_agent)
    out: dict[str, Any] = {"cik": {}, "shares": {}, "snapshots": {}, "raw": {}}
    as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    for record in records:
        ticker = str(record["ticker"])
        identity = resolve_sec_cik(ticker, cmap)
        cik = identity.get("cik")
        shares = load_sec_shares(sec_cache, ticker, cik, as_of=as_of)
        normalized_path = Path("data_cache/fundamentals_cache/CURRENT_ANALYSIS_SEC") / f"{normalize_us_ticker(ticker)}.json"
        normalized = {}
        if normalized_path.exists():
            try:
                normalized = json.loads(normalized_path.read_text(encoding="utf-8")).get("normalized", {})
            except (OSError, ValueError):
                normalized = {}
        market = {
            "security_identity": ticker,
            "share_class_identity": ticker,
            "shares_outstanding": shares.get("shares_outstanding"),
            "shares_basis": shares.get("concept") if shares.get("status") == "AVAILABLE" else None,
            "shares_as_of": shares.get("shares_end_date"),
            "price": None,
        }
        snapshot = snapshot_from_normalized(ticker, normalized, market=market, as_of=as_of_dt)
        out["cik"][ticker] = identity
        out["shares"][ticker] = shares
        out["snapshots"][ticker] = snapshot.to_dict()
        raw = read_sec_raw(sec_cache, cik)
        if raw is not None:
            out["raw"][ticker] = raw
        # Snapshot persistence is delegated to the existing atomic helper.
        from investment_agents.valuation.current import persist_current_snapshot
        persist_current_snapshot(snapshot, valuation_cache)
    out["identity_map_count"] = len(cmap)
    out["record_count"] = len(records)
    out["capex_inventory"] = capex_concept_inventory(out["raw"])
    return out
