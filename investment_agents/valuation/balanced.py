"""Approved deterministic Balanced Valuation V1 (peer PE only)."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping
import hashlib, json

VERSION = "BALANCED_VALUATION_V1_PEER_PE_001"

@dataclass(frozen=True)
class BalancedValuationV1Record:
    ticker: str; methodology_version: str; company_type: str
    current_pe: float|None; peer_pe_median: float|None; peer_level: str|None
    peer_count: int; peer_set_hash: str|None; pe_peer_ratio: float|None
    valuation_classification: str; evidence_strength: str
    history_context_status: str; historical_pe_median: float|None; historical_pe_ratio: float|None
    current_pb: float|None; peer_pb_median: float|None; pb_peer_ratio: float|None
    pr: float|None; pr_status: str; data_gaps: tuple[str,...]; reason_codes: tuple[str,...]
    as_of: str|None; source_hashes: Mapping[str,str|None]

def _hash_peers(peers: Any) -> str|None:
    if not peers: return None
    return hashlib.sha256(json.dumps(sorted(peers), separators=(",",":"), ensure_ascii=True).encode()).hexdigest()

def build_balanced_record(ticker: str, snapshot: Mapping[str,Any], classification: Mapping[str,Any],
                          peer_row: Mapping[str,Any]|None, hist_row: Mapping[str,Any]|None,
                          hist_median: Mapping[str,Any]|None, *, snapshot_hash: str|None=None,
                          peer_hash: str|None=None, history_hash: str|None=None,
                          pb_peer_row: Mapping[str,Any]|None=None) -> BalancedValuationV1Record:
    ctype = str(classification.get("company_type", "SPECIAL_TYPE_UNKNOWN"))
    unsupported = ctype != "ORDINARY_NON_FINANCIAL"
    long_loss = ctype == "LONG_TERM_UNPROFITABLE"
    pef = snapshot.get("pe_ttm") or {}; pbf = snapshot.get("pb") or {}; prf = snapshot.get("pr") or {}
    current_pe = pef.get("value") if pef.get("status") == "AVAILABLE" and isinstance(pef.get("value"),(int,float)) and pef.get("value")>0 else None
    current_pb = pbf.get("value") if pbf.get("status") == "AVAILABLE" and isinstance(pbf.get("value"),(int,float)) and pbf.get("value")>0 else None
    row = peer_row or {}; pref = row.get("reference_value"); pcount = int(row.get("peer_count") or 0)
    level = row.get("reference_level") if pref and pcount >= 10 else None
    ratio = float(current_pe/pref) if current_pe is not None and level and float(pref)>0 else None
    cls = "VALUATION_EVIDENCE_INSUFFICIENT"; strength = "VALUATION_EVIDENCE_INSUFFICIENT"; reasons=[]
    if long_loss: cls="EXCLUDED_FROM_BALANCED_UNIVERSE"; reasons.append("LONG_TERM_UNPROFITABLE")
    elif unsupported: cls="VALUATION_MODEL_NOT_SUPPORTED_V1"; reasons.append("UNSUPPORTED_COMPANY_TYPE")
    elif ratio is None: reasons.append("VALUATION_EVIDENCE_INSUFFICIENT")
    else:
        cls = "VALUATION_ATTRACTIVE" if ratio <= .85 else ("VALUATION_REASONABLE" if ratio <= 1.15 else "VALUATION_EXPENSIVE")
        strength = "VALUATION_EVIDENCE_ADEQUATE"
    hcount = int((hist_row or {}).get("pe_a_count") or 0); hm = (hist_median or {}).get("PE") or {}
    hmedian = hm.get("value") if hcount >= 3 and hm.get("status") in {"5Y_REFERENCE","LIMITED_REFERENCE"} else None
    hctx = "ROBUST_HISTORY_CONTEXT" if hcount >= 5 else ("LIMITED_HISTORY_CONTEXT" if hcount >= 3 else "INSUFFICIENT_HISTORY_CONTEXT")
    if strength == "VALUATION_EVIDENCE_ADEQUATE" and hcount >= 5: strength="VALUATION_EVIDENCE_STRONG"
    hratio = float(current_pe/hmedian) if current_pe and hmedian and hmedian>0 else None
    pbrow = pb_peer_row or {}; pbref = pbrow.get("reference_value") if pbrow.get("reference_level") and int(pbrow.get("peer_count") or 0) >= 10 else None
    pbratio = float(current_pb/pbref) if current_pb is not None and pbref and pbref > 0 else None
    gaps=[]
    if prf.get("status") != "AVAILABLE": gaps.append("PR_UNAVAILABLE")
    if not (snapshot.get("fcf_yield") or {}).get("status") == "AVAILABLE": gaps.append("FCF_UNAVAILABLE")
    return BalancedValuationV1Record(ticker, VERSION, ctype, current_pe, float(pref) if level else None, level, pcount if level else 0, _hash_peers(row.get("peer_tickers")) if level else None, ratio, cls, strength, hctx, hmedian, hratio, current_pb, pbref, pbratio, prf.get("value") if prf.get("status")=="AVAILABLE" else None, str(prf.get("status") or "UNAVAILABLE"), tuple(gaps), tuple(dict.fromkeys(reasons)), snapshot.get("as_of"), {"snapshot":snapshot_hash,"peer":peer_hash,"history":history_hash})

def record_dict(r: BalancedValuationV1Record) -> dict[str,Any]:
    d=asdict(r); d["data_gaps"]=list(r.data_gaps); d["reason_codes"]=list(r.reason_codes); return d
