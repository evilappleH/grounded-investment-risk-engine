"""SEC primary filing document evidence; no filing conclusions."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import hashlib, json, os, re
from pathlib import Path
from typing import Any, Callable, Mapping
import requests
from investment_agents.path_safety import redact_secrets, safe_artifact_path, validate_artifact_ticker, validate_run_id

class _TextParser(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.headings=[]; self._heading=None
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"h1","h2","h3","h4"}: self._heading=[]
    def handle_endtag(self, tag):
        if tag.lower() in {"h1","h2","h3","h4"} and self._heading is not None:
            text=" ".join(self._heading).strip()
            if text: self.headings.append(text)
            self._heading=None
    def handle_data(self, data):
        text=" ".join(data.split())
        if text:
            self.parts.append(text)
            if self._heading is not None: self._heading.append(text)

@dataclass(frozen=True)
class FilingContentEvidence:
    evidence_id: str; ticker: str; cik: str; form: str; accession: str; filing_date: date|None; report_date: date|None; document_reference: str; filing_text_available: bool; extracted_sections: Mapping[str,str]; item_metadata: tuple[str,...]; content_hash: str|None; fetched_at: datetime; content_type: str|None; warnings: tuple[str,...]=(); provenance: Mapping[str,Any]=None
    structured_extraction_status: str="PARTIAL"; automated_materiality_eligible: bool=False; official_item_codes: tuple[str,...]=()

class SECPrimaryDocumentAdapter:
    def __init__(self, *, user_agent: str|None=None, get: Callable[...,Any]|None=None, cache_dir: Path|None=None):
        self.user_agent=os.getenv("SEC_USER_AGENT") if user_agent is None else user_agent; self.get=get or requests.get
        self.cache_dir=cache_dir or (Path(__file__).resolve().parents[2]/"data_cache"/"sec_filings"); self.cache_dir.mkdir(parents=True,exist_ok=True)
    def fetch(self, *, ticker: str, cik: str, form: str, accession: str, primary_document: str, filing_date: date|None=None, report_date: date|None=None, official_item_codes: tuple[str,...]=()) -> FilingContentEvidence:
        if not self.user_agent: raise RuntimeError("SEC_PROVIDER_NOT_CONFIGURED: set SEC_USER_AGENT")
        clean=accession.replace("-",""); url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean}/{primary_document}"
        key=f"{str(int(cik)).zfill(10)}_{accession}_{primary_document}".replace("/","_")
        path=self.cache_dir/f"{key}.html"; now=datetime.now(timezone.utc); warnings=[]; content_type=None
        try:
            if path.exists():
                raw=path.read_bytes(); source="cache"
                fetched_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            else:
                response=self.get(url,headers={"User-Agent":self.user_agent},timeout=30); response.raise_for_status(); raw=response.content; content_type=response.headers.get("Content-Type") if hasattr(response,"headers") else None; path.write_bytes(raw); source="sec"
                fetched_at=now
            digest=hashlib.sha256(raw).hexdigest(); parser=_TextParser(); parser.feed(raw.decode("utf-8",errors="replace")); text=" ".join(parser.parts); headings=parser.headings
            if not text: warnings.append("FILING_CONTENT_PARSE_ERROR")
            sections={}
            for heading in headings:
                low=heading.lower()
                if any(term in low for term in ("financial statements","management's discussion","management’s discussion","risk factors","controls and procedures")):
                    sections[heading]=heading
            if text: warnings.append("SECTION_EXTRACTION_PARTIAL")
            extraction_status="PARTIAL" if text else "UNAVAILABLE"
            return FilingContentEvidence(f"sec-content:{accession}",ticker.upper(),str(cik).zfill(10),form,accession,filing_date,report_date,url,bool(text),sections,tuple(official_item_codes),digest,fetched_at,content_type,tuple(dict.fromkeys(warnings)),{"source":source,"url":url,"analysis_mode":"CURRENT_ANALYSIS","pit_safe":False,"cache_read_at":now if source == "cache" else None,"structured_extraction_status":extraction_status,"automated_materiality_eligible":False},extraction_status,False,tuple(official_item_codes))
        except Exception:
            # Keep a traceable reference even when content is unavailable.
            raise

def persist_filing_content(evidence: FilingContentEvidence, *, root: Path) -> Path:
    run_id=validate_run_id(str((evidence.provenance or {}).get("run_id", ""))); ticker=validate_artifact_ticker(evidence.ticker)
    safe_accession=re.sub(r"[^A-Za-z0-9-]", "", evidence.accession)
    if not safe_accession: raise ValueError("accession is required for persistence")
    path=safe_artifact_path(root,"research_runs",run_id,"events",f"{ticker}_{safe_accession}_filing_content.json"); path.parent.mkdir(parents=True,exist_ok=True)
    def safe(v):
        if isinstance(v,dict): return {str(k):safe(x) for k,x in v.items()}
        if isinstance(v,(tuple,list)): return [safe(x) for x in v]
        if hasattr(v,"isoformat"): return v.isoformat()
        return v
    try:
        with path.open("x",encoding="utf-8") as handle: json.dump(redact_secrets(safe(asdict(evidence))),handle,ensure_ascii=True,indent=2)
    except FileExistsError as exc: raise FileExistsError(str(path)) from exc
    return path
