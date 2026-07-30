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

from mws_local_state import LOCAL_ROOT, WorkspaceStateError, utc_now_iso
from repo_paths import MOTOR_ROOT, REPO_ROOT, VLLM_ASCEND_ROOT, VLLM_ROOT
from mws_machine_target import build_fixed_source_paths, machine_ref
from mws_result import progress
from mws_transport import FakeRemoteTransport, RemoteTransport, shell_quote, transport_for_machine, validate_machine_transport_fields

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


def repo_manifest(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkspaceStateError(f"{name}: submodule not initialized at {path}")
    head = _git(["rev-parse", "HEAD"], path)
    if head.returncode:
        raise WorkspaceStateError(f"{name}: cannot resolve HEAD")
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"], path)
    tracked_diff = _git(["diff", "--binary", "HEAD"], path)
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z"], path)
    untracked_names = [item for item in untracked.stdout.split("\0") if item]
    untracked_hashes: dict[str, str] = {}
    for relative in untracked_names:
        candidate = path / relative
        if candidate.is_file():
            untracked_hashes[relative] = _sha256_bytes(candidate.read_bytes())
    return {
        "name": name,
        "path": str(path.relative_to(REPO_ROOT)),
        "commit": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_sha256": _sha256_bytes(status.stdout.encode()),
        "tracked_diff_sha256": _sha256_bytes(tracked_diff.stdout.encode()),
        "untracked_files": untracked_hashes,
    }


def overlay_manifest() -> dict[str, str]:
    if not OVERLAY_ROOT.exists():
        return {}
    hashes: dict[str, str] = {}
    for path in sorted(OVERLAY_ROOT.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(OVERLAY_ROOT))
            hashes[rel] = _sha256_bytes(path.read_bytes())
    return hashes


def build_source_manifest(machine: dict[str, Any]) -> dict[str, Any]:
    paths = build_fixed_source_paths(machine)
    repos = [repo_manifest(name, path) for name, path in REPO_DIRS.items()]
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
        "python_overlay_hashes": overlay_manifest(),
    }


def create_repo_tarball(repo_path: Path) -> bytes:
    if not repo_path.exists():
        raise WorkspaceStateError(f"repository path missing: {repo_path}")
    ls = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], repo_path)
    if ls.returncode:
        raise WorkspaceStateError(f"git ls-files failed for {repo_path}: {ls.stderr.strip()}")
    members = [item for item in ls.stdout.split("\0") if item]
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative in members:
            absolute = repo_path / relative
            if absolute.is_file():
                archive.add(absolute, arcname=relative)
    return buffer.getvalue()


def create_overlay_tarball() -> bytes | None:
    if not OVERLAY_ROOT.exists() or not any(OVERLAY_ROOT.rglob("*")):
        return None
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(OVERLAY_ROOT.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(OVERLAY_ROOT)))
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
) -> dict[str, Any]:
    validate_machine_transport_fields(machine)
    paths = build_fixed_source_paths(machine)
    manifest = build_source_manifest(machine)
    tx = transport or transport_for_machine(machine, fake_root=fake_root)
    progress(f"sync target host={machine.get('host')}")

    synced: list[dict[str, Any]] = []
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

    manifest["synced"] = synced
    manifest["pythonpath"] = ":".join(
        [
            paths["motor_source"],
            paths["vllm_source"],
            paths["vllm_ascend_source"],
            paths["python_overlay"],
        ]
    )
    manifest["target"] = machine.get("host")
    return manifest


def sync_workspace_fanout(
    machine: dict[str, Any],
    *,
    fake_roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    nodes = fanout_nodes(machine, machine.get("candidate_nodes", []))
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    for node in nodes:
        fake_root = (fake_roots or {}).get(node)
        try:
            manifest = sync_workspace_to_remote(machine, fake_root=fake_root)
            targets.append({"node": node, "status": "ok", "manifest": manifest})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{node}: {exc}")
            targets.append({"node": node, "status": "error", "error": str(exc)})
    overall = {
        "schema_version": 2,
        "status": "error" if errors else "ok",
        "errors": errors,
        "targets": targets,
    }
    if targets and targets[0]["status"] == "ok":
        overall.update(targets[0]["manifest"])
    return overall
