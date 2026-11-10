"""Deterministic safety-critical committee decision layer.

The LLM Committee remains an optional narrative facility.  This module is
deliberately pure: it consumes verified agent outputs and canonical claims and
cannot create source facts, scores, recommendations, or position sizes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from investment_agents.llm.runtime import InvestorResearchResult, ResearchResultStatus
from .claims import CanonicalClaim

ALLOWED_STANCES = frozenset({
    "ATTRACTIVE_FOR_FURTHER_RESEARCH", "MIXED", "UNATTRACTIVE_FOR_THIS_STYLE",
    "INSUFFICIENT_EVIDENCE",
})


@dataclass(frozen=True)
class AgentDecisionRecord:
    agent: str
    ticker: str
    run_id: str | None
    status: str
    stance: str
    supporting_claim_ids: tuple[str, ...]
    risk_claim_ids: tuple[str, ...]
    valuation_claim_ids: tuple[str, ...]
    material_data_gaps: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    evidence_sufficiency: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitteeDecisionRecord:
    ticker: str
    run_id: str | None
    committee_status: str
    research_consensus: str
    evidence_sufficiency: str
    valuation_sufficiency: str
    shared_supporting_claim_ids: tuple[str, ...]
    buffett_only_claim_ids: tuple[str, ...]
    munger_only_claim_ids: tuple[str, ...]
    shared_risk_claim_ids: tuple[str, ...]
    material_data_gaps: tuple[str, ...]
    reason_codes: tuple[str, ...]
    portfolio_policy_consideration: str
    invalidation_conditions: tuple[str, ...]


def _claims_by_domain(claims: Sequence[CanonicalClaim], *domains: str) -> tuple[str, ...]:
    wanted = set(domains)
    return tuple(sorted(c.claim_id for c in claims if c.domain in wanted))


def build_agent_decision(result: InvestorResearchResult, claims: Sequence[CanonicalClaim], *, run_id: str | None = None) -> AgentDecisionRecord:
    known = {claim.claim_id for claim in claims}
    stance = str(result.research_status or "INSUFFICIENT_EVIDENCE")
    valid = result.status == ResearchResultStatus.COMPLETE and stance in ALLOWED_STANCES
    gaps = tuple(dict.fromkeys(str(x) for x in (*result.data_limitations, *result.key_unknowns)))
    # Agent output contains interpretations, not source IDs.  The deterministic
    # record therefore exposes only verified claims available for the relevant
    # evidence domains; it never treats prose as a fact.
    reasons: list[str] = []
    supplied = tuple(str(cid) for cid in (getattr(result, "supporting_claim_ids", ()) or ()))
    if supplied:
        supporting = supplied
        if any(cid not in known for cid in supporting):
            valid = False
            reasons.append("INVALID_CLAIM_ID")
    else:
        supporting = tuple(cid for cid in _claims_by_domain(claims, "PROFITABILITY", "CASH_FLOW", "BALANCE_SHEET", "LEVERAGE", "GROWTH") if cid in known)
    risk = tuple(cid for cid in _claims_by_domain(claims, "BALANCE_SHEET", "LEVERAGE", "RISK") if cid in known)
    valuation = tuple(cid for cid in _claims_by_domain(claims, "VALUATION") if cid in known)
    if not valid:
        reasons.append("AGENT_RESULT_INVALID")
    if not supporting:
        reasons.append("NO_VERIFIED_SUPPORTING_CLAIMS")
    if not valuation:
        reasons.append("VALUATION_EVIDENCE_INSUFFICIENT")
    evidence = "SUFFICIENT" if valid and supporting else "INSUFFICIENT"
    return AgentDecisionRecord(result.agent_name, result.ticker, run_id or result.source_run_id, "VALID" if valid else "INVALID", stance, supporting, risk, valuation, gaps, tuple(result.conditions_that_would_change_view), evidence, tuple(reasons))


def build_committee_decision(buffett: AgentDecisionRecord, munger: AgentDecisionRecord, claims: Sequence[CanonicalClaim], *, valuation_status: str | None = None, data_gaps: Sequence[str] = ()) -> CommitteeDecisionRecord:
    same_identity = buffett.ticker == munger.ticker and buffett.run_id == munger.run_id
    valid = same_identity and buffett.status == "VALID" and munger.status == "VALID"
    shared = tuple(sorted(set(buffett.supporting_claim_ids) & set(munger.supporting_claim_ids)))
    b_only = tuple(sorted(set(buffett.supporting_claim_ids) - set(munger.supporting_claim_ids)))
    m_only = tuple(sorted(set(munger.supporting_claim_ids) - set(buffett.supporting_claim_ids)))
    shared_risk = tuple(sorted(set(buffett.risk_claim_ids) & set(munger.risk_claim_ids)))
    gaps = tuple(dict.fromkeys((*data_gaps, *buffett.material_data_gaps, *munger.material_data_gaps)))
    valuation = valuation_status or ("COMPLETE" if buffett.valuation_claim_ids and munger.valuation_claim_ids else "INSUFFICIENT")
    if not valid:
        status, evidence, policy, reasons = "INSUFFICIENT_EVIDENCE", "INSUFFICIENT", "INELIGIBLE", ("INVALID_AGENT_DECISION",)
    else:
        status = "AGREEMENT" if buffett.stance == munger.stance else "PARTIAL_AGREEMENT"
        evidence = "SUFFICIENT" if shared else "INSUFFICIENT"
        reasons = [] if shared else ["NO_SHARED_VERIFIED_SUPPORT"]
        policy = "ELIGIBLE" if evidence == "SUFFICIENT" and valuation == "COMPLETE" else "INELIGIBLE"
        if valuation != "COMPLETE": reasons.append("VALUATION_EVIDENCE_INSUFFICIENT")
    if not same_identity:
        reasons = (*reasons, "AGENT_IDENTITY_MISMATCH")
    return CommitteeDecisionRecord(buffett.ticker, buffett.run_id, status, "AGREE" if buffett.stance == munger.stance else "DISAGREE", evidence, valuation, shared, b_only, m_only, shared_risk, gaps, tuple(dict.fromkeys(reasons)), policy, tuple(dict.fromkeys((*buffett.invalidation_conditions, *munger.invalidation_conditions))))
