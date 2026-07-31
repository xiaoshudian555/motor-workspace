#!/usr/bin/env python3
"""Remote-code-parity helpers: sync local dirty tree to machine fixed directories."""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from mws_local_state import LOCAL_ROOT, WorkspaceStateError, get_machine, utc_now_iso
from mws_machine_target import (
    MACHINE_READY_REQUIRED_CHECKS,
    build_fixed_source_paths,
    endpoint_matches_machine,
    machine_identity_matches,
    machine_ref,
)
from mws_result import RESULT_SCHEMA_VERSION
from mws_result import progress
from mws_state import atomic_write_json, file_lock, load_json
from mws_transport import RemoteTransport, shell_quote, transport_for_machine, validate_machine_transport_fields
from repo_paths import MOTOR_ROOT, REPO_ROOT, VLLM_ASCEND_ROOT, VLLM_ROOT

REPO_DIRS = {
    "motor": MOTOR_ROOT,
    "vllm": VLLM_ROOT,
    "vllm_ascend": VLLM_ASCEND_ROOT,
}
REPO_REMOTE_KEYS = {
    "motor": "motor_source",
    "vllm": "vllm_source",
    "vllm_ascend": "vllm_ascend_source",
}
OVERLAY_ROOT = LOCAL_ROOT / "python-overlay"
PARITY_STATE_DIR = LOCAL_ROOT / "parity-state"
MACHINE_RUNS_DIR = LOCAL_ROOT / "machine-runs"
REMOTE_LOCK_DIRNAME = ".parity-sync.lock"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(args: list[str], path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def list_repo_members(repo_path: Path) -> list[str]:
    if not repo_path.exists():
        raise WorkspaceStateError(f"repository path missing: {repo_path}")
    ls = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], repo_path)
    if ls.returncode:
        raise WorkspaceStateError(f"git ls-files failed for {repo_path}: {ls.stderr.strip()}")
    return [item for item in ls.stdout.split("\0") if item]


