"""Deterministic CURRENT_ANALYSIS valuation data layer (V1A).

This module only compiles facts.  It does not rank securities or make an
investment decision.  Market-derived inputs must be supplied with an explicit
security/share basis; annual diluted-average shares are not silently used as
current shares outstanding.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .profit_ratio import ROEMode, calculate_pr_metrics


def resolve_sec_cik(ticker: str, cik_map: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one canonical security ticker against the existing SEC map.

    This helper deliberately does not create or infer identities.  Callers
    supply the official SEC ticker map loaded by ``SECFirstFundamentalsProvider``.
    """
    from investment_agents.data.universe import normalize_us_ticker
    requested = str(ticker).strip()
    normalized = normalize_us_ticker(requested)
    value = cik_map.get(normalized)
    if value is None and requested != normalized:
        value = cik_map.get(requested)
    # SEC's official ticker map commonly encodes share classes with a dash
    # while the project's canonical security identity preserves the dot.
    # This is a lookup alias only; it never merges the securities.
    if value is None and "." in normalized:
        value = cik_map.get(normalized.replace(".", "-"))
    if value is None:
        return {"ticker": requested, "normalized_ticker": normalized,
                "cik": None, "status": "UNAVAILABLE", "reason": "CIK_MISSING"}
    text = str(value).strip()
    if not text.isdigit():
        return {"ticker": requested, "normalized_ticker": normalized,
                "cik": None, "status": "UNAVAILABLE", "reason": "CIK_INVALID"}
    return {"ticker": requested, "normalized_ticker": normalized,
            "cik": text.zfill(10), "status": "AVAILABLE", "reason": None}


@dataclass(frozen=True)
class ValuationField:
    value: float | None
    status: str
    reason: str | None = None
    source: str | None = None
    period: str | None = None
    as_of: str | None = None
    provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CurrentValuationSnapshot:
    ticker: str
    as_of: str
    analysis_mode: str
    price: ValuationField
    shares_outstanding: ValuationField
    market_cap: ValuationField
    net_income_ttm: ValuationField
    operating_cash_flow_ttm: ValuationField
    capital_expenditures_ttm: ValuationField
    fcf_ttm: ValuationField
    ebitda_ttm: ValuationField
    cash: ValuationField
    total_debt: ValuationField
    equity: ValuationField
    enterprise_value: ValuationField
    pe_ttm: ValuationField
    fcf_yield: ValuationField
    ev_ebitda: ValuationField
    pb: ValuationField
    pr: ValuationField

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _field(value: float | None, *, status: str = "AVAILABLE", reason: str | None = None,
           source: str | None = None, period: str | None = None,
           as_of: str | None = None, provenance: Mapping[str, Any] | None = None) -> ValuationField:
    return ValuationField(value=value, status=status, reason=reason, source=source,
                          period=period, as_of=as_of, provenance=provenance)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _raw_field(fundamentals: Mapping[str, Any], name: str, as_of: str) -> ValuationField:
    value = _num(fundamentals.get(name))
    meta = fundamentals.get(f"{name}_metadata") or {}
    missing_reason = {
        "net_income_ttm": "TTM_NET_INCOME_UNAVAILABLE",
        "operating_cash_flow_ttm": "OCF_TTM_UNAVAILABLE",
        "capital_expenditures_ttm": "CAPEX_TTM_UNAVAILABLE",
        "ebitda_ttm": "EBITDA_UNAVAILABLE",
    }.get(name, f"{name.upper()}_MISSING")
    return _field(value, status="AVAILABLE" if value is not None else "UNAVAILABLE",
                  reason=None if value is not None else missing_reason,
                  source=meta.get("source") or fundamentals.get(f"{name}_source"),
                  period=meta.get("period") or fundamentals.get(f"{name}_period"),
                  as_of=meta.get("as_of") or as_of,
                  provenance=meta.get("provenance") or fundamentals.get(f"{name}_provenance"))


