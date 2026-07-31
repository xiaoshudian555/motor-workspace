#!/usr/bin/env python3
"""Run-scoped local evidence directories and upstream references."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from mws_local_state import LOCAL_ROOT, ROOT, WorkspaceStateError, utc_now_iso
from mws_state import atomic_write_json, file_lock, load_json

RunKind = Literal[
    "workspace-ready",
    "machine-ready",
    "parity-complete",
    "deploy-environment-ready",
    "deploy-config-ready",
    "deploy-complete",
]

RUN_KINDS: frozenset[str] = frozenset(
    {
        "workspace-ready",
        "machine-ready",
        "parity-complete",
        "deploy-environment-ready",
        "deploy-config-ready",
        "deploy-complete",
    }
)

RUN_DIRS: dict[RunKind, str] = {
    "workspace-ready": "workspace-runs",
    "machine-ready": "machine-runs",
    "parity-complete": "parity-runs",
    "deploy-environment-ready": "environment-runs",
    "deploy-config-ready": "config-runs",
    "deploy-complete": "deploy-runs",
}

CONFIG_BUNDLES_DIR = LOCAL_ROOT / "config-bundles"
VALIDATION_RUNS_DIR = LOCAL_ROOT / "validation-runs"


def new_run_id(prefix: str) -> str:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def new_workflow_run_id() -> str:
    return new_run_id("workflow")


def run_dir_for_kind(kind: RunKind, run_id: str) -> Path:
    if kind not in RUN_KINDS:
        raise WorkspaceStateError(f"unknown run kind: {kind}")
    return LOCAL_ROOT / RUN_DIRS[kind] / run_id


def run_record_path(kind: RunKind, run_id: str) -> Path:
    return run_dir_for_kind(kind, run_id) / "run.json"


def parity_run_dir(run_id: str) -> Path:
    return run_dir_for_kind("parity-complete", run_id)


def deploy_run_dir(run_id: str) -> Path:
    return run_dir_for_kind("deploy-complete", run_id)


def validation_run_dir(run_id: str) -> Path:
    path = VALIDATION_RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_bundle_dir(config_fingerprint: str) -> Path:
    return CONFIG_BUNDLES_DIR / config_fingerprint


def _validate_upstream_ref(ref: Any) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise WorkspaceStateError("upstream ref must be an object")
    kind = str(ref.get("kind", "")).strip()
    run_id = str(ref.get("run_id", "")).strip()
    if kind not in RUN_KINDS:
        raise WorkspaceStateError(f"invalid upstream kind: {kind!r}")
    if not run_id:
        raise WorkspaceStateError("upstream run_id is required")
    return {"kind": kind, "run_id": run_id}


def load_run(kind: RunKind, run_id: str) -> dict[str, Any]:
    path = run_record_path(kind, run_id)
    if not path.exists():
        raise WorkspaceStateError(f"missing run record: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{path} must contain an object")
    record_kind = str(data.get("kind", kind))
    if record_kind != kind:
        raise WorkspaceStateError(
            f"run {run_id} kind mismatch: expected {kind}, got {record_kind}"
        )
    if str(data.get("run_id", run_id)) != run_id:
        raise WorkspaceStateError(f"run_id mismatch for {path}")
    if data.get("status") != "ready":
        raise WorkspaceStateError(f"run {run_id} is not ready")
    return data


def write_run(
    kind: RunKind,
    run_id: str,
    payload: dict[str, Any],
    *,
    immutable: bool = True,
) -> Path:
    if kind not in RUN_KINDS:
        raise WorkspaceStateError(f"unknown run kind: {kind}")
    path = run_record_path(kind, run_id)
    if immutable and path.exists():
        raise WorkspaceStateError(f"run already exists and is immutable: {path}")
    record = dict(payload)
    record["kind"] = kind
    record["run_id"] = run_id
    record.setdefault("created_at", utc_now_iso())
    run_dir_for_kind(kind, run_id).mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return path


def write_parity_run(run_id: str, payload: dict[str, Any]) -> Path:
    record = dict(payload)
    record.setdefault("kind", "parity-complete")
    if record.get("parity_complete") and record.get("status") in {"ok", "ready"}:
        record["status"] = "ready"
    path = write_run("parity-complete", run_id, record, immutable=True)
    if "manifest" in record:
        atomic_write_json(parity_run_dir(run_id) / "manifest.json", record["manifest"])
    return path


def write_deploy_run(run_id: str, payload: dict[str, Any]) -> Path:
    record = dict(payload)
    record.setdefault("kind", "deploy-complete")
    return write_run("deploy-complete", run_id, record, immutable=True)


def load_deploy_run(run_id: str, *, allow_failed: bool = True) -> dict[str, Any]:
    """Load a deploy run record.

    Deploy runs record both ready and failed outcomes; status/restart/stop/diagnosis
    legitimately need to read failed runs to gather evidence. Unlike ``load_run`` this
    does not require status == "ready", but still enforces kind/run_id integrity.
    """
    path = deploy_run_dir(run_id) / "run.json"
    if not path.exists():
        raise WorkspaceStateError(f"missing run record: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{path} must contain an object")
    record_kind = str(data.get("kind", "deploy-complete"))
    if record_kind != "deploy-complete":
        raise WorkspaceStateError(
            f"run {run_id} kind mismatch: expected deploy-complete, got {record_kind}"
        )
    if str(data.get("run_id", run_id)) != run_id:
        raise WorkspaceStateError(f"run_id mismatch for {path}")
    if not allow_failed and data.get("status") != "ready":
        raise WorkspaceStateError(f"run {run_id} is not ready")
    return data


def validate_upstream_refs(
    refs: list[dict[str, Any]],
    *,
    expected: dict[str, RunKind] | None = None,
    workflow_run_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ref in refs:
        item = _validate_upstream_ref(ref)
        record = load_run(item["kind"], item["run_id"])  # type: ignore[arg-type]
        if workflow_run_id and record.get("workflow_run_id") not in {None, workflow_run_id}:
            raise WorkspaceStateError(
                f"upstream {item['kind']}:{item['run_id']} belongs to another workflow"
            )
        if expected and item["kind"] in expected.values():
            pass
        normalized.append(item)
    if expected:
        present = {item["kind"] for item in normalized}
        for label, kind in expected.items():
            if kind not in present:
                raise WorkspaceStateError(f"missing required upstream {label}: {kind}")
    return normalized


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(encoded)


def bundle_digest_for_files(paths: dict[str, Path]) -> str:
    items: list[tuple[str, str]] = []
    for name in sorted(paths):
        path = paths[name]
        if not path.exists():
            raise WorkspaceStateError(f"bundle file missing: {path}")
        items.append((name, digest_bytes(path.read_bytes())))
    return digest_json(items)


def create_config_bundle(
    *,
    config_fingerprint: str,
    bundle_files: dict[str, Path],
    metadata: dict[str, Any],
    expected_bundle_digest: str | None = None,
) -> dict[str, Any]:
    if not config_fingerprint.strip():
        raise WorkspaceStateError("config_fingerprint is required")
    bundle_root = config_bundle_dir(config_fingerprint)
    lock_path = bundle_root.with_suffix(".lock")
    with file_lock(lock_path):
        bundle_root.mkdir(parents=True, exist_ok=True)
        manifest_path = bundle_root / "bundle.json"
        staged_paths = {name: bundle_root / name for name in bundle_files}

        if manifest_path.exists():
            existing = load_json(manifest_path, default={})
            existing_digest = str(existing.get("bundle_digest", ""))
            if expected_bundle_digest and expected_bundle_digest != existing_digest:
                raise WorkspaceStateError("bundle_digest mismatch for fingerprint")
            current_digest = bundle_digest_for_files(staged_paths)
            if current_digest != existing_digest:
                raise WorkspaceStateError(
                    "config bundle content was modified or fingerprint collision detected"
                )
            return {
                "config_fingerprint": config_fingerprint,
                "bundle_digest": existing_digest,
                "bundle_dir": _repo_relative(bundle_root),
            }

        for name, src in bundle_files.items():
            dest = staged_paths[name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        digest = bundle_digest_for_files(staged_paths)
        payload = {
            "config_fingerprint": config_fingerprint,
            "bundle_digest": digest,
            "created_at": utc_now_iso(),
            **metadata,
        }
        atomic_write_json(manifest_path, payload)
        return {
            "config_fingerprint": config_fingerprint,
            "bundle_digest": digest,
            "bundle_dir": _repo_relative(bundle_root),
        }


def relative_repo(path: Path) -> str:
    return _repo_relative(path)


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
