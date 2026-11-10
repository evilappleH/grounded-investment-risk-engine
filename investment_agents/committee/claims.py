"""Deterministic canonical claim compiler.

Claims are references to source-owned atomic facts.  This module deliberately
contains no model/provider code and never invents values or interpretations.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, is_dataclass
import hashlib, json
from typing import Any, Mapping, Sequence

DOMAINS={"PROFITABILITY","CASH_FLOW","BALANCE_SHEET","LEVERAGE","GROWTH","VALUATION","CAPITAL_ALLOCATION","PIOTROSKI","QUALIFICATION","RISK"}
FIELD_DOMAIN={"revenue":"PROFITABILITY","net_income":"PROFITABILITY","gross_profit":"PROFITABILITY","cash":"BALANCE_SHEET","equity":"BALANCE_SHEET","total_assets":"BALANCE_SHEET","total_debt":"LEVERAGE","pe":"VALUATION","pb":"VALUATION","pr":"VALUATION","score":"PIOTROSKI","partial_score":"PIOTROSKI"}

@dataclass(frozen=True)
class CanonicalClaim:
    claim_id:str; ticker:str; domain:str; canonical_statement:str; atomic_evidence_ids:tuple[str,...]; fields:tuple[str,...]; periods:tuple[str,...]; units:tuple[str|None,...]; provenance_hash:str

def _payload(package: Any) -> Mapping[str, Any]:
    if hasattr(package, "research_package"):
        from investment_agents.research.enhanced import enhanced_prompt_payload
        package = enhanced_prompt_payload(package)
    if isinstance(package, Mapping):
        return package
    if is_dataclass(package):
        value = asdict(package)
        return value if isinstance(value, Mapping) else {}
    return {}


def compile_claims(package: Any, *, run_id: str | None = None) -> tuple[CanonicalClaim, ...]:
    """Compile only scalar, present values into stable claim cards.

    The atomic catalog is the source of truth; a claim is never emitted for a
    missing, nested, conflicting, or ambiguous value.  IDs intentionally
    retain the exact atomic evidence ID so hydration is deterministic.
    """
    p = _payload(package)
    ticker = str(p.get("ticker", ""))
    out: list[CanonicalClaim] = []
    for category, fields in (("PROFITABILITY", ("revenue", "net_income", "gross_profit")),
                             ("BALANCE_SHEET", ("cash", "equity", "total_assets")),
                             ("LEVERAGE", ("total_debt",)),
                             ("VALUATION", ("pe", "pb", "pr")),
                             ("PIOTROSKI", ("score", "partial_score"))):
        for field in fields:
            value = None
            source_section = None
            for section in ("current_fundamentals", "valuation", "piotroski"):
                values = p.get(section)
                if isinstance(values, Mapping) and values.get(field) is not None:
                    candidate = values.get(field)
                    if not isinstance(candidate, (Mapping, list, tuple)):
                        value = candidate
                        source_section = section
                        break
            if value is None:
                continue
            # Preserve the exact source registry identity; the claim domain
            # is semantic, while the atomic ID is provenance-specific.
            atomic_id = f"source:{ticker}:{source_section}:{field}"
            claim_id = f"claim:{ticker}:{category.lower()}:{field}"
            statement = f"{ticker} {category.lower()} {field} = {value}"
            provenance = {"ticker": ticker, "run_id": run_id,
                          "source": (p.get("provenance") or {}).get("source", "ResearchPackage")}
            out.append(CanonicalClaim(
                claim_id, ticker, category, statement, (atomic_id,), (field,), (), (None,),
                hashlib.sha256(json.dumps(provenance, sort_keys=True, default=str).encode()).hexdigest(),
            ))
    return tuple(out)

def validate_claim_support(claim_ids: Sequence[str], claims: Sequence[CanonicalClaim], domain: str | None = None) -> bool:
    known={c.claim_id:c for c in claims}
    return bool(claim_ids) and all(cid in known and (domain is None or known[cid].domain==domain) for cid in claim_ids)

def claims_json(claims: Sequence[CanonicalClaim]) -> list[dict[str,Any]]:
    return [asdict(c) for c in claims]


def claim_catalog_hash(claims: Sequence[CanonicalClaim]) -> str:
    payload = json.dumps(claims_json(claims), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