def latest_completed_daily_close(frame: Any, *, as_of: date | datetime) -> dict[str, Any]:
    """Extract the latest non-null completed close on or before ``as_of``.

    The function accepts a pandas-like frame but keeps pandas out of the data
    contract: callers provide rows with ``Date``/``date`` and ``Close`` keys.
    """
    cutoff = as_of.date() if isinstance(as_of, datetime) else as_of
    rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame or [])
    valid: list[tuple[date, float, Mapping[str, Any]]] = []
    for row in rows:
        raw_date = row.get("Date", row.get("date", row.get("session_date")))
        close = _num(row.get("Close", row.get("close")))
        if raw_date is None or close is None:
            continue
        try:
            parsed = raw_date.date() if hasattr(raw_date, "date") else date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            continue
        if parsed <= cutoff and close > 0:
            valid.append((parsed, close, row))
    if not valid:
        return {"status": "UNAVAILABLE", "reason": "PRICE_MISSING"}
    price_date, close, row = max(valid, key=lambda item: item[0])
    return {"status": "AVAILABLE", "price": close, "price_date": price_date.isoformat(),
            "currency": row.get("Currency", row.get("currency", "USD")),
            "reason": None}


_SHARE_FORM_PRIORITY = {"10-K/A": 5, "10-K": 4, "10-Q/A": 3, "10-Q": 2, "8-K": 1}


def select_sec_shares(facts: Mapping[str, Any], *, as_of: date | datetime,
                      security_identity: str, share_class_identity: str | None = None) -> dict[str, Any]:
    """Select EntityCommonStockSharesOutstanding without using weighted shares."""
    cutoff = as_of.date() if isinstance(as_of, datetime) else as_of
    concept = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
    if not isinstance(concept, Mapping):
        return {"status": "UNAVAILABLE", "reason": "SEC_CONCEPT_MISSING",
                "concept": "EntityCommonStockSharesOutstanding"}
    rows = concept.get("units", {}).get("shares", []) if isinstance(concept, Mapping) else []
    candidates: list[Mapping[str, Any]] = []
    for row in rows:
        try:
            filed = date.fromisoformat(str(row.get("filed"))[:10])
            end = date.fromisoformat(str(row.get("end"))[:10])
            value = _num(row.get("val"))
        except (TypeError, ValueError):
            continue
        if value is not None and value > 0 and filed <= cutoff and end <= cutoff:
            candidates.append({**row, "filed_date": filed.isoformat(), "end_date": end.isoformat(), "value": value})
    if not candidates:
        return {"status": "UNAVAILABLE", "reason": "NO_VALID_SHARE_FACT",
                "concept": "EntityCommonStockSharesOutstanding"}
    if share_class_identity and share_class_identity != security_identity:
        return {"status": "UNAVAILABLE", "reason": "SHARE_CLASS_BASIS_UNCERTAIN"}
    latest_end = max(str(r["end_date"]) for r in candidates)
    same_end = [r for r in candidates if r["end_date"] == latest_end]
    latest_filed = max(str(r["filed_date"]) for r in same_end)
    same_filing = [r for r in same_end if r["filed_date"] == latest_filed]
    values = {round(float(r["value"]), 8) for r in same_filing}
    if len(values) > 1:
        return {"status": "UNAVAILABLE", "reason": "SHARES_CONFLICT", "end_date": latest_end,
                "filed_date": latest_filed, "concept": "EntityCommonStockSharesOutstanding"}
    chosen = max(same_filing, key=lambda r: (_SHARE_FORM_PRIORITY.get(str(r.get("form")), 0), str(r.get("accn", ""))))
    return {"status": "AVAILABLE", "shares_outstanding": chosen["value"],
            "shares_end_date": chosen["end_date"], "filed_date": chosen["filed_date"],
            "form": chosen.get("form"), "concept": "EntityCommonStockSharesOutstanding",
            "source": "SEC:dei:EntityCommonStockSharesOutstanding", "reason": None}


