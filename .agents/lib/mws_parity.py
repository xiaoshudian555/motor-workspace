#!/usr/bin/env python3
"""Remote-code-parity helpers: sync local dirty tree to shared mount root."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from mws_local_state import ROOT, WorkspaceStateError, utc_now_iso
from mws_result import progress

REPO_DIRS = {
    "motor": ROOT / "motor",
    "vllm": ROOT / "vllm",
    "vllm_ascend": ROOT / "vllm-ascend",
}


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
        "path": str(path.relative_to(ROOT)),
        "commit": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_sha256": _sha256_bytes(status.stdout.encode()),
        "tracked_diff_sha256": _sha256_bytes(tracked_diff.stdout.encode()),
        "untracked_files": untracked_hashes,
    }


def build_source_manifest(session: dict[str, Any]) -> dict[str, Any]:
    repos = [repo_manifest(name, path) for name, path in REPO_DIRS.items()]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": utc_now_iso(),
        "session_id": session.get("session_id"),
        "mount_root": session.get("paths", {}).get("mount_root"),
        "remote_session_root": session.get("remote_session_root"),
        "repositories": repos,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    manifest["snapshot_sha256"] = _sha256_bytes(canonical)
    return manifest


def create_repo_tarball(repo_path: Path) -> bytes:
    """Archive working tree including dirty/untracked non-ignored files."""
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


def ssh_base(machine: dict[str, Any]) -> list[str]:
    host = machine["host"]
    user = machine.get("user", "root")
    port = str(machine.get("port", 22))
    return ["ssh", "-p", port, f"{user}@{host}"]


def remote_mkdir(machine: dict[str, Any], remote_path: str) -> None:
    cmd = ssh_base(machine) + ["mkdir", "-p", remote_path]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode:
        raise WorkspaceStateError(
            f"remote mkdir failed for {remote_path}: {result.stderr.strip() or result.stdout.strip()}"
        )


def remote_extract_tarball(
    machine: dict[str, Any], remote_dir: str, tarball: bytes
) -> None:
    remote_mkdir(machine, remote_dir)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as tmp:
        tmp.write(tarball)
        tmp.flush()
        scp_port = str(machine.get("port", 22))
        user = machine.get("user", "root")
        host = machine["host"]
        remote_tmp = f"/tmp/mws-parity-{hashlib.sha256(tarball).hexdigest()[:12]}.tar.gz"
        scp = [
            "scp",
            "-P",
            scp_port,
            tmp.name,
            f"{user}@{host}:{remote_tmp}",
        ]
        result = subprocess.run(scp, check=False, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceStateError(
                f"scp failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        extract = ssh_base(machine) + [
            "bash",
            "-lc",
            f"mkdir -p {remote_dir} && tar -xzf {remote_tmp} -C {remote_dir} && rm -f {remote_tmp}",
        ]
        result = subprocess.run(extract, check=False, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceStateError(
                f"remote extract failed: {result.stderr.strip() or result.stdout.strip()}"
            )


def sync_session_to_remote(session: dict[str, Any], machine: dict[str, Any]) -> dict[str, Any]:
    paths = session.get("paths", {})
    manifest = build_source_manifest(session)
    progress("building repository tarballs")
    synced: list[dict[str, Any]] = []
    for name, repo_path in REPO_DIRS.items():
        key = f"{name}_source" if name != "vllm_ascend" else "vllm_ascend_source"
        if name == "motor":
            key = "motor_source"
        elif name == "vllm":
            key = "vllm_source"
        else:
            key = "vllm_ascend_source"
        remote_dir = paths[key]
        tarball = create_repo_tarball(repo_path)
        progress(f"syncing {name} to {remote_dir}")
        remote_extract_tarball(machine, remote_dir, tarball)
        synced.append(
            {
                "name": name,
                "remote_dir": remote_dir,
                "tarball_sha256": _sha256_bytes(tarball),
            }
        )
    manifest["synced"] = synced
    return manifest


def fanout_nodes(machine: dict[str, Any], nodes: list[str]) -> list[str]:
    backend = machine.get("parity_backend", "shared-hostpath")
    if backend == "shared-hostpath":
        return [machine["host"]]
    if backend == "node-local-hostpath":
        if not nodes:
            raise WorkspaceStateError("node-local-hostpath requires candidate_nodes")
        return list(nodes)
    raise WorkspaceStateError(f"unsupported parity_backend: {backend}")
