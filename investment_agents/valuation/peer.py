"""Deterministic CURRENT_ANALYSIS peer valuation references.

Only cross-sectional descriptive statistics are produced here.  This module
does not classify a security as cheap/fair/expensive and is not consumed by
PortfolioPolicy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


METRICS = ("pe_ttm", "pb")


@dataclass(frozen=True)
class PeerReference:
    ticker: str
    metric: str
    company_value: float | None
    reference_value: float | None
    reference_level: str | None
    peer_count: int
    minimum_peer_threshold_used: int
    peer_tickers: tuple[str, ...]
    reason: str | None
    ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "metric": self.metric,
            "company_value": self.company_value,
            "reference_value": self.reference_value,
            "reference_level": self.reference_level,
            "peer_count": self.peer_count,
            "minimum_peer_threshold_used": self.minimum_peer_threshold_used,
            "peer_tickers": list(self.peer_tickers), "reason": self.reason,
            "ratio": self.ratio,
        }


def percentile(values: Sequence[float], p: float) -> float | None:
    """Deterministic linear percentile with no external numerical dependency."""
    xs = sorted(float(x) for x in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p
    lo, hi = int(rank), min(int(rank) + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def stats(values: Sequence[float]) -> dict[str, float | int | None]:
    xs = sorted(float(x) for x in values)
    return {
        "count": len(xs), "median": percentile(xs, .5),
        "p25": percentile(xs, .25), "p75": percentile(xs, .75),
        "minimum": min(xs) if xs else None, "maximum": max(xs) if xs else None,
    }


def eligible_metric(snapshot: Mapping[str, Any], metric: str, excluded: set[str]) -> bool:
    if snapshot.get("ticker") in excluded:
        return False
    field = snapshot.get(metric) or {}
    if field.get("status") != "AVAILABLE":
        return False
    value = field.get("value")
    if not isinstance(value, (int, float)) or value <= 0:
        return False
    if metric == "pe_ttm":
        ni = snapshot.get("net_income_ttm") or {}
        mc = snapshot.get("market_cap") or {}
        return ni.get("status") == "AVAILABLE" and isinstance(ni.get("value"), (int, float)) and ni["value"] > 0 and mc.get("status") == "AVAILABLE"
    if metric == "pb":
        eq = snapshot.get("equity") or {}
        mc = snapshot.get("market_cap") or {}
        return eq.get("status") == "AVAILABLE" and isinstance(eq.get("value"), (int, float)) and eq["value"] > 0 and mc.get("status") == "AVAILABLE"
    return False


def company_type(sector: str | None, industry: str | None, *, long_loss: bool = False, insufficient_history: bool = False, negative_equity: bool = False) -> str:
    """Mutually exclusive descriptive buckets derived only from GICS/known flags."""
    if long_loss:
        return "LONG_TERM_UNPROFITABLE"
    if insufficient_history:
        return "INSUFFICIENT_PROFIT_HISTORY"
    if negative_equity:
        return "NEGATIVE_EQUITY_STRUCTURALLY_INCOMPATIBLE"
    s, i = (sector or "").lower(), (industry or "").lower()
    if "insurance" in i or "insurance" in s:
        return "INSURANCE"
    if "bank" in i or "thrifts" in i or "mortgage finance" in i:
        return "BANK"
    if "reit" in i:
        return "REIT"
    if s == "real estate":
        return "OTHER_SPECIAL"
    if s == "financials":
        return "OTHER_SPECIAL"
    return "ORDINARY_NON_FINANCIAL"


def build_references(
    snapshots: Mapping[str, Mapping[str, Any]],
    classifications: Mapping[str, Mapping[str, Any]],
    excluded: set[str],
    threshold: int,
) -> dict[str, Any]:
    rows: dict[str, dict[str, PeerReference]] = {}
    for ticker, snap in snapshots.items():
        c = classifications.get(ticker, {})
        group_type = c.get("company_type", "SPECIAL_TYPE_UNKNOWN")
        rows[ticker] = {}
        for metric in METRICS:
            value = (snap.get(metric) or {}).get("value") if eligible_metric(snap, metric, excluded) else None
            if value is None:
                rows[ticker][metric] = PeerReference(ticker, metric, None, None, None, 0, threshold, (), "COMPANY_METRIC_UNAVAILABLE", None)
                continue
            same = [other for other, osnap in snapshots.items()
                    if other != ticker and classifications.get(other, {}).get("company_type") == group_type
                    and eligible_metric(osnap, metric, excluded)]
            industry = c.get("industry")
            sector = c.get("sector")
            ind_peers = tuple(sorted(o for o in same if classifications.get(o, {}).get("industry") == industry))
            sec_peers = tuple(sorted(o for o in same if classifications.get(o, {}).get("sector") == sector))
            level, peers = None, ()
            if len(ind_peers) >= threshold:
                level, peers = "GICS_INDUSTRY", ind_peers
            elif len(sec_peers) >= threshold:
                level, peers = "GICS_SECTOR", sec_peers
            if not peers:
                rows[ticker][metric] = PeerReference(ticker, metric, float(value), None, None, 0, threshold, (), "NO_PEER_REFERENCE", None)
                continue
            vals = [float((snapshots[o][metric] or {})["value"]) for o in peers]
            ref = percentile(vals, .5)
            rows[ticker][metric] = PeerReference(ticker, metric, float(value), ref, level, len(peers), threshold, peers, None, float(value) / ref if ref else None)
    return {"threshold": threshold, "rows": {t: {m: r.to_dict() for m, r in ms.items()} for t, ms in rows.items()}}


def atomic_write_artifact(path: Path, payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(body).hexdigest()
    envelope = {"schema_version": "peer-valuation-v1e", "payload": payload, "artifact_hash": digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(envelope, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)
    return digest
