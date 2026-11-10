"""Test-only deterministic selector; never used for investment ranking."""
from __future__ import annotations
from typing import Any, Mapping, Sequence

CONTRACT = "PRE_AUTO_TEST_SELECTOR_V1"

def select_pre_auto_candidate(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible=[]
    for c in candidates:
        if c.get("portfolio_policy") != "ELIGIBLE" or c.get("risk_gate") != "PASS":
            continue
        price=c.get("reference_price")
        if not isinstance(price,(int,float)) or price <= 0 or price > 500:
            continue
        eligible.append(c)
    if not eligible:
        return {"status":"PRE_AUTO_SELECTION_AMBIGUOUS","selected":None,"reason":"NO_TEST_ELIGIBLE_CANDIDATE","selection_contract":CONTRACT,"selection_purpose":"EXECUTION_PLUMBING_TEST_ONLY","not_investment_ranking":True}
    committee_rank={"AGREEMENT":0,"PARTIAL_AGREEMENT":1}
    valuation_rank={"VALUATION_ATTRACTIVE":0,"VALUATION_REASONABLE":1}
    best_committee=min(committee_rank.get(str(c.get("committee_status")),99) for c in eligible)
    eligible=[c for c in eligible if committee_rank.get(str(c.get("committee_status")),99)==best_committee]
    best_val=min(valuation_rank.get(str(c.get("valuation_classification")),99) for c in eligible)
    eligible=[c for c in eligible if valuation_rank.get(str(c.get("valuation_classification")),99)==best_val]
    min_price=min(float(c["reference_price"]) for c in eligible)
    eligible=[c for c in eligible if float(c["reference_price"])==min_price]
    if len(eligible)!=1:
        return {"status":"PRE_AUTO_SELECTION_AMBIGUOUS","selected":None,"reason":"EXACT_TEST_NOTIONAL_TIE","selection_contract":CONTRACT,"selection_purpose":"EXECUTION_PLUMBING_TEST_ONLY","not_investment_ranking":True}
    return {"status":"SELECTED","selected":eligible[0].get("ticker"),"resolution_stage":"SMALLER_ONE_SHARE_TEST_NOTIONAL" if len(eligible)>1 else "COMMITTEE_AND_VALUATION_PREFERENCE","selection_contract":CONTRACT,"selection_purpose":"EXECUTION_PLUMBING_TEST_ONLY","not_investment_ranking":True}
