"""Structured, provenance-preserving event evidence adapters."""

from .models import EventEvidence, EventEvidencePackage, EvidenceSourceClass, EvidenceMateriality, SentimentEvidence
from .sec import SECEdgarAdapter
from .bridges import TradingAgentsNewsAdapter, MacroEvidenceAdapter, SentimentEvidenceAdapter
from .package import collect_event_evidence, persist_event_package, persist_macro_context
from .bridges import StructuredYFinanceNewsAdapter, AlphaVantageNewsAdapter
from .macro import FREDStructuredMacroAdapter, MacroContextPackage, MacroObservation
from .capabilities import EventProviderCapability, event_provider_capabilities
from .synthesis import EventSynthesisResult, EventCluster, EvidenceInterpretation, EventMateriality, ThesisImpact, EventSynthesisRuntime, synthesize_event_evidence, build_event_synthesis_prompt, persist_event_synthesis
from .primary import FilingContentEvidence, SECPrimaryDocumentAdapter, persist_filing_content

__all__ = ["EventEvidence", "EventEvidencePackage", "EvidenceSourceClass", "EvidenceMateriality", "SentimentEvidence", "SECEdgarAdapter", "TradingAgentsNewsAdapter", "StructuredYFinanceNewsAdapter", "AlphaVantageNewsAdapter", "MacroEvidenceAdapter", "SentimentEvidenceAdapter", "FREDStructuredMacroAdapter", "MacroContextPackage", "MacroObservation", "EventProviderCapability", "event_provider_capabilities", "collect_event_evidence", "persist_event_package", "persist_macro_context", "EventSynthesisResult", "EventCluster", "EvidenceInterpretation", "EventMateriality", "ThesisImpact", "EventSynthesisRuntime", "synthesize_event_evidence", "build_event_synthesis_prompt", "persist_event_synthesis", "FilingContentEvidence", "SECPrimaryDocumentAdapter", "persist_filing_content"]
