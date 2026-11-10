from __future__ import annotations
import collections, datetime, json, os
from pathlib import Path
from .v1d import run_sec_input_audit

def main() -> None:
    result = run_sec_input_audit(
        run_path=Path("data_cache/research_runs/72e523511db44863b9454bba2250de7b"),
        sec_cache=Path("data_cache/sec_companyfacts"),
        valuation_cache=Path("data_cache/CURRENT_ANALYSIS_VALUATION"),
        user_agent=os.environ.get("SEC_USER_AGENT", ""),
        as_of=datetime.date(2026, 9, 12),
    )
    print("RECORDS", result["record_count"], "MAP", result["identity_map_count"])
    print("CIK_AVAIL", sum(v["status"] == "AVAILABLE" for v in result["cik"].values()))
    print("SHARES_AVAIL", sum(v.get("status") == "AVAILABLE" for v in result["shares"].values()))
    print("SHARE_REASONS", dict(collections.Counter(v.get("reason") for v in result["shares"].values())))
    print("FIVE", {t: result["shares"].get(t) for t in ["ABNB", "AMAT", "AOS", "COIN", "COST"]})
    slim = {k: result[k] for k in ("identity_map_count", "record_count", "cik", "shares", "snapshots", "capex_inventory")}
    Path("data_cache/v1d_sec_audit.json").write_text(json.dumps(slim, sort_keys=True, default=str), encoding="utf-8")

if __name__ == "__main__":
    main()
