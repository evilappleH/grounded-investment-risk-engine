from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from investment_agents.events import *

class Resp:
    content=b"<html><h1>Risk Factors</h1><p>Text</p><h2>Financial Statements</h2></html>"
    headers={"Content-Type":"text/html"}
    def raise_for_status(self): pass

class PrimaryEvidenceTests(unittest.TestCase):
    def test_fetch_normalizes_html_hashes_and_cache_reuse(self):
        calls=[]
        with TemporaryDirectory() as d:
            def get(*args,**kwargs): calls.append(args[0]); return Resp()
            a=SECPrimaryDocumentAdapter(user_agent="test",get=get,cache_dir=Path(d))
            one=a.fetch(ticker="KO",cik="0000021344",form="10-Q",accession="0001-02-000003",primary_document="ko.htm",filing_date=date(2026,7,1))
            two=a.fetch(ticker="KO",cik="0000021344",form="10-Q",accession="0001-02-000003",primary_document="ko.htm",filing_date=date(2026,7,1))
            self.assertEqual(len(calls),1); self.assertEqual(one.content_hash,two.content_hash); self.assertTrue(one.filing_text_available); self.assertIn("Risk Factors",one.extracted_sections)

    def test_missing_content_and_config(self):
        class Empty(Resp): content=b"<html></html>"
        with TemporaryDirectory() as d:
            out=SECPrimaryDocumentAdapter(user_agent="test",get=lambda *a,**k: Empty(),cache_dir=Path(d)).fetch(ticker="KO",cik="1",form="8-K",accession="1-2",primary_document="x.htm")
            self.assertFalse(out.filing_text_available); self.assertIn("FILING_CONTENT_PARSE_ERROR",out.warnings)
        with self.assertRaisesRegex(RuntimeError,"SEC_PROVIDER_NOT_CONFIGURED"):
            SECPrimaryDocumentAdapter(user_agent="",get=lambda *a,**k: Resp()).fetch(ticker="KO",cik="1",form="8-K",accession="1-2",primary_document="x.htm")

    def test_metadata_only_does_not_raise_materiality(self):
        now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        e=EventEvidence("sec:1","KO","8-K",EvidenceSourceClass.PRIMARY_CORPORATE,"SEC",title="8-K",fetched_at=now)
        p=EventEvidencePackage("KO","r",now,date(2026,1,1),date(2026,1,2),(e,))
        self.assertEqual(synthesize_event_evidence(p).overall_materiality,EventMateriality.MATERIALITY_UNKNOWN)

if __name__=='__main__': unittest.main()
