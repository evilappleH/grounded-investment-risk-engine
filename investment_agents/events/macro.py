"""Direct structured FRED observations, shared at run level."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
import os
from typing import Any, Mapping, Callable
import requests

FRED_SERIES = {"fed_funds_rate": ("FEDFUNDS", "Percent", "monthly"), "10y_treasury": ("DGS10", "Percent", "daily"), "cpi": ("CPIAUCSL", "Index", "monthly"), "unemployment_rate": ("UNRATE", "Percent", "monthly")}

@dataclass(frozen=True)
class MacroObservation:
    series_id: str; observation_date: date; value: float; units: str; frequency: str; source: str; fetched_at: datetime; analysis_mode: str = "CURRENT_ANALYSIS"

@dataclass(frozen=True)
class MacroContextPackage:
    run_id: str | None; lookback_start: date; lookback_end: date; observations: tuple[MacroObservation, ...]; series_metadata: Mapping[str, Mapping[str, str]]; source_failures: Mapping[str, str]; fetched_at: datetime; analysis_mode: str = "CURRENT_ANALYSIS"; pit_safe: bool = False

class FREDStructuredMacroAdapter:
    source = "fred"
    def __init__(self, *, api_key: str | None = None, get: Callable[..., Any] | None = None) -> None: self.api_key = os.getenv("FRED_API_KEY") if api_key is None else api_key; self.get = get or requests.get
    def fetch_context(self, *, run_id: str | None = None, end: date | None = None, lookback_days: int = 30, series: tuple[str, ...] = tuple(FRED_SERIES)) -> MacroContextPackage:
        end = end or date.today(); start = end - timedelta(days=lookback_days); now = datetime.now(timezone.utc); obs=[]; failures={}; metadata={}
        if not self.api_key: return MacroContextPackage(run_id,start,end,(),{}, {"fred":"FRED_PROVIDER_NOT_CONFIGURED"}, now)
        for name in series:
            if name not in FRED_SERIES: failures[name] = "UNKNOWN_SERIES"; continue
            sid, units, frequency = FRED_SERIES[name]; metadata[sid] = {"name":name,"units":units,"frequency":frequency}
            try:
                response = self.get("https://api.stlouisfed.org/fred/series/observations", params={"series_id":sid,"api_key":self.api_key,"file_type":"json","observation_start":start.isoformat(),"observation_end":end.isoformat()}, timeout=20); response.raise_for_status(); rows=response.json().get("observations",[])
                for row in rows:
                    if row.get("value") in (None,".",""): continue
                    obs.append(MacroObservation(sid,date.fromisoformat(row["date"]),float(row["value"]),units,frequency,self.source,now))
            except Exception as exc: failures[sid] = type(exc).__name__
        return MacroContextPackage(run_id,start,end,tuple(obs),metadata,failures,now)
