#!/usr/bin/env python3
"""Remote-code-parity helpers: sync local dirty tree to machine fixed directories.

Transport is git-object incremental (synthetic snapshot commit -> bundle ->
bare mirror -> worktree materialize). Overlay (python-overlay, not a git repo)
keeps the plain tarball path.
"""

from __future__ import annotations

import hashlib
import os
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
MIRROR_DIRNAME = ".mws-mirrors"
PARITY_REF = "refs/parity/current"
PARITY_REMOTE_BRANCH = "parity/current"
PARITY_LOCAL_REF = "refs/parity/snapshot"
PARITY_REMOTE_NAME = "parity"
SNAPSHOT_PARENT_REF = "refs/parity/snapshot-parent"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(
    args: list[str],
    path: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
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


def _working_tree_tree_hash(repo_path: Path) -> str:
    """Compute the tree hash of the working tree via a temporary index.

    `git read-tree HEAD` + `git add -A` + `git write-tree` reproduces the full
    working tree (tracked changes and untracked files, respecting .gitignore)
    without touching the real index. The resulting tree hash is a content
    digest of the dirty working tree.
    """
    index_fd, index_path = tempfile.mkstemp(prefix="mws-parity-index-")
    os.close(index_fd)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = index_path
    env.setdefault("GIT_AUTHOR_NAME", "mws-parity")
    env.setdefault("GIT_AUTHOR_EMAIL", "mws-parity@localhost")
    env.setdefault("GIT_COMMITTER_NAME", "mws-parity")
    env.setdefault("GIT_COMMITTER_EMAIL", "mws-parity@localhost")
    try:
        result = _git(["read-tree", "HEAD"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"git read-tree failed for {repo_path}: {result.stderr.strip()}"
            )
        result = _git(["add", "-A"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"git add failed for {repo_path}: {result.stderr.strip()}"
            )
        result = _git(["write-tree"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"git write-tree failed for {repo_path}: {result.stderr.strip()}"
            )
        return result.stdout.strip()
    finally:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass


def repo_manifest(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkspaceStateError(f"{name}: submodule not initialized at {path}")
    head = _git(["rev-parse", "HEAD"], path)
    if head.returncode:
        raise WorkspaceStateError(f"{name}: cannot resolve HEAD")
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"], path)
    tracked_diff = _git(["diff", "--binary", "HEAD"], path)
    ls = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], path)
    if ls.returncode:
        raise WorkspaceStateError(f"{name}: git ls-files failed")
    members = [item for item in ls.stdout.split("\0") if item]
    tracked = _git(["ls-files", "-z", "--cached"], path)
    tracked_set = set(tracked.stdout.split("\0")) if tracked.returncode == 0 else set()
    untracked_hashes: dict[str, str] = {}
    for relative in members:
        absolute = path / relative
        if relative not in tracked_set and absolute.is_file():
            untracked_hashes[relative] = _sha256_bytes(absolute.read_bytes())
    return {
        "name": name,
        "path": _repo_relative_path(path),
        "commit": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_sha256": _sha256_bytes(status.stdout.encode()),
        "tracked_diff_sha256": _sha256_bytes(tracked_diff.stdout.encode()),
        "file_count": len(members),
        "content_digest": _working_tree_tree_hash(path),
        "untracked_files": untracked_hashes,
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
    run_id = str(machine_run_id or "").strip()
    if not run_id:
        candidates = []
        if MACHINE_RUNS_DIR.exists():
            candidates = sorted(
                MACHINE_RUNS_DIR.glob("*/run.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        for candidate in candidates:
            record = load_json(candidate, default={})
            if not isinstance(record, dict):
                continue
            if (
                record.get("kind") == "machine-ready"
                and record.get("status") == "ready"
                and str(record.get("alias") or record.get("machine") or "") == machine_alias
            ):
                run_id = candidate.parent.name
                break
        if not run_id:
            raise WorkspaceStateError(
                f"no successful machine-ready run found for {machine_alias!r}"
            )
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


def _temp_git_env() -> tuple[dict[str, str], str]:
    """Return (env, index_path) with a private GIT_INDEX_FILE so the real index
    and working tree are never touched by synthetic-snapshot commands."""
    index_fd, index_path = tempfile.mkstemp(prefix="mws-parity-index-")
    os.close(index_fd)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = index_path
    env.setdefault("GIT_AUTHOR_NAME", "mws-parity")
    env.setdefault("GIT_AUTHOR_EMAIL", "mws-parity@localhost")
    env.setdefault("GIT_COMMITTER_NAME", "mws-parity")
    env.setdefault("GIT_COMMITTER_EMAIL", "mws-parity@localhost")
    return env, index_path


def build_synthetic_snapshot(
    repo_path: Path,
    *,
    parent_commit: str | None = None,
) -> dict[str, Any]:
    """Build a synthetic snapshot commit whose tree is the full dirty working
    tree (tracked changes + untracked files).

    The snapshot is built with a private temporary index, so the local
    repository's real index and working tree are never mutated. When a parent
    commit is given the snapshot chains onto it, so `git bundle base..snapshot`
    transfers only the object delta."""
    env, index_path = _temp_git_env()
    try:
        result = _git(["read-tree", "HEAD"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"synthetic snapshot read-tree failed for {repo_path}: {result.stderr.strip()}"
            )
        result = _git(["add", "-A"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"synthetic snapshot add failed for {repo_path}: {result.stderr.strip()}"
            )
        result = _git(["write-tree"], repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"synthetic snapshot write-tree failed for {repo_path}: {result.stderr.strip()}"
            )
        tree = result.stdout.strip()
        commit_args = ["commit-tree", tree, "-m", "mws parity snapshot"]
        if parent_commit:
            commit_args += ["-p", parent_commit]
        result = _git(commit_args, repo_path, env=env)
        if result.returncode:
            raise WorkspaceStateError(
                f"synthetic snapshot commit-tree failed for {repo_path}: {result.stderr.strip()}"
            )
        snapshot_commit = result.stdout.strip()
        base = parent_commit or "HEAD"
        diff_result = _git(
            ["diff", "--name-only", f"{base}..{snapshot_commit}"], repo_path, env=env
        )
        changed_paths: list[str] = []
        if diff_result.returncode == 0:
            changed_paths = [line for line in diff_result.stdout.splitlines() if line]
        return {
            "commit": snapshot_commit,
            "tree": tree,
            "changed_paths": changed_paths,
        }
    finally:
        os.unlink(index_path)


def create_incremental_bundle(
    repo_path: Path,
    *,
    base_commit: str | None,
) -> bytes:
    """Create a git bundle carrying only the objects reachable from the latest
    parity snapshot but not from the base snapshot.

    The caller must already have pointed `PARITY_LOCAL_REF` at the new snapshot
    commit. With no base commit the bundle is the full synthetic tree; with a
    base it transfers only the object delta."""
    fd, bundle_path = tempfile.mkstemp(prefix="mws-parity-bundle-", suffix=".bundle")
    os.close(fd)
    try:
        if base_commit:
            result = _git(
                ["bundle", "create", bundle_path, f"{base_commit}..{PARITY_LOCAL_REF}"],
                repo_path,
            )
        else:
            result = _git(["bundle", "create", bundle_path, PARITY_LOCAL_REF], repo_path)
        if result.returncode:
            raise WorkspaceStateError(
                f"git bundle create failed for {repo_path}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return Path(bundle_path).read_bytes()
    finally:
        Path(bundle_path).unlink(missing_ok=True)


def _local_snapshot_commit(repo_path: Path) -> str | None:
    """Return the last synthetic snapshot commit for the repo, if any."""
    result = _git(["rev-parse", "--verify", PARITY_LOCAL_REF], repo_path)
    if result.returncode:
        return None
    return result.stdout.strip()


def _mirror_has_commit(transport: RemoteTransport, mirror_dir: str, commit: str) -> bool:
    result = transport.git(mirror_dir, "cat-file", "-e", f"{commit}^{{commit}}")
    return result.returncode == 0


def mirror_dir_for(machine: dict[str, Any], repo_name: str) -> str:
    paths = build_fixed_source_paths(machine)
    return f"{paths['remote_workspace_root']}/{MIRROR_DIRNAME}/{repo_name}.git"


def _remote_head(transport: RemoteTransport, worktree_dir: str) -> str:
    result = transport.git(worktree_dir, "rev-parse", "HEAD")
    if result.returncode:
        raise WorkspaceStateError(
            f"cannot resolve remote HEAD at {worktree_dir}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def ensure_remote_mirror(transport: RemoteTransport, mirror_dir: str) -> None:
    result = transport.git(mirror_dir, "rev-parse", "--is-bare-repository")
    if result.returncode == 0 and result.stdout.strip() == "true":
        return
    transport.run(f"mkdir -p {shell_quote(mirror_dir)}")
    init = transport.git(mirror_dir, "init", "--bare")
    if init.returncode:
        raise WorkspaceStateError(
            f"git init --bare failed for {mirror_dir}: "
            f"{init.stderr.strip() or init.stdout.strip()}"
        )


def publish_bundle_to_mirror(
    transport: RemoteTransport,
    *,
    mirror_dir: str,
    repo_name: str,
    bundle: bytes,
) -> None:
    digest = _sha256_bytes(bundle)[:12]
    remote_archive = f"/tmp/mws-parity-{repo_name}-{digest}.bundle"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bundle") as handle:
        handle.write(bundle)
        local_archive = handle.name
    try:
        transport.upload_file(local_archive, remote_archive)
    finally:
        Path(local_archive).unlink(missing_ok=True)

    refspec = f"{PARITY_LOCAL_REF}:{PARITY_REF}"
    fetch = transport.git(mirror_dir, "fetch", "--force", remote_archive, refspec)
    transport.run(f"rm -f {shell_quote(remote_archive)}")
    if fetch.returncode:
        raise WorkspaceStateError(
            f"mirror fetch failed for {repo_name}: "
            f"{fetch.stderr.strip() or fetch.stdout.strip()}"
        )


def materialize_worktree(
    transport: RemoteTransport,
    *,
    mirror_dir: str,
    worktree_dir: str,
) -> None:
    """Materialize the parity/current snapshot into the fixed worktree dir.

    The worktree is a real git worktree whose origin/remote is the shared
    mirror. `checkout -f -B` + `reset --hard` + `clean -ffd` makes the fixed
    directory exactly match the snapshot, self-healing remote drift."""
    check = transport.run(f"test -d {shell_quote(worktree_dir)}/.git")
    if check.returncode != 0:
        transport.run(f"rm -rf {shell_quote(worktree_dir)}")
        transport.run(f"mkdir -p {shell_quote(worktree_dir)}")
        init = transport.git(worktree_dir, "init")
        if init.returncode:
            raise WorkspaceStateError(
                f"worktree init failed for {worktree_dir}: "
                f"{init.stderr.strip() or init.stdout.strip()}"
            )
    set_url = transport.git(
        worktree_dir, "remote", "set-url", PARITY_REMOTE_NAME, mirror_dir
    )
    if set_url.returncode:
        add = transport.git(
            worktree_dir, "remote", "add", PARITY_REMOTE_NAME, mirror_dir
        )
        if add.returncode:
            raise WorkspaceStateError(
                f"worktree remote add failed for {worktree_dir}: "
                f"{add.stderr.strip() or add.stdout.strip()}"
            )

    fetch = transport.git(
        worktree_dir,
        "fetch",
        "--force",
        PARITY_REMOTE_NAME,
        f"{PARITY_REF}:refs/remotes/parity/current",
    )
    if fetch.returncode:
        raise WorkspaceStateError(
            f"worktree fetch failed for {worktree_dir}: "
            f"{fetch.stderr.strip() or fetch.stdout.strip()}"
        )
    checkout = transport.git(
        worktree_dir,
        "checkout",
        "-f",
        "-B",
        PARITY_REMOTE_BRANCH,
        "refs/remotes/parity/current",
    )
    if checkout.returncode:
        raise WorkspaceStateError(
            f"worktree checkout failed for {worktree_dir}: "
            f"{checkout.stderr.strip() or checkout.stdout.strip()}"
        )
    reset = transport.git(
        worktree_dir, "reset", "--hard", "refs/remotes/parity/current"
    )
    if reset.returncode:
        raise WorkspaceStateError(
            f"worktree reset failed for {worktree_dir}: "
            f"{reset.stderr.strip() or reset.stdout.strip()}"
        )
    clean = transport.git(worktree_dir, "clean", "-ffd")
    if clean.returncode:
        raise WorkspaceStateError(
            f"worktree clean failed for {worktree_dir}: "
            f"{clean.stderr.strip() or clean.stdout.strip()}"
        )


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
    for name in REPO_DIRS:
        worktree_dir = paths[REPO_REMOTE_KEYS[name]]
        expected_commit = prior.get("snapshot_commits", {}).get(name)
        if not expected_commit:
            return None
        head_result = transport.git(worktree_dir, "rev-parse", "HEAD")
        status_result = transport.git(worktree_dir, "status", "--porcelain")
        if (
            head_result.returncode
            or head_result.stdout.strip() != expected_commit
            or status_result.stdout.strip()
        ):
            return None
        observed = expected_commit
        item = {
            "name": name,
            "remote_dir": worktree_dir,
            "content_digest": expected_commit,
            "file_count": None,
            "verified": True,
            "missing_files": [],
            "extra_files": [],
            "mismatched_files": [],
        }
        proof.append(item)
        remote_digests[name] = expected_commit

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
            snapshot_commits: dict[str, str] = {}
            used_incremental = False
            for name, repo_path in REPO_DIRS.items():
                remote_dir = paths[REPO_REMOTE_KEYS[name]]
                mirror_dir = mirror_dir_for(machine, name)
                ensure_remote_mirror(tx, mirror_dir)
                base = _local_snapshot_commit(repo_path)
                if base and not _mirror_has_commit(tx, mirror_dir, base):
                    base = None
                if base:
                    used_incremental = True
                snapshot = build_synthetic_snapshot(repo_path, parent_commit=base)
                _git(["update-ref", PARITY_LOCAL_REF, snapshot["commit"]], repo_path)
                bundle = create_incremental_bundle(repo_path, base_commit=base)
                progress(f"syncing {name} to {remote_dir}")
                publish_bundle_to_mirror(
                    tx,
                    mirror_dir=mirror_dir,
                    repo_name=name,
                    bundle=bundle,
                )
                materialize_worktree(
                    tx, mirror_dir=mirror_dir, worktree_dir=remote_dir
                )
                observed = _remote_head(tx, remote_dir)
                if observed != snapshot["commit"]:
                    raise WorkspaceStateError(
                        f"remote proof failed for {name}: "
                        f"expected {snapshot['commit']}, got {observed}"
                    )
                snapshot_commits[name] = snapshot["commit"]
                synced.append(
                    {
                        "name": name,
                        "remote_dir": remote_dir,
                        "snapshot_commit": snapshot["commit"],
                        "changed_files": len(snapshot["changed_paths"]),
                        "incremental": base is not None,
                    }
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

            proof: list[dict[str, Any]] = []
            remote_digests: dict[str, str] = {}
            for name in REPO_DIRS:
                remote_dir = paths[REPO_REMOTE_KEYS[name]]
                item = {
                    "name": name,
                    "remote_dir": remote_dir,
                    "content_digest": snapshot_commits[name],
                    "file_count": None,
                    "verified": True,
                    "missing_files": [],
                    "extra_files": [],
                    "mismatched_files": [],
                }
                proof.append(item)
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
            manifest["snapshot_commits"] = snapshot_commits
            manifest["remote_content_digests"] = remote_digests
            manifest["remote_content_digest"] = aggregate_content_digest(
                {key: digest for key, digest in remote_digests.items()}
            )
            manifest["sync_mode"] = (
                "git-incremental" if used_incremental else "git-initial"
            )
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
                    "snapshot_commits": snapshot_commits,
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


def prove_identity_parity(
    machine: dict[str, Any],
    *,
    transport: RemoteTransport | None = None,
    machine_ready: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove source readiness for the remote-native topology via identity.

    In remote-native the Agent runs on the target host and its working tree
    *is* the machine's fixed source paths that Pods load via hostPath +
    PYTHONPATH. This proves identity: each local source repo resolves to the
    machine's fixed source dir, the fixed dirs exist, and content digests are
    captured as evidence. Nothing is copied or overwritten.

    Fails closed: local repo not at the fixed path, fixed dir missing, or
    machine mismatch never publishes a ready proof.
    """
    validate_machine_transport_fields(machine)
    machine_alias = str(machine.get("alias") or machine.get("host"))
    if machine_ready is None:
        raise WorkspaceStateError(
            f"machine-ready evidence required for identity parity on {machine_alias!r}; "
            "run machine-management verify first"
        )
    paths = build_fixed_source_paths(machine)
    tx = transport or transport_for_machine(machine)
    lock_path = remote_lock_path(machine)

    expected = {
        "motor": paths["motor_source"],
        "vllm": paths["vllm_source"],
        "vllm_ascend": paths["vllm_ascend_source"],
    }

    progress(f"identity parity target host={machine.get('host')} source_mode=identity")

    with file_lock(local_parity_lock_path(machine_alias)):
        tx.acquire_parity_lock(lock_path)
        try:
            local_digests: dict[str, str] = {}
            repo_evidence: list[dict[str, Any]] = []
            for name in ("motor", "vllm", "vllm_ascend"):
                local_repo = REPO_DIRS[name]
                fixed_dir = Path(expected[name])
                if local_repo.resolve() != fixed_dir.resolve():
                    raise WorkspaceStateError(
                        f"identity parity failed for {name}: local source repo {local_repo} "
                        f"does not resolve to fixed source dir {fixed_dir}; this topology "
                        "requires the Agent working tree to be the machine fixed source paths"
                    )
                probe = tx.run(f"test -d {shell_quote(str(fixed_dir))}")
                if probe.returncode != 0:
                    raise WorkspaceStateError(
                        f"identity parity failed for {name}: fixed source dir missing: {fixed_dir}"
                    )
                manifest_item = repo_manifest(name, local_repo)
                repo_evidence.append(manifest_item)
                local_digests[name] = manifest_item["content_digest"]

            local_bundle = dict(local_digests)
            identity_manifest = {
                "schema_version": 2,
                "kind": "parity-complete",
                "status": "ready",
                "source_mode": "identity",
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
                "repositories": repo_evidence,
                "local_content_digest": aggregate_content_digest(local_bundle),
                "local_content_digests": local_bundle,
                "remote_content_digest": aggregate_content_digest(local_bundle),
                "remote_content_digests": local_bundle,
                "content_digests": local_bundle,
                "machine_ready": machine_ready,
                "pythonpath": ":".join(
                    [
                        paths["motor_source"],
                        paths["vllm_source"],
                        paths["vllm_ascend_source"],
                        paths["python_overlay"],
                    ]
                ),
                "target": machine.get("host"),
                "sync_mode": "identity",
            }

            save_parity_state(
                machine_alias,
                {
                    "machine_alias": machine_alias,
                    "updated_at": identity_manifest["created_at"],
                    "local_content_digest": identity_manifest["local_content_digest"],
                    "remote_content_digest": identity_manifest["remote_content_digest"],
                    "remote_content_digests": local_bundle,
                    "snapshot_commits": {},
                    "machine_ready": machine_ready,
                    "source_dirs": identity_manifest["source_dirs"],
                },
            )
            return identity_manifest
        finally:
            tx.release_parity_lock(lock_path)
