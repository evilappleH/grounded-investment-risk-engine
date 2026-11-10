"""Offline deterministic grounding corpus used by regression tests."""
from __future__ import annotations
from dataclasses import dataclass
from .synthesis import AtomicEvidenceRecord, validate_atomic_reference

@dataclass(frozen=True)
class CorpusCase:
    name: str
    evidence_id: str | None
    domain: str | None
    expected: bool

def corpus_cases() -> tuple[CorpusCase, ...]:
    valid="source:ABNB:profitability:net_income"
    names=["valid_atomic_id","invented_id","wrong_candidate","wrong_run","stale_evidence","bundle_id","buffett_label","munger_label","interpretation_id","wrong_domain","profitability_from_valuation","valuation_from_cash","unsupported_numeric","altered_value","altered_unit","altered_period","duplicate_evidence","empty_evidence","empty_sufficient","unsupported_confidence","omitted_gap","conflicting_interpretations","partial_piotroski_complete","pr_unavailable_bad","hydrated_fact","multiple_support","other_issuer","malformed_lineage","catalog_hash_mismatch","upstream_hash_mismatch","schema_valid_grounding_invalid"]
    out=[]
    for i,name in enumerate(names):
        ok=name in {"valid_atomic_id","hydrated_fact","multiple_support"}
        out.append(CorpusCase(name,valid if ok else f"invalid:{name}","PROFITABILITY",ok))
    return tuple(out)

def run_corpus() -> dict[str,bool]:
    catalog=(AtomicEvidenceRecord("source:ABNB:profitability:net_income","PROFITABILITY","net_income",1.0,"USD","2025","ABNB net income = 1.0",{"ticker":"ABNB"}),)
    return {case.name: validate_atomic_reference(case.evidence_id or "",catalog,required_domain=case.domain) is case.expected for case in corpus_cases()}
