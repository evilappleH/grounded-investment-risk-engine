"""Small deterministic bridge from Balanced valuation to policy preflight."""
from __future__ import annotations
from typing import Mapping, Any

def valuation_allows_new_position(record: Mapping[str, Any]) -> tuple[bool, str]:
    if record.get("company_type") == "LONG_TERM_UNPROFITABLE":
        return False, "EXCLUDED_FROM_BALANCED_UNIVERSE"
    if record.get("company_type") != "ORDINARY_NON_FINANCIAL":
        return False, "VALUATION_MODEL_NOT_SUPPORTED_V1"
    if record.get("evidence_strength") not in {"VALUATION_EVIDENCE_ADEQUATE", "VALUATION_EVIDENCE_STRONG"}:
        return False, "VALUATION_EVIDENCE_INSUFFICIENT"
    if record.get("valuation_classification") == "VALUATION_EXPENSIVE":
        return False, "VALUATION_TOO_EXPENSIVE_FOR_NEW_POSITION_V1"
    if record.get("valuation_classification") not in {"VALUATION_ATTRACTIVE", "VALUATION_REASONABLE"}:
        return False, "VALUATION_EVIDENCE_INSUFFICIENT"
    return True, "VALUATION_PASS_CONTINUE_RESEARCH"
