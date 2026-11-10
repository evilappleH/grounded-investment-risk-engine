"""Immutable Committee attempt index and rebuild support."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json, hashlib
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class AttemptPointer:
    attempt_id: str
    attempt_number: int
    path: str
    runtime_status: str
    grounded: bool
    portfolio_eligible: bool

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

class CommitteeAttemptIndex:
    def __init__(self, root: Path, run_id: str, ticker: str):
        self.root=Path(root); self.run_id=run_id; self.ticker=ticker
        self.directory=self.root/"research_runs"/run_id/"committee"/ticker
        self.path=self.directory/"INDEX.json"
    def rebuild(self) -> dict[str, Any]:
        attempts=[]
        for path in sorted(self.directory.glob("attempt-*.json")):
            try:
                d=json.loads(path.read_text(encoding="utf-8")); r=d["result"]
                n=int(d["committee_attempt_number"])
                attempts.append(AttemptPointer(d["committee_attempt_id"],n,str(path),r.get("runtime_status")=="COMPLETE",not any("UNVERIFIED_MODEL_CLAIM" in str(w) for w in r.get("warnings",())),False))
            except (OSError,ValueError,KeyError,TypeError):
                continue
        attempts.sort(key=lambda x:x.attempt_number)
        if [x.attempt_number for x in attempts] != list(dict.fromkeys(x.attempt_number for x in attempts)):
            raise ValueError("DUPLICATE_COMMITTEE_ATTEMPT")
        pointers={"latest":attempts[-1].attempt_id if attempts else None,"latest_schema_valid":next((x.attempt_id for x in reversed(attempts) if x.runtime_status),None),"latest_runtime_complete":next((x.attempt_id for x in reversed(attempts) if x.runtime_status),None),"latest_grounded":next((x.attempt_id for x in reversed(attempts) if x.grounded),None),"latest_portfolio_eligible":None,"attempts":[asdict(x) for x in attempts]}
        payload={"run_id":self.run_id,"ticker":self.ticker,"schema_version":"committee-attempt-index-v1","pointers":pointers}
        payload["index_hash"]=_digest(payload)
        self.directory.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,sort_keys=True,indent=2),encoding='utf-8'); tmp.replace(self.path)
        return payload
    def load_verified(self) -> dict[str,Any]:
        d=json.loads(self.path.read_text(encoding='utf-8')); h=d.pop('index_hash');
        if _digest(d)!=h: raise ValueError("COMMITTEE_INDEX_HASH_MISMATCH")
        return {**d,"index_hash":h}