def _row_date(row: Mapping[str, Any], key: str) -> date | None:
    raw = row.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def compute_ttm_from_sec_rows(rows: list[Mapping[str, Any]], *, as_of: date | datetime) -> dict[str, Any]:
    """Compute FY or FY + current-YTD - prior-YTD for one normalized concept."""
    cutoff = as_of.date() if isinstance(as_of, datetime) else as_of
    usable = [r for r in rows if _num(r.get("val")) is not None and (_row_date(r, "filed") or cutoff) <= cutoff]
    annual = [r for r in usable if str(r.get("fp", "")).upper() == "FY" or str(r.get("form", "")).startswith("10-K")]
    annual = [r for r in annual if _row_date(r, "start") and _row_date(r, "end") and (_row_date(r, "end") - _row_date(r, "start")).days >= 300]
    ytd = [r for r in usable if _row_date(r, "start") and _row_date(r, "end") and 80 <= (_row_date(r, "end") - _row_date(r, "start")).days <= 300 and str(r.get("fp", "")).upper() != "FY"]
    if annual:
        latest_annual = max(annual, key=lambda r: (_row_date(r, "end") or date.min, _row_date(r, "filed") or date.min))
    else:
        latest_annual = None
    if ytd and latest_annual:
        current = max(ytd, key=lambda r: (_row_date(r, "end") or date.min, _row_date(r, "filed") or date.min))
        current_end = _row_date(current, "end")
        current_start = _row_date(current, "start")
        duration = (current_end - current_start).days if current_end and current_start else None
        prior = []
        for row in ytd:
            start, end = _row_date(row, "start"), _row_date(row, "end")
            if not start or not end or duration is None:
                continue
            if abs(abs((end - current_end).days) - 365) <= 31 and abs((end - start).days - duration) <= 31:
                prior.append(row)
        if prior:
            comparable = max(prior, key=lambda r: (_row_date(r, "filed") or date.min))
            value = float(latest_annual.get("val")) + float(current.get("val")) - float(comparable.get("val"))
            return {"status": "AVAILABLE", "value": value, "period_end": current_end.isoformat(),
                    "method": "FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD", "source_rows": [latest_annual, current, comparable]}
        if current_end and latest_annual.get("end") and current_end > (_row_date(latest_annual, "end") or date.min):
            return {"status": "UNAVAILABLE", "reason": "TTM_PERIOD_INCOMPATIBLE"}
    if latest_annual:
        return {"status": "AVAILABLE", "value": float(latest_annual.get("val")),
                "period_end": _row_date(latest_annual, "end").isoformat(), "method": "LATEST_VALID_FY",
                "source_rows": [latest_annual]}
    return {"status": "UNAVAILABLE", "reason": "TTM_NET_INCOME_UNAVAILABLE"}


