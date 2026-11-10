"""Immutable run manifests and downstream verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping

from investment_agents.path_safety import safe_artifact_path, validate_run_id


MANIFEST_SCHEMA_VERSION = "research-run-manifest-v1"
COMMIT_MARKER = "RESEARCH_RUN_COMMITTED"


@dataclass(frozen=True)
class VerifiedRunManifest:
    run_id: str
    methodology_version: str
    manifest_hash: str
    created_at: str
    artifact_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def create_run_manifest(
    *,
    root: Path,
    run_id: str,
    methodology_version: str,
    artifacts: Mapping[str, str | Path],
) -> tuple[Path, Path]:
    """Commit a SHA-256 manifest after all material run artifacts exist."""
    root = root.resolve()
    run_id = validate_run_id(run_id)
    entries: dict[str, dict[str, object]] = {}
    for name, candidate in sorted(artifacts.items()):
        path = Path(candidate).resolve()
        if root != path and root not in path.parents:
            raise ValueError("manifest artifact escapes configured root")
        if not path.is_file():
            raise FileNotFoundError(path)
        digest, size = _file_digest(path)
        entries[name] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": digest,
            "size_bytes": size,
        }
    created_at = datetime.now(timezone.utc).isoformat()
    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "methodology_version": methodology_version,
        "created_at": created_at,
        "completion_marker": COMMIT_MARKER,
        "artifacts": entries,
    }
    manifest_hash = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    manifest = {**body, "manifest_hash": manifest_hash}
    run_dir = safe_artifact_path(root, "research_runs", run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    commit_path = run_dir / "COMMITTED.json"
    try:
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=True, sort_keys=True, indent=2)
        with commit_path.open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "run_id": run_id,
                    "manifest_hash": manifest_hash,
                    "completion_marker": COMMIT_MARKER,
                    "created_at": created_at,
                },
                handle,
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
    except FileExistsError as exc:
        raise FileExistsError("refusing to overwrite run manifest/commit marker") from exc
    return manifest_path, commit_path


def verify_run_manifest(*, root: Path, run_id: str) -> VerifiedRunManifest:
    """Verify commit marker, manifest identity, and every artifact digest."""
    root = root.resolve()
    run_id = validate_run_id(run_id)
    run_dir = safe_artifact_path(root, "research_runs", run_id)
    manifest_path = run_dir / "manifest.json"
    commit_path = run_dir / "COMMITTED.json"
    if not manifest_path.is_file() or not commit_path.is_file():
        raise ValueError("research run is not committed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(commit, dict):
        raise ValueError("invalid run integrity metadata")
    manifest_hash = manifest.get("manifest_hash")
    body = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    expected_hash = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if not isinstance(manifest_hash, str) or manifest_hash != expected_hash:
        raise ValueError("run manifest hash mismatch")
    if (
        manifest.get("run_id") != run_id
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("completion_marker") != COMMIT_MARKER
        or commit.get("run_id") != run_id
        or commit.get("manifest_hash") != manifest_hash
        or commit.get("completion_marker") != COMMIT_MARKER
    ):
        raise ValueError("run commit identity mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("run manifest has no material artifacts")
    for entry in artifacts.values():
        if not isinstance(entry, dict):
            raise ValueError("invalid manifest artifact entry")
        path = safe_artifact_path(root, *str(entry.get("path", "")).split("/"))
        if not path.is_file():
            raise ValueError("manifest artifact is missing")
        digest, size = _file_digest(path)
        if digest != entry.get("sha256") or size != entry.get("size_bytes"):
            raise ValueError("manifest artifact hash mismatch")
    return VerifiedRunManifest(
        run_id=run_id,
        methodology_version=str(manifest.get("methodology_version", "")),
        manifest_hash=manifest_hash,
        created_at=str(manifest.get("created_at", "")),
        artifact_count=len(artifacts),
    )
