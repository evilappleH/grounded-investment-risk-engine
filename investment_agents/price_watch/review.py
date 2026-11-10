from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from investment_agents.committee import build_agent_decision, build_committee_decision, compile_claims
from investment_agents.llm import InvestorAgentRuntime, OpenAIProvider

from .models import WatchEvent


class TriggeredResearchReviewer:
    """Runs the existing research agents; it never imports an execution adapter."""

    def __init__(self, root: Path, *, model: str | None = None) -> None:
        self.root = root
        self.model = model

    def _latest_package(self, ticker: str) -> tuple[str, dict] | None:
        candidates = sorted((self.root / "data_cache" / "research_runs").glob("*/research_queue.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            for entry in envelope.get("payload", {}).get("entries", []):
                if entry.get("ticker") == ticker:
                    return str(envelope["run_id"]), entry["research_package"]
        return None

    def review(self, event: WatchEvent) -> dict:
        base = {"event": asdict(event), "reviewed_at": datetime.now(timezone.utc).isoformat(), "execution": "NO_ORDER"}
        located = self._latest_package(event.ticker)
        if located is None:
            return {**base, "status": "INSUFFICIENT_CURRENT_EVIDENCE", "reason_codes": ["RESEARCH_PACKAGE_NOT_FOUND"]}
        run_id, package = located
        package = dict(package)
        package["user_price_review"] = {
            "event_id": event.event_id,
            "side": event.side.value,
            "trigger_price": event.price,
            "quoted_at": event.quoted_at.isoformat(),
            "user_rationale": event.user_rationale,
            "boundary": "This is review context only. Do not output a trade or order.",
        }
        provider = OpenAIProvider()
        if not provider.is_configured:
            return {**base, "status": "REVIEW_NOT_RUN", "reason_codes": ["LLM_PROVIDER_NOT_CONFIGURED"], "source_run_id": run_id}
        try:
            runtime = InvestorAgentRuntime(provider, model=self.model, temperature=0.0)
            buffett_result = runtime.analyze(package, "buffett", source_run_id=run_id)
            munger_result = runtime.analyze(package, "munger", source_run_id=run_id)
            claims = compile_claims(package, run_id=run_id)
            buffett = build_agent_decision(buffett_result, claims, run_id=run_id)
            munger = build_agent_decision(munger_result, claims, run_id=run_id)
            committee = build_committee_decision(buffett, munger, claims)
            status = "PRICE_REACHED_REVIEW_COMPLETED" if committee.evidence_sufficiency == "SUFFICIENT" else "INSUFFICIENT_CURRENT_EVIDENCE"
            stances = {buffett.stance, munger.stance}
            if status != "PRICE_REACHED_REVIEW_COMPLETED" or committee.committee_status != "AGREEMENT":
                assessment = "MORE_RESEARCH_REQUIRED"
            elif event.side.value == "BUY_REVIEW":
                assessment = "PRICE_POINT_SUPPORTIVE" if stances == {"ATTRACTIVE_FOR_FURTHER_RESEARCH"} else "PRICE_POINT_NOT_CONFIRMED"
            else:
                assessment = "SELL_REVIEW_SUPPORTIVE" if stances == {"UNATTRACTIVE_FOR_THIS_STYLE"} else "SELL_REVIEW_NOT_CONFIRMED"
            return {**base, "status": status, "price_point_assessment": assessment, "source_run_id": run_id, "buffett": asdict(buffett), "munger": asdict(munger), "committee": asdict(committee)}
        except Exception as exc:
            return {**base, "status": "REVIEW_FAILED", "reason_codes": [type(exc).__name__], "source_run_id": run_id}


def persist_review(result: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    event_id = result["event"]["event_id"]
    path = directory / f"{event_id}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, default=str, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)
    return path
