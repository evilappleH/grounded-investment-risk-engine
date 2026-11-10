from datetime import date, datetime, timezone
import unittest
from investment_agents.events import *
from investment_agents.research import ResearchPackage, build_enhanced_research_package
from investment_agents.llm import build_agent_prompt

class EnhancedTests(unittest.TestCase):
    def setUp(self):
        now=datetime.now(timezone.utc)
        rp=ResearchPackage("KO",None,None,None,{"net_income":1},{}, {"pr":1}, {"warnings":()}, {"status":"PARTIAL"},{"status":"QUALIFIED_WITH_WARNINGS"},{}, {"analysis_mode":"CURRENT_ANALYSIS","methodology_version":"step6c-v1"})
        ev=EventEvidence("sec:1","KO","8-K",EvidenceSourceClass.PRIMARY_CORPORATE,"SEC",title="8-K",fetched_at=now)
        self.ep=EventEvidencePackage("KO","erun",now,date(2026,1,1),date(2026,1,2),(ev,),(),(),(),{},("RAW_SENTIMENT_UNAVAILABLE",),{"analysis_mode":"CURRENT_ANALYSIS","pit_safe":False})
        self.syn=synthesize_event_evidence(self.ep)
        self.enh=build_enhanced_research_package(rp,self.ep,self.syn,None)

    def test_provenance_and_layers_preserved(self):
        self.assertEqual(self.enh.provenance["pit_safe"],False)
        self.assertIn("sec:1",self.enh.evidence_layers["PRIMARY_CORPORATE_FACT"])
        self.assertIn("RAW_SENTIMENT_UNAVAILABLE",self.enh.evidence_layers["DATA_GAP / PROVIDER_FAILURE"])
        self.assertEqual(self.enh.event_synthesis.overall_materiality,EventMateriality.MATERIALITY_UNKNOWN)

    def test_agent_prompt_contains_events_and_boundaries(self):
        preview=build_agent_prompt(self.enh,"buffett",model="fixture")
        self.assertIn("event_evidence_package",preview.user_prompt)
        self.assertIn("RAW_SENTIMENT_UNAVAILABLE",preview.user_prompt)
        self.assertIn("CURRENT_ANALYSIS",preview.user_prompt)
        self.assertIn("not-PIT-safe",preview.user_prompt)
        self.assertNotIn("BUY/SELL",preview.user_prompt)

    def test_structured_baseline_is_not_persona_thesis(self):
        self.assertEqual(self.enh.event_synthesis.baseline_type,"NO_BASELINE")
        self.assertNotIn("Buffett",repr(self.enh.provenance))

if __name__=='__main__': unittest.main()