def build_current_valuation_snapshot(
    ticker: str,
    *,
    as_of: datetime | str,
    market: Mapping[str, Any] | None,
    fundamentals: Mapping[str, Any] | None,
    analysis_mode: str = "CURRENT_ANALYSIS",
) -> CurrentValuationSnapshot:
    """Compile a current snapshot from explicit market and accounting facts.

    ``market`` is intentionally separate from SEC/accounting fundamentals.
    A non-current mode fails closed and returns unavailable fields.
    """
    now = as_of.isoformat() if isinstance(as_of, datetime) else str(as_of)
    f = fundamentals or {}
    m = market or {}
    unavailable = lambda reason: _field(None, status="UNAVAILABLE", reason=reason, as_of=now)
    if analysis_mode != "CURRENT_ANALYSIS":
        u = unavailable("BACKTEST_PIT_VALUATION_UNAVAILABLE")
        return CurrentValuationSnapshot(ticker=ticker, as_of=now, analysis_mode=analysis_mode,
            price=u, shares_outstanding=u, market_cap=u, net_income_ttm=u,
            operating_cash_flow_ttm=u, capital_expenditures_ttm=u, fcf_ttm=u,
            ebitda_ttm=u, cash=u, total_debt=u, equity=u, enterprise_value=u, pe_ttm=u,
            fcf_yield=u, ev_ebitda=u, pb=u, pr=u)

    identity = m.get("security_identity") or ticker
    price = _num(m.get("price"))
    shares = _num(m.get("shares_outstanding"))
    basis = m.get("shares_basis")
    market_prov = {"security_identity": identity, "price_basis": m.get("price_basis"), "shares_basis": basis}
    price_field = _field(price, status="AVAILABLE" if price is not None else "UNAVAILABLE",
        reason=None if price is not None else "PRICE_MISSING", source=m.get("price_source"), as_of=m.get("price_as_of") or now,
        period=m.get("price_date"), provenance=market_prov)
    shares_ok = shares is not None and bool(basis) and m.get("share_class_identity", identity) == identity
    shares_field = _field(shares if shares_ok else None, status="AVAILABLE" if shares_ok else "UNAVAILABLE",
        reason=None if shares_ok else ("SHARE_CLASS_BASIS_UNCERTAIN" if shares is not None else "SHARES_MISSING"),
        source=m.get("shares_source"), period=m.get("shares_period"), as_of=m.get("shares_as_of") or now,
        provenance=market_prov)
    mc = price * shares if price is not None and shares_ok and price > 0 and shares > 0 else None
    mc_reason = None if mc is not None else (
        "MARKET_CAP_UNAVAILABLE_SHARE_BASIS"
        if price is not None and shares is not None and not shares_ok
        else "MARKET_CAP_UNAVAILABLE"
    )
    market_cap = _field(mc, status="AVAILABLE" if mc is not None else "UNAVAILABLE", reason=mc_reason,
        source="calculated:price_times_shares" if mc is not None else None, as_of=now,
        provenance={**market_prov, "formula": "price * compatible_shares_outstanding"})

    ni = _raw_field(f, "net_income_ttm", now)
    ocf = _raw_field(f, "operating_cash_flow_ttm", now)
    capex = _raw_field(f, "capital_expenditures_ttm", now)
    ebitda = _raw_field(f, "ebitda_ttm", now)
    cash = _raw_field(f, "cash", now)
    debt = _raw_field(f, "total_debt", now)
    equity = _raw_field(f, "equity", now)
    fcf_value = None
    fcf_reason = None
    if ocf.value is not None and capex.value is not None:
        if f.get("accounting_basis") and f.get("capex_basis") and f.get("accounting_basis") != f.get("capex_basis"):
            fcf_reason = "FCF_BASIS_INCOMPATIBLE"
        elif f.get("capex_is_outflow") is False:
            fcf_reason = "FCF_BASIS_INCOMPATIBLE"
        else:
            fcf_value = ocf.value - abs(capex.value)
    else:
        fcf_reason = "FCF_INPUT_MISSING"
    fcf = _field(fcf_value, status="AVAILABLE" if fcf_value is not None else "UNAVAILABLE", reason=fcf_reason,
        source="calculated:operating_cash_flow_minus_capex" if fcf_value is not None else None,
        period=f.get("fcf_period") or ocf.period or capex.period, as_of=now,
        provenance={"operating_cash_flow": ocf.provenance, "capital_expenditures": capex.provenance,
                    "formula": "operating_cash_flow - abs(capital_expenditures)"})
    ev = None
    ev_reason = None
    if market_cap.value is not None and cash.value is not None and debt.value is not None:
        ev = market_cap.value + debt.value - cash.value
    else:
        ev_reason = "MARKET_CAP_UNAVAILABLE" if market_cap.value is None else ("DEBT_MISSING" if debt.value is None else "CASH_MISSING")
    enterprise = _field(ev, status="AVAILABLE" if ev is not None else "UNAVAILABLE", reason=ev_reason,
        source="calculated:market_cap_plus_debt_minus_cash" if ev is not None else None, as_of=now,
        provenance={"market_cap": market_cap.provenance, "debt": debt.provenance, "cash": cash.provenance,
                    "formula": "market_cap + total_debt - cash"})
    pe = (market_cap.value / ni.value) if market_cap.value is not None and ni.value is not None and ni.value > 0 else None
    pe_reason = None if pe is not None else ("NONPOSITIVE_NET_INCOME" if ni.value is not None and ni.value <= 0 else "MARKET_CAP_UNAVAILABLE")
    pe_field = _field(pe, status="AVAILABLE" if pe is not None else "UNAVAILABLE", reason=pe_reason, source="calculated:market_cap_over_ttm_net_income", as_of=now)
    fy = (fcf.value / market_cap.value) if fcf.value is not None and market_cap.value is not None and market_cap.value > 0 else None
    fy_field = _field(fy, status="AVAILABLE" if fy is not None else "UNAVAILABLE", reason=None if fy is not None else ("MARKET_CAP_UNAVAILABLE" if market_cap.value is None else fcf_reason), source="calculated:fcf_over_market_cap" if fy is not None else None, as_of=now)
    ev_ebitda = (enterprise.value / ebitda.value) if enterprise.value is not None and ebitda.value is not None and ebitda.value > 0 else None
    ev_reason2 = None if ev_ebitda is not None else ("EV_UNAVAILABLE" if enterprise.value is None else ("EV_EBITDA_NOT_MEANINGFUL" if ebitda.value is not None and ebitda.value <= 0 else "EBITDA_UNAVAILABLE"))
    ev_ebitda_field = _field(ev_ebitda, status="AVAILABLE" if ev_ebitda is not None else "UNAVAILABLE", reason=ev_reason2, source="calculated:ev_over_ebitda" if ev_ebitda is not None else None, as_of=now)
    pb = (market_cap.value / equity.value) if market_cap.value is not None and equity.value is not None and equity.value > 0 else None
    pb_reason = None if pb is not None else ("NONPOSITIVE_EQUITY" if equity.value is not None and equity.value <= 0 else "MARKET_CAP_UNAVAILABLE")
    pb_field = _field(pb, status="AVAILABLE" if pb is not None else "UNAVAILABLE", reason=pb_reason, source="calculated:market_cap_over_equity" if pb is not None else None, as_of=now)
    roe = _num(f.get("roe_percent"))
    pr_result = calculate_pr_metrics(ticker=ticker, pe=pe, roe_percent=roe, roe_mode=ROEMode.TTM,
        data_as_of=now, earnings_positive=(ni.value is not None and ni.value > 0), pe_basis="trailing", roe_basis=f.get("roe_basis"))
    pr = _field(pr_result.pr, status="AVAILABLE" if pr_result.pr is not None else "UNAVAILABLE", reason=None if pr_result.pr is not None else "PR_UNAVAILABLE", source="calculated:pe_over_roe" if pr_result.pr is not None else None, as_of=now)
    return CurrentValuationSnapshot(ticker=ticker, as_of=now, analysis_mode=analysis_mode,
        price=price_field, shares_outstanding=shares_field, market_cap=market_cap,
        net_income_ttm=ni, operating_cash_flow_ttm=ocf, capital_expenditures_ttm=capex,
        fcf_ttm=fcf, ebitda_ttm=ebitda, cash=cash, total_debt=debt,
        equity=equity, enterprise_value=enterprise, pe_ttm=pe_field, fcf_yield=fy_field,
        ev_ebitda=ev_ebitda_field, pb=pb_field, pr=pr)


def persist_current_snapshot(snapshot: CurrentValuationSnapshot, root: str | Path) -> Path:
    """Atomically persist a CURRENT_ANALYSIS snapshot and its content hash."""
    path = Path(root) / "CURRENT_ANALYSIS" / "valuation" / f"{snapshot.ticker}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = {"analysis_mode": "CURRENT_ANALYSIS", "snapshot": payload,
                "snapshot_hash": hashlib.sha256(raw).hexdigest(), "written_at": datetime.utcnow().isoformat() + "Z"}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(envelope, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


__all__ = ["ValuationField", "CurrentValuationSnapshot", "build_current_valuation_snapshot", "persist_current_snapshot"]