def file_hashes_for_members(repo_path: Path, members: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in members:
        absolute = repo_path / relative
        if absolute.is_file():
            hashes[relative] = _sha256_bytes(absolute.read_bytes())
    return hashes


def aggregate_content_digest(file_hashes: dict[str, str]) -> str:
    lines = [f"{path}\0{digest}\n" for path, digest in sorted(file_hashes.items())]
    return _sha256_bytes("".join(lines).encode())


def compute_repo_content_digest(repo_path: Path) -> dict[str, Any]:
    members = list_repo_members(repo_path)
    file_hashes = file_hashes_for_members(repo_path, members)
    return {
        "file_count": len(file_hashes),
        "file_hashes": file_hashes,
        "content_digest": aggregate_content_digest(file_hashes),
    }


def compute_overlay_content_digest() -> dict[str, Any]:
    if not OVERLAY_ROOT.exists():
        return {"file_count": 0, "file_hashes": {}, "content_digest": aggregate_content_digest({})}
    file_hashes: dict[str, str] = {}
    for path in sorted(OVERLAY_ROOT.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(OVERLAY_ROOT))
            file_hashes[rel] = _sha256_bytes(path.read_bytes())
    return {
        "file_count": len(file_hashes),
        "file_hashes": file_hashes,
        "content_digest": aggregate_content_digest(file_hashes),
    }


def _repo_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def repo_manifest(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkspaceStateError(f"{name}: submodule not initialized at {path}")
    head = _git(["rev-parse", "HEAD"], path)
    if head.returncode:
        raise WorkspaceStateError(f"{name}: cannot resolve HEAD")
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"], path)
    tracked_diff = _git(["diff", "--binary", "HEAD"], path)
    content = compute_repo_content_digest(path)
    return {
        "name": name,
        "path": _repo_relative_path(path),
        "commit": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_sha256": _sha256_bytes(status.stdout.encode()),
        "tracked_diff_sha256": _sha256_bytes(tracked_diff.stdout.encode()),
        "file_count": content["file_count"],
        "content_digest": content["content_digest"],
        "untracked_files": {
            rel: digest
            for rel, digest in content["file_hashes"].items()
            if _git(["ls-files", "--error-unmatch", rel], path).returncode != 0
        },
    }


def overlay_manifest() -> dict[str, str]:
    return compute_overlay_content_digest()["file_hashes"]


def build_source_manifest(machine: dict[str, Any]) -> dict[str, Any]:
    paths = build_fixed_source_paths(machine)
    repos = [repo_manifest(name, path) for name, path in REPO_DIRS.items()]
    overlay = compute_overlay_content_digest()
    repo_digests = {repo["name"]: repo["content_digest"] for repo in repos}
    local_bundle = {
        **repo_digests,
        "python_overlay": overlay["content_digest"],
    }
    return {
        "schema_version": 2,
        "created_at": utc_now_iso(),
        "machine": machine_ref(machine),
        "mount_root": paths["mount_root"],
        "remote_workspace_root": paths["remote_workspace_root"],
        "source_dirs": {
            "motor": paths["motor_source"],
            "vllm": paths["vllm_source"],
            "vllm_ascend": paths["vllm_ascend_source"],
            "python_overlay": paths["python_overlay"],
        },
        "repositories": repos,
        "python_overlay_hashes": overlay["file_hashes"],
        "python_overlay_digest": overlay["content_digest"],
        "local_content_digest": aggregate_content_digest(
            {name: digest for name, digest in local_bundle.items()}
        ),
        "local_content_digests": local_bundle,
    }


def parity_state_path(machine_alias: str) -> Path:
    return PARITY_STATE_DIR / f"{machine_alias}.json"


def load_parity_state(machine_alias: str) -> dict[str, Any] | None:
    path = parity_state_path(machine_alias)
    if not path.exists():
        return None
    data = load_json(path, default=None)
    return data if isinstance(data, dict) else None


def save_parity_state(machine_alias: str, payload: dict[str, Any]) -> Path:
    path = parity_state_path(machine_alias)
    atomic_write_json(path, payload)
    return path


def local_parity_lock_path(machine_alias: str) -> Path:
    return LOCAL_ROOT / f"parity-sync-{machine_alias}.lock"


def remote_lock_path(machine: dict[str, Any]) -> str:
    paths = build_fixed_source_paths(machine)
    return f"{paths['remote_workspace_root']}/{REMOTE_LOCK_DIRNAME}"


def remote_directory_file_hashes(transport: RemoteTransport, remote_dir: str) -> dict[str, str]:
    return transport.directory_file_hashes(remote_dir)


def verify_remote_content(
    transport: RemoteTransport,
    *,
    repo_name: str,
    remote_dir: str,
    expected_hashes: dict[str, str],
) -> dict[str, Any]:
    observed = remote_directory_file_hashes(transport, remote_dir)
    missing = sorted(set(expected_hashes) - set(observed))
    extra = sorted(set(observed) - set(expected_hashes))
    mismatched = sorted(
        rel for rel in expected_hashes if rel in observed and observed[rel] != expected_hashes[rel]
    )
    ok = not missing and not extra and not mismatched
    return {
        "name": repo_name,
        "remote_dir": remote_dir,
        "content_digest": aggregate_content_digest(observed),
        "file_count": len(observed),
        "verified": ok,
        "missing_files": missing,
        "extra_files": extra,
        "mismatched_files": mismatched,
    }


def load_machine_ready_evidence(
    machine_alias: str,
    *,
    machine_run_id: str | None = None,
) -> dict[str, Any]:
    if not machine_run_id or not str(machine_run_id).strip():
        raise WorkspaceStateError(
            f"machine_run_id is required to load machine-ready evidence for {machine_alias!r}"
        )
    run_id = str(machine_run_id).strip()
    run_path = MACHINE_RUNS_DIR / run_id / "run.json"
    if not run_path.exists():
        raise WorkspaceStateError(
            f"machine-ready run not found: {run_id} ({run_path})"
        )
    data = load_json(run_path, default={})
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"invalid machine run record: {run_path}")
    return _validate_machine_ready_record(data, machine_alias, run_id)


def _validate_machine_ready_record(
    record: dict[str, Any],
    machine_alias: str,
    run_id: str,
) -> dict[str, Any]:
    schema_version = str(record.get("schema_version", ""))
    if schema_version != RESULT_SCHEMA_VERSION:
        raise WorkspaceStateError(
            f"machine-ready run {run_id} has unsupported schema_version: {schema_version!r}"
        )
    kind = str(record.get("kind", ""))
    if kind != "machine-ready":
        raise WorkspaceStateError(
            f"machine-ready run {run_id} kind mismatch: expected machine-ready, got {kind!r}"
        )
    if str(record.get("run_id", run_id)) != run_id:
        raise WorkspaceStateError(f"machine-ready run {run_id} run_id mismatch")
    status = str(record.get("status", ""))
    if status != "ready":
        raise WorkspaceStateError(f"machine-ready run {run_id} is not ready (status={status!r})")

    alias = str(record.get("alias") or record.get("machine") or "")
    if alias != machine_alias:
        raise WorkspaceStateError(
            f"machine-ready run {run_id} is for {alias!r}, not {machine_alias!r}"
        )

    machine = get_machine(machine_alias)
    ref = record.get("machine_ref")
    endpoint = record.get("endpoint")
    if not isinstance(ref, dict) or not isinstance(endpoint, dict):
        raise WorkspaceStateError(
            f"machine-ready run {run_id} missing machine_ref/endpoint evidence"
        )
    if not machine_identity_matches(machine, ref):
        raise WorkspaceStateError(
            f"machine-ready run {run_id} machine_ref does not match inventory for {machine_alias!r}"
        )
    if not endpoint_matches_machine(machine, endpoint):
        raise WorkspaceStateError(
            f"machine-ready run {run_id} endpoint does not match inventory for {machine_alias!r}"
        )

    checks = record.get("checks")
    if not isinstance(checks, list):
        raise WorkspaceStateError(f"machine-ready run {run_id} missing checks list")

    seen_names: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            raise WorkspaceStateError(f"machine-ready run {run_id} has invalid check entry")
        name = str(item.get("name", "")).strip()
        if not name:
            raise WorkspaceStateError(f"machine-ready run {run_id} has unnamed check")
        seen_names.add(name)
        check_status = str(item.get("status", ""))
        if check_status not in {"ok", "warning"}:
            raise WorkspaceStateError(
                f"machine-ready run {run_id} check {name!r} has invalid status {check_status!r}"
            )

    missing = sorted(MACHINE_READY_REQUIRED_CHECKS - seen_names)
    if missing:
        raise WorkspaceStateError(
            f"machine-ready run {run_id} missing required checks: {', '.join(missing)}"
        )

    return {
        "machine_run_id": run_id,
        "workflow_run_id": record.get("workflow_run_id"),
        "alias": machine_alias,
        "machine_ref": ref,
        "endpoint": endpoint,
        "checks": checks,
        "verified_at": record.get("finished_at") or record.get("created_at"),
    }


def create_repo_tarball(repo_path: Path) -> bytes:
    members = list_repo_members(repo_path)
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative in members:
            absolute = repo_path / relative
            if absolute.is_file():
                archive.add(absolute, arcname=relative)
    return buffer.getvalue()


def create_overlay_tarball() -> bytes | None:
    overlay = compute_overlay_content_digest()
    if not overlay["file_hashes"]:
        return None
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative in sorted(overlay["file_hashes"]):
            path = OVERLAY_ROOT / relative
            if path.is_file():
                archive.add(path, arcname=relative)
    return buffer.getvalue()


def _publish_tarball_to_remote(
    transport: RemoteTransport,
    *,
    repo_name: str,
    remote_dir: str,
    tarball: bytes | None,
    empty_ok: bool = False,
) -> dict[str, Any]:
    if tarball is None:
        if not empty_ok:
            raise WorkspaceStateError(f"no tarball content for {repo_name}")
        transport.run(f"rm -rf {shell_quote(remote_dir)}")
        transport.mkdir(remote_dir)
        return {"name": repo_name, "remote_dir": remote_dir, "tarball_sha256": None}

    digest = _sha256_bytes(tarball)[:12]
    remote_archive = f"/tmp/mws-parity-{repo_name}-{digest}.tar.gz"
    staging = f"{remote_dir}.staging"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as handle:
        handle.write(tarball)
        local_archive = handle.name
    try:
        transport.upload_file(local_archive, remote_archive)
    finally:
        Path(local_archive).unlink(missing_ok=True)

    transport.run(f"rm -rf {shell_quote(staging)}")
    transport.mkdir(staging)
    extract = (
        f"tar -xzf {shell_quote(remote_archive)} -C {shell_quote(staging)} && "
        f"rm -f {shell_quote(remote_archive)}"
    )
    result = transport.run(extract)
    if result.returncode:
        transport.run(f"rm -rf {shell_quote(staging)}")
        raise WorkspaceStateError(
            f"extract failed for {repo_name}: {result.stderr.strip() or result.stdout.strip()}"
        )

    switch = (
        f"rm -rf {shell_quote(remote_dir)} && "
        f"mv {shell_quote(staging)} {shell_quote(remote_dir)}"
    )
    switch_result = transport.run(switch)
    if switch_result.returncode:
        transport.run(f"rm -rf {shell_quote(staging)}")
        raise WorkspaceStateError(
            f"publish failed for {repo_name}: {switch_result.stderr.strip() or switch_result.stdout.strip()}"
        )
    return {
        "name": repo_name,
        "remote_dir": remote_dir,
        "tarball_sha256": _sha256_bytes(tarball),
    }


def _expected_repo_hashes(repo_name: str, repo_path: Path) -> dict[str, str]:
    if repo_name == "python-overlay":
        return compute_overlay_content_digest()["file_hashes"]
    return compute_repo_content_digest(repo_path)["file_hashes"]


def _try_no_change_fast_path(
    machine: dict[str, Any],
    transport: RemoteTransport,
    manifest: dict[str, Any],
    *,
    machine_alias: str,
) -> dict[str, Any] | None:
    prior = load_parity_state(machine_alias)
    if not prior or prior.get("local_content_digest") != manifest["local_content_digest"]:
        return None

    paths = build_fixed_source_paths(machine)
    remote_digests: dict[str, str] = {}
    proof: list[dict[str, Any]] = []
    for name, repo_path in REPO_DIRS.items():
        remote_dir = paths[REPO_REMOTE_KEYS[name]]
        expected = _expected_repo_hashes(name, repo_path)
        item = verify_remote_content(
            transport,
            repo_name=name,
            remote_dir=remote_dir,
            expected_hashes=expected,
        )
        proof.append(item)
        if not item["verified"]:
            return None
        remote_digests[name] = item["content_digest"]

    overlay_dir = paths["python_overlay"]
    overlay_expected = _expected_repo_hashes("python-overlay", OVERLAY_ROOT)
    overlay_proof = verify_remote_content(
        transport,
        repo_name="python-overlay",
        remote_dir=overlay_dir,
        expected_hashes=overlay_expected,
    )
    proof.append(overlay_proof)
    if not overlay_proof["verified"]:
        return None
    remote_digests["python_overlay"] = overlay_proof["content_digest"]

    manifest = dict(manifest)
    manifest["sync_mode"] = "no-change-fast-path"
    manifest["remote_content_digests"] = remote_digests
    manifest["remote_content_digest"] = aggregate_content_digest(
        {key: digest for key, digest in remote_digests.items()}
    )
    manifest["remote_proof"] = proof
    manifest["machine_ready"] = prior.get("machine_ready")
    manifest["parity_state_ref"] = str(parity_state_path(machine_alias))
    manifest["pythonpath"] = ":".join(
        [
            paths["motor_source"],
            paths["vllm_source"],
            paths["vllm_ascend_source"],
            paths["python_overlay"],
        ]
    )
    manifest["target"] = machine.get("host")
    manifest["status"] = "ok"
    manifest["completed_at"] = utc_now_iso()
    return manifest


def fanout_nodes(machine: dict[str, Any], nodes: list[str]) -> list[str]:
    backend = machine.get("parity_backend", "shared-hostpath")
    if backend == "shared-hostpath":
        return [machine["host"]]
    if backend == "node-local-hostpath":
        raise WorkspaceStateError(
            "node-local-hostpath is not supported yet; use shared-hostpath with a shared /mnt"
        )
    raise WorkspaceStateError(f"unsupported parity_backend: {backend}")


def sync_workspace_to_remote(
    machine: dict[str, Any],
    *,
    transport: RemoteTransport | None = None,
    fake_root: Path | None = None,
    machine_ready: dict[str, Any] | None = None,
    skip_fast_path: bool = False,
) -> dict[str, Any]:
    validate_machine_transport_fields(machine)
    if machine_ready is None:
        machine_alias = str(machine.get("alias") or machine.get("host"))
        raise WorkspaceStateError(
            f"machine-ready evidence required for parity sync on {machine_alias!r}; "
            "run machine-management verify first"
        )
    machine_alias = str(machine.get("alias") or machine.get("host"))
    paths = build_fixed_source_paths(machine)
    manifest = build_source_manifest(machine)
    tx = transport or transport_for_machine(machine, fake_root=fake_root)
    lock_path = remote_lock_path(machine)

    if machine_ready:
        manifest["machine_ready"] = machine_ready

    progress(f"sync target host={machine.get('host')}")

    with file_lock(local_parity_lock_path(machine_alias)):
        tx.acquire_parity_lock(lock_path)
        try:
            if not skip_fast_path:
                fast = _try_no_change_fast_path(
                    machine, tx, manifest, machine_alias=machine_alias
                )
                if fast is not None:
                    progress("no local or remote changes detected; fast path")
                    return fast

            synced: list[dict[str, Any]] = []
            completed_repos: list[str] = []
            try:
                for name, repo_path in REPO_DIRS.items():
                    remote_dir = paths[REPO_REMOTE_KEYS[name]]
                    tarball = create_repo_tarball(repo_path)
                    progress(f"syncing {name} to {remote_dir}")
                    synced.append(
                        _publish_tarball_to_remote(
                            tx,
                            repo_name=name,
                            remote_dir=remote_dir,
                            tarball=tarball,
                        )
                    )
                    completed_repos.append(name)

                overlay_dir = paths["python_overlay"]
                overlay_tarball = create_overlay_tarball()
                progress(f"syncing python-overlay to {overlay_dir}")
                synced.append(
                    _publish_tarball_to_remote(
                        tx,
                        repo_name="python-overlay",
                        remote_dir=overlay_dir,
                        tarball=overlay_tarball,
                        empty_ok=True,
                    )
                )
                completed_repos.append("python-overlay")
            except Exception:
                for name in completed_repos:
                    key = REPO_REMOTE_KEYS.get(name)
                    remote_dir = paths[key] if key else paths.get("python_overlay")
                    if remote_dir:
                        tx.run(f"rm -rf {shell_quote(remote_dir)}.staging")
                raise

            proof: list[dict[str, Any]] = []
            remote_digests: dict[str, str] = {}
            for name, repo_path in REPO_DIRS.items():
                remote_dir = paths[REPO_REMOTE_KEYS[name]]
                expected = _expected_repo_hashes(name, repo_path)
                item = verify_remote_content(
                    tx,
                    repo_name=name,
                    remote_dir=remote_dir,
                    expected_hashes=expected,
                )
                proof.append(item)
                if not item["verified"]:
                    raise WorkspaceStateError(
                        f"remote proof failed for {name}: "
                        f"missing={item['missing_files'][:5]} "
                        f"extra={item['extra_files'][:5]} "
                        f"mismatched={item['mismatched_files'][:5]}"
                    )
                remote_digests[name] = item["content_digest"]

            overlay_dir = paths["python_overlay"]
            overlay_expected = _expected_repo_hashes("python-overlay", OVERLAY_ROOT)
            overlay_proof = verify_remote_content(
                tx,
                repo_name="python-overlay",
                remote_dir=overlay_dir,
                expected_hashes=overlay_expected,
            )
            proof.append(overlay_proof)
            if not overlay_proof["verified"]:
                raise WorkspaceStateError("remote proof failed for python-overlay")
            remote_digests["python_overlay"] = overlay_proof["content_digest"]

            manifest["synced"] = synced
            manifest["remote_proof"] = proof
            manifest["remote_content_digests"] = remote_digests
            manifest["remote_content_digest"] = aggregate_content_digest(
                {key: digest for key, digest in remote_digests.items()}
            )
            manifest["sync_mode"] = "full-sync"
            manifest["pythonpath"] = ":".join(
                [
                    paths["motor_source"],
                    paths["vllm_source"],
                    paths["vllm_ascend_source"],
                    paths["python_overlay"],
                ]
            )
            manifest["target"] = machine.get("host")
            manifest["status"] = "ok"
            manifest["completed_at"] = utc_now_iso()

            save_parity_state(
                machine_alias,
                {
                    "machine_alias": machine_alias,
                    "updated_at": manifest["completed_at"],
                    "local_content_digest": manifest["local_content_digest"],
                    "local_content_digests": manifest["local_content_digests"],
                    "remote_content_digest": manifest["remote_content_digest"],
                    "remote_content_digests": remote_digests,
                    "machine_ready": machine_ready,
                    "source_dirs": manifest["source_dirs"],
                },
            )
            return manifest
        finally:
            tx.release_parity_lock(lock_path)


def sync_workspace_fanout(
    machine: dict[str, Any],
    *,
    fake_roots: dict[str, Path] | None = None,
    machine_ready: dict[str, Any] | None = None,
    skip_fast_path: bool = False,
) -> dict[str, Any]:
    nodes = fanout_nodes(machine, machine.get("candidate_nodes", []))
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    for node in nodes:
        fake_root = (fake_roots or {}).get(node)
        try:
            manifest = sync_workspace_to_remote(
                machine,
                fake_root=fake_root,
                machine_ready=machine_ready,
                skip_fast_path=skip_fast_path,
            )
            targets.append({"node": node, "status": "ok", "manifest": manifest})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{node}: {exc}")
            targets.append({"node": node, "status": "error", "error": str(exc)})
    overall: dict[str, Any] = {
        "schema_version": 2,
        "status": "error" if errors else "ok",
        "errors": errors,
        "targets": targets,
    }
    if targets and targets[0]["status"] == "ok":
        overall.update(targets[0]["manifest"])
    return overall
