"""Deterministic historical valuation feasibility helpers (diagnostic only)."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except (TypeError, ValueError):
        return None


def select_historical_shares(
    facts: Mapping[str, Any], fiscal_period_end: date, *, window_days: int,
    on_or_before: bool = False, security_class_compatible: bool = True,
) -> dict[str, Any]:
    """Select a DEI shares observation nearest a fiscal end for diagnostics."""
    if not security_class_compatible:
        return {"status": "UNAVAILABLE", "reason": "HISTORICAL_SHARE_CLASS_BASIS_UNCERTAIN"}
    concept = ((facts.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    rows = ((concept or {}).get("units") or {}).get("shares", []) if isinstance(concept, Mapping) else []
    candidates=[]
    for row in rows:
        end, filed = _date(row.get("end")), _date(row.get("filed"))
        value=row.get("val")
        if not end or not filed or not isinstance(value,(int,float)) or value <= 0:
            continue
        delta=(fiscal_period_end-end).days
        if on_or_before and delta < 0:
            continue
        if abs(delta) <= window_days:
            candidates.append((abs(delta), -int(end.strftime("%Y%m%d")), -int(filed.strftime("%Y%m%d")), row, delta))
    if not candidates:
        return {"status":"UNAVAILABLE","reason":"HISTORICAL_SHARES_MISSING"}
    _,_,_,row,delta=min(candidates, key=lambda x:x[:3])
    return {"status":"AVAILABLE","shares":float(row["val"]),"observation_date":str(row["end"])[:10],"filed_date":str(row["filed"])[:10],"form":row.get("form"),"direction":"BEFORE" if delta>0 else "ON" if delta==0 else "AFTER","gap_days":abs(delta),"concept":"EntityCommonStockSharesOutstanding"}


def historical_pe_a(price: float | None, shares: float | None, net_income: float | None) -> float | None:
    if not all(isinstance(x,(int,float)) for x in (price,shares,net_income)) or price <= 0 or shares <= 0 or net_income <= 0:
        return None
    return float(price) * float(shares) / float(net_income)


def historical_pe_b(price: float | None, diluted_eps: float | None, *, basis_compatible: bool) -> float | None:
    if not basis_compatible or not all(isinstance(x,(int,float)) for x in (price,diluted_eps)) or price <= 0 or diluted_eps <= 0:
        return None
    return float(price) / float(diluted_eps)


def historical_pb(market_cap: float | None, equity: float | None) -> float | None:
    if not all(isinstance(x,(int,float)) for x in (market_cap,equity)) or market_cap <= 0 or equity <= 0:
        return None
    return float(market_cap) / float(equity)


def relative_difference(a: float | None, b: float | None) -> float | None:
    if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or a <= 0 or b <= 0:
        return None
    return abs(a-b)/((a+b)/2)


def history_bucket(count: int) -> str:
    if count >= 5: return "5_PLUS_VALID_YEARS"
    if count >= 3: return "3_TO_4_VALID_YEARS"
    if count >= 1: return "1_TO_2_VALID_YEARS"
    return "ZERO_VALID_YEARS"
