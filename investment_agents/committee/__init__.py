"""Investment research synthesis; no recommendation or trading decision."""

from .synthesis import (
    AtomicEvidenceRecord, CommitteeResearchResult, CommitteeRuntime, CommitteeStatus, EvidenceItem, build_atomic_evidence_catalog, validate_atomic_reference, build_committee_prompt,
    persist_committee_result, synthesize_committee,
)
from .claims import CanonicalClaim, claim_catalog_hash, claims_json, compile_claims, validate_claim_support
from .deterministic import AgentDecisionRecord, CommitteeDecisionRecord, build_agent_decision, build_committee_decision
from .agent_v2 import AGENT_CONTRACT_VERSION, ClaimMenuItem, build_claim_menu, normalize_agent_v2

__all__ = ["AtomicEvidenceRecord", "CommitteeResearchResult", "CommitteeRuntime", "CommitteeStatus", "EvidenceItem", "build_atomic_evidence_catalog", "validate_atomic_reference", "build_committee_prompt", "persist_committee_result", "synthesize_committee", "CanonicalClaim", "compile_claims", "claims_json", "claim_catalog_hash", "validate_claim_support", "AgentDecisionRecord", "CommitteeDecisionRecord", "build_agent_decision", "build_committee_decision", "AGENT_CONTRACT_VERSION", "ClaimMenuItem", "build_claim_menu", "normalize_agent_v2"]
