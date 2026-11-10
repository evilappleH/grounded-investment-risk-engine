"""Explicit composition of financial ResearchPackage and event evidence."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
from .queue import ResearchPackage

@dataclass(frozen=True)
class EnhancedResearchPackage:
    research_package: ResearchPackage|Mapping[str,Any]
    event_evidence_package: Any
    event_synthesis: Any
    macro_context_package: Any|None
    provenance: Mapping[str,Any]
    evidence_layers: Mapping[str,tuple[str,...]]

    @property
    def ticker(self) -> str:
        return self.research_package.ticker if hasattr(self.research_package,"ticker") else str(self.research_package.get("ticker",""))

def build_enhanced_research_package(research_package: ResearchPackage|Mapping[str,Any], event_evidence_package: Any, event_synthesis: Any, macro_context_package: Any|None=None) -> EnhancedResearchPackage:
    research_ticker = research_package.ticker if hasattr(research_package,"ticker") else str(research_package.get("ticker", ""))
    if research_ticker != getattr(event_evidence_package, "ticker", None) or research_ticker != getattr(event_synthesis, "ticker", None):
        raise ValueError("research, event evidence, and event synthesis tickers must match")
    event_run_id = getattr(event_evidence_package, "run_id", None)
    if event_run_id != getattr(event_synthesis, "run_id", None):
        raise ValueError("event evidence and synthesis run_id must match")
    if macro_context_package is not None and getattr(macro_context_package, "run_id", event_run_id) not in (None, event_run_id):
        raise ValueError("macro context run_id must match event evidence run_id")
    base_prov = research_package.provenance if hasattr(research_package,"provenance") else research_package.get("provenance", {})
    event_prov = getattr(event_evidence_package,"provenance", {}) or {}
    synthesis_prov = {"interpretation_only": True, "overall_materiality": str(getattr(event_synthesis,"overall_materiality", "")), "thesis_impact": str(getattr(event_synthesis,"thesis_impact", ""))}
    provenance = {"research_provenance": base_prov, "event_provenance": event_prov, "synthesis_provenance": synthesis_prov, "analysis_mode": "CURRENT_ANALYSIS", "pit_safe": False}
    layers={"PRIMARY_CORPORATE_FACT": tuple(x.evidence_id for x in getattr(event_evidence_package,"primary_corporate_evidence",())), "SECONDARY_NEWS_CLAIM": tuple(x.evidence_id for x in getattr(event_evidence_package,"news_evidence",())), "MACRO_CONTEXT": tuple(x.evidence_id for x in getattr(event_evidence_package,"macro_evidence",())), "MODEL_INTERPRETATION": tuple(f"synthesis:{i}" for i,_ in enumerate(getattr(event_synthesis,"evidence_map",()),1)), "DATA_GAP / PROVIDER_FAILURE": tuple(getattr(event_synthesis,"data_gaps",()))}
    return EnhancedResearchPackage(research_package,event_evidence_package,event_synthesis,macro_context_package,provenance,layers)

def enhanced_prompt_payload(package: EnhancedResearchPackage) -> dict[str,Any]:
    base=asdict(package.research_package) if hasattr(package.research_package,"__dataclass_fields__") else dict(package.research_package)
    return {**base,"event_evidence_package":asdict(package.event_evidence_package) if hasattr(package.event_evidence_package,"__dataclass_fields__") else package.event_evidence_package,"event_synthesis":asdict(package.event_synthesis) if hasattr(package.event_synthesis,"__dataclass_fields__") else package.event_synthesis,"macro_context_package":asdict(package.macro_context_package) if hasattr(package.macro_context_package,"__dataclass_fields__") else package.macro_context_package,"enhanced_provenance":package.provenance,"evidence_layers":package.evidence_layers}
