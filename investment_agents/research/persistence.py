"""Filesystem persistence for immutable, auditable Step 6C artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from investment_agents.ranking import RankingResult
from investment_agents.scanner import ScanResult
from investment_agents.path_safety import redact_secrets, safe_artifact_path, validate_run_id

from .queue import METHODOLOGY_VERSION, ResearchQueue, jsonable
from .integrity import create_run_manifest


def stable_cache_root() -> Path:
    """Return an absolute project-relative runtime cache root independent of CWD."""
    return Path(__file__).resolve().parents[2] / "data_cache"


def _write_new(path: Path, payload: Any) -> None:
    """Write JSON without silently overwriting an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(redact_secrets(jsonable(payload)), handle, ensure_ascii=True, indent=2, sort_keys=True)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite existing snapshot/report: {path}") from exc


def export_research_run(
    scan: ScanResult,
    ranking: RankingResult,
    queue: ResearchQueue,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Persist universe snapshot, scan, ranking, and queue under one run_id."""
    root = (root or stable_cache_root()).resolve()
    run_id = validate_run_id(scan.run_id)
    if queue.run_id != run_id:
        raise ValueError("ResearchQueue run_id does not match ScanResult run_id")
    if ranking.universe != scan.universe or queue.universe != scan.universe:
        raise ValueError("scan, ranking, and queue universes do not match")
    run_dir = safe_artifact_path(root, "research_runs", run_id)
    generated_at = datetime.now(timezone.utc)
    envelope = {
        "run_id": run_id,
        "generated_at": generated_at,
        "universe": scan.universe,
        "analysis_mode": scan.analysis_mode,
        "methodology_version": METHODOLOGY_VERSION,
        "universe_snapshot_reference": queue.universe_snapshot_reference,
    }
    snapshot_path = safe_artifact_path(root, "universe_snapshots", f"{scan.universe}_{run_id}.json")
    snapshot = {
        **envelope,
        "snapshot_type": "CURRENT_CONSTITUENT_SNAPSHOT",
        "pit_safe": False,
        "universe_name": scan.universe_metadata.get("name"),
        "source": scan.universe_metadata.get("source"),
        "fetched_at": scan.universe_metadata.get("fetched_at"),
        "source_date": scan.universe_metadata.get("source_date"),
        "constituent_count": len(scan.universe_constituents),
        "constituents": scan.universe_constituents,
    }
    _write_new(snapshot_path, snapshot)
    paths = {"universe_snapshot": str(snapshot_path)}
    for name, value in (("scan_result.json", scan), ("ranking_result.json", ranking), ("research_queue.json", queue)):
        path = run_dir / name
        _write_new(path, {**envelope, "payload": value})
        paths[name.removesuffix(".json")] = str(path)
    manifest_path, commit_path = create_run_manifest(
        root=root,
        run_id=run_id,
        methodology_version=METHODOLOGY_VERSION,
        artifacts=paths,
    )
    paths["manifest"] = str(manifest_path)
    paths["commit_marker"] = str(commit_path)
    return paths
