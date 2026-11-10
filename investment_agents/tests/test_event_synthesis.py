from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from investment_agents.events import *

class SynthesisTests(unittest.TestCase):
    def setUp(self):
        now=datetime.now(timezone.utc)
        self.sec=EventEvidence("sec:1","KO","8-K",EvidenceSourceClass.PRIMARY_CORPORATE,"SEC EDGAR",title="8-K",published_at=date(2026,9,1),fetched_at=now,reference="sec-ref")
        self.news=EventEvidence("news:1","KO","NEWS_ARTICLE",EvidenceSourceClass.NEWS,"yfinance",title="Headline",fetched_at=now,reference="https://news/a")
        self.package=EventEvidencePackage("KO","run",now,date(2026,8,1),date(2026,9,8),(self.sec,),(self.news,),(),(),{},("NEWS_PUBLISHED_TIME_UNAVAILABLE",),{"analysis_mode":"CURRENT_ANALYSIS","pit_safe":False})
        self.macro=MacroContextPackage("run",date(2026,8,1),date(2026,9,8),(),{"DGS10":{"units":"Percent","frequency":"daily"}},{},now)

    def test_sec_metadata_is_not_material_and_news_time_gap_preserved(self):
        out=synthesize_event_evidence(self.package,self.macro)
        self.assertEqual(out.event_clusters[0].materiality, EventMateriality.MATERIALITY_UNKNOWN)
        self.assertIn("NEWS_PUBLISHED_TIME_UNAVAILABLE", out.data_gaps)
        self.assertEqual(out.thesis_impact, ThesisImpact.INSUFFICIENT_EVIDENCE)

    def test_macro_is_context_not_company_event(self):
        out=synthesize_event_evidence(self.package,self.macro)
        self.assertEqual(out.macro_context_summary[0].startswith("Macro context"),True)
        self.assertNotIn("DGS10", " ".join(out.primary_facts))

    def test_provider_failure_is_not_no_event_and_sentiment_degraded(self):
        p=EventEvidencePackage("KO","run",self.package.generated_at,date(2026,8,1),date(2026,9,8),source_failures={"reddit":"PROVIDER_RATE_LIMITED"})
        out=synthesize_event_evidence(p,None)
        self.assertIn("PROVIDER_FAILURE:reddit", out.data_gaps)
        self.assertEqual(out.sentiment_status,"RAW_SENTIMENT_PROVIDER_RATE_LIMITED")
        self.assertEqual(out.thesis_impact, ThesisImpact.INSUFFICIENT_EVIDENCE)

    def test_duplicate_reference_forms_one_cluster(self):
        duplicate=EventEvidence("news:2","KO","NEWS_ARTICLE",EvidenceSourceClass.NEWS,"other",title="Mirror",reference="https://news/a")
        p=EventEvidencePackage(**{**self.package.__dict__,"news_evidence":(self.news,duplicate)})
        self.assertEqual(len(synthesize_event_evidence(p,self.macro).event_clusters),2)  # SEC and shared news cluster

    def test_dry_run_does_not_call_provider_and_persists(self):
        class Never:
            provider_name="fixture"
            def generate(self, **kwargs): raise AssertionError("must not call")
        runtime=EventSynthesisRuntime(Never(),model="fixture")
        out=runtime.synthesize(self.package,self.macro,execute=False)
        self.assertEqual(out.runtime_status,"COMPLETE")
        with TemporaryDirectory() as d:
            path=persist_event_synthesis(out,root=Path(d)); self.assertTrue(path.exists())

if __name__ == "__main__": unittest.main()
