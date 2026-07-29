from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCAL_ROOT = ROOT / ".motor-workspace-local"


class CommandError(RuntimeError):
    pass


def progress(message: str) -> None:
    print(message, file=sys.stderr)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") in {"ok", "warning"} else 1


def run(
    args: list[str],
    *,
    cwd: pathlib.Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"required command not found: {args[0]}") from exc
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"{' '.join(args)} failed: {detail}")
    return result


def load_document(path: pathlib.Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise CommandError(
                f"{path} is YAML; install PyYAML or keep the file JSON-compatible YAML"
            ) from exc
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise CommandError(f"{path} must contain an object")
    return value


def git(args: list[str], path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(path), *args], check=False)


def source_state(name: str, config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / str(config["path"])
    if not path.exists():
        return {"name": name, "path": str(config["path"]), "present": False}
    head = git(["rev-parse", "HEAD"], path)
    status = git(["status", "--porcelain=v1", "--untracked-files=all"], path)
    branch = git(["branch", "--show-current"], path)
    if head.returncode:
        return {
            "name": name,
            "path": str(config["path"]),
            "present": True,
            "error": head.stderr.strip(),
        }
    lines = [line for line in status.stdout.splitlines() if line]
    return {
        "name": name,
        "path": str(config["path"]),
        "present": True,
        "commit": head.stdout.strip(),
        "branch": branch.stdout.strip() or "detached",
        "dirty": bool(lines),
        "changed_files": len(lines),
        "lock_commit": config.get("commit"),
        "lock_match": head.stdout.strip() == config.get("commit"),
    }


def command_status(_: argparse.Namespace) -> int:
    lock = load_document(ROOT / "workspace.lock.yaml")
    sources = [
        source_state(name, config)
        for name, config in lock.get("sources", {}).items()
    ]
    return emit(
        {
            "status": "ok",
            "workspace": str(ROOT),
            "sources": sources,
            "runtime": lock.get("runtime", {}),
        }
    )


def command_lock_verify(_: argparse.Namespace) -> int:
    lock = load_document(ROOT / "workspace.lock.yaml")
    errors: list[str] = []
    sources = []
    for name, config in lock.get("sources", {}).items():
        state = source_state(name, config)
        sources.append(state)
        if not state.get("present"):
            errors.append(f"{name}: submodule is not initialized")
        elif config.get("commit") == "UNRESOLVED":
            errors.append(f"{name}: lock commit is unresolved")
        elif not state.get("lock_match"):
            errors.append(f"{name}: submodule HEAD does not match lock")
    base_image = lock.get("runtime", {}).get("base_image", "")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", str(base_image)):
        errors.append("runtime.base_image must be repository:tag@sha256:digest")
    return emit(
        {
            "status": "error" if errors else "ok",
            "sources": sources,
            "errors": errors,
        }
    )


def kubectl_base(profile: dict[str, Any]) -> list[str]:
    args = ["kubectl"]
    context = profile.get("kubernetes", {}).get("context")
    if context:
        args.extend(["--context", str(context)])
    return args


def command_preflight(args: argparse.Namespace) -> int:
    profile_path = ROOT / args.profile
    profile = load_document(profile_path)
    kubectl = kubectl_base(profile)
    namespace = profile["kubernetes"]["namespace"]
    patterns = [
        str(item).lower()
        for item in profile["mindcluster"].get("component_patterns", [])
    ]
    required_apis = [
        str(item).lower()
        for item in profile["mindcluster"].get("required_api_resources", [])
    ]
    run_id = new_run_id("preflight")
    run_dir = LOCAL_ROOT / "runs" / run_id / "mindcluster"
    run_dir.mkdir(parents=True, exist_ok=False)
    progress(f"collecting read-only MindCluster evidence for {run_id}")

    probes = {
        "version": [*kubectl, "version", "-o", "json"],
        "api_resources": [*kubectl, "api-resources", "-o", "name"],
        "pods": [*kubectl, "get", "pods", "-A", "-o", "json"],
        "nodes": [*kubectl, "get", "nodes", "-o", "json"],
        "namespace_auth": [
            *kubectl,
            "auth",
            "can-i",
            "get",
            "pods",
            "-n",
            namespace,
        ],
    }
    evidence: dict[str, dict[str, Any]] = {}
    for name, command in probes.items():
        result = run(command, check=False)
        (run_dir / f"{name}.stdout").write_text(result.stdout, encoding="utf-8")
        (run_dir / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
        evidence[name] = {"returncode": result.returncode}

    api_text = (run_dir / "api_resources.stdout").read_text(encoding="utf-8").lower()
    missing_apis = [item for item in required_apis if item not in api_text]
    pods_text = (run_dir / "pods.stdout").read_text(encoding="utf-8").lower()
    missing_components = [item for item in patterns if item not in pods_text]
    auth_text = (run_dir / "namespace_auth.stdout").read_text(
        encoding="utf-8"
    ).strip().lower()
    errors = []
    if evidence["version"]["returncode"]:
        errors.append("cannot query Kubernetes API")
    if missing_apis:
        errors.append(f"missing API resources: {', '.join(missing_apis)}")
    if missing_components:
        errors.append(f"component patterns not found: {', '.join(missing_components)}")
    if auth_text != "yes":
        errors.append(f"cannot read pods in namespace {namespace}")

    result_payload = {
        "status": "error" if errors else "ok",
        "run_id": run_id,
        "profile": profile["name"],
        "read_only": True,
        "evidence_dir": str(run_dir.relative_to(ROOT)),
        "missing_api_resources": missing_apis,
        "missing_component_patterns": missing_components,
        "errors": errors,
        "next": "inspect evidence, then configure build" if not errors else "fix preflight errors",
    }
    (run_dir.parent / "run.json").write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return emit(result_payload)


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot_repo(name: str, config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / str(config["path"])
    if not path.exists():
        raise CommandError(f"{name}: submodule is not initialized")
    head = git(["rev-parse", "HEAD"], path)
    if head.returncode:
        raise CommandError(f"{name}: cannot resolve HEAD")
    status = git(["status", "--porcelain=v1", "--untracked-files=all"], path)
    tracked_diff = git(["diff", "--binary", "HEAD"], path)
    untracked = git(["ls-files", "--others", "--exclude-standard", "-z"], path)
    untracked_names = [
        item for item in untracked.stdout.split("\0") if item
    ]
    untracked_hashes = {}
    for relative in untracked_names:
        candidate = path / relative
        if candidate.is_file():
            untracked_hashes[relative] = hash_bytes(candidate.read_bytes())
    return {
        "name": name,
        "path": str(config["path"]),
        "commit": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_sha256": hash_bytes(status.stdout.encode()),
        "tracked_diff_sha256": hash_bytes(tracked_diff.stdout.encode()),
        "untracked_files": untracked_hashes,
    }


def new_run_id(prefix: str) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def command_snapshot(_: argparse.Namespace) -> int:
    lock = load_document(ROOT / "workspace.lock.yaml")
    run_id = new_run_id("snapshot")
    source_dir = LOCAL_ROOT / "runs" / run_id / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    progress(f"creating source manifest {run_id}")
    repos = [
        snapshot_repo(name, config)
        for name, config in lock.get("sources", {}).items()
    ]
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repositories": repos,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    manifest["snapshot_sha256"] = hash_bytes(canonical)
    path = source_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return emit(
        {
            "status": "ok",
            "run_id": run_id,
            "snapshot_sha256": manifest["snapshot_sha256"],
            "artifacts": [str(path.relative_to(ROOT))],
            "next": "materialize a build context with tools/build",
        }
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="motorws")
    commands = root.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="show source and lock state")
    status.set_defaults(handler=command_status)

    lock = commands.add_parser("lock", help="manage workspace lock")
    lock_commands = lock.add_subparsers(dest="lock_command", required=True)
    verify = lock_commands.add_parser("verify", help="verify source/runtime lock")
    verify.set_defaults(handler=command_lock_verify)

    preflight = commands.add_parser("preflight", help="run read-only checks")
    preflight_commands = preflight.add_subparsers(
        dest="preflight_command", required=True
    )
    mindcluster = preflight_commands.add_parser(
        "mindcluster", help="check MindCluster and Volcano"
    )
    mindcluster.add_argument("--profile", default="profiles/a2-dev.yaml")
    mindcluster.set_defaults(handler=command_preflight)

    snapshot = commands.add_parser("snapshot", help="source snapshot operations")
    snapshot_commands = snapshot.add_subparsers(
        dest="snapshot_command", required=True
    )
    create = snapshot_commands.add_parser(
        "create", help="create a three-repository source manifest"
    )
    create.set_defaults(handler=command_snapshot)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        return args.handler(args)
    except CommandError as exc:
        return emit({"status": "error", "errors": [str(exc)]})
    except KeyboardInterrupt:
        return emit({"status": "error", "errors": ["interrupted"]})

