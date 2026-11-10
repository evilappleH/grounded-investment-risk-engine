"""Versioned, reference-only AgentDecisionRecord V2 contract."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence
from .claims import CanonicalClaim

AGENT_CONTRACT_VERSION = "AGENT_DECISION_RECORD_V2"
ALLOWED_STANCES = frozenset({"ATTRACTIVE_FOR_FURTHER_RESEARCH","MIXED","UNATTRACTIVE_FOR_THIS_STYLE","INSUFFICIENT_EVIDENCE"})

@dataclass(frozen=True)
class ClaimMenuItem:
    alias: str; claim_id: str; domain: str; canonical_statement: str; period: str|None = None; as_of: str|None = None

def build_claim_menu(claims: Sequence[CanonicalClaim]) -> tuple[ClaimMenuItem,...]:
    ordered=sorted(claims,key=lambda c:(c.domain,c.claim_id))
    return tuple(ClaimMenuItem(f"C{i:03d}",c.claim_id,c.domain,c.canonical_statement,(c.periods[0] if c.periods else None),None) for i,c in enumerate(ordered,1))

def alias_map(menu: Sequence[ClaimMenuItem]) -> dict[str,str]: return {x.alias:x.claim_id for x in menu}

def normalize_agent_v2(raw: Mapping[str,Any], *, ticker: str, run_id: str|None, claims: Sequence[CanonicalClaim], menu: Sequence[ClaimMenuItem]) -> dict[str,Any]:
    amap=alias_map(menu); known={c.claim_id:c for c in claims}; reasons=[]
    stance=str(raw.get("stance", "INSUFFICIENT_EVIDENCE"))
    if stance not in ALLOWED_STANCES: reasons.append("INVALID_STANCE")
    def resolve(values):
        out=[]
        for v in values or ():
            if v not in amap: reasons.append("AGENT_UNKNOWN_CLAIM_REFERENCE")
            elif amap[v] not in out:
                claim=known.get(amap[v])
                if claim is None or claim.ticker != ticker: reasons.append("AGENT_CLAIM_WRONG_TICKER")
                else: out.append(amap[v])
        return tuple(out)
    supporting=resolve(raw.get("supporting_claim_refs")); risks=resolve(raw.get("risk_claim_refs"))
    interpretations=[]
    for item in raw.get("interpretations", ()) or ():
        if not isinstance(item,Mapping): reasons.append("MALFORMED_INTERPRETATION"); continue
        ids=resolve(item.get("supporting_claim_refs"));
        requested_domain=item.get("domain")
        if requested_domain and any(known[cid].domain != requested_domain for cid in ids): reasons.append("AGENT_CLAIM_DOMAIN_MISMATCH")
        if not ids: reasons.append("AGENT_CLAIM_SUPPORT_INSUFFICIENT")
        interpretations.append({"statement":str(item.get("statement","")),"supporting_claim_ids":list(ids)})
    valid=not reasons and (bool(supporting) or stance=="INSUFFICIENT_EVIDENCE")
    return {"agent_contract_version":AGENT_CONTRACT_VERSION,"ticker":ticker,"run_id":run_id,"status":"VALID" if valid else "INVALID","stance":stance,"supporting_claim_ids":list(supporting),"risk_claim_ids":list(risks),"interpretations":interpretations,"material_data_gaps":[str(x) for x in raw.get("material_data_gaps",()) or ()],"invalidation_conditions":[str(x) for x in raw.get("invalidation_conditions",()) or ()],"reason_codes":list(dict.fromkeys(reasons))}

def menu_json(menu: Sequence[ClaimMenuItem]): return [asdict(x) for x in menu]
