#!/usr/bin/env python3
"""Motor deployer thin-wrapper helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from mws_local_state import ROOT, WorkspaceStateError
from mws_session_state import pythonpath_for_session

DEPLOYER_ROOT = ROOT / "motor" / "examples" / "deployer"
DEPLOY_PY = DEPLOYER_ROOT / "deploy.py"


def load_profile(profile_path: Path) -> dict[str, Any]:
    text = profile_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise WorkspaceStateError(
                f"{profile_path} is YAML; install PyYAML"
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{profile_path} must contain an object")
    return data


def kubectl_base(profile: dict[str, Any]) -> list[str]:
    args = ["kubectl"]
    context = profile.get("kubernetes", {}).get("context")
    if context:
        args.extend(["--context", str(context)])
    return args


def inject_pythonpath_env(manifest_text: str, pythonpath: str) -> str:
    if not pythonpath:
        return manifest_text
    lines = manifest_text.splitlines()
    out: list[str] = []
    i = 0
    injected = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if re.match(r"^\s+- name:\s+\w+\s*$", line) and i + 1 < len(lines):
            # skip if next block already has PYTHONPATH
            block = "\n".join(lines[i : min(i + 20, len(lines))])
            if "PYTHONPATH" in block:
                i += 1
                continue
        if line.strip() == "env:":
            out.append(f"            - name: PYTHONPATH")
            out.append(f"              value: \"{pythonpath}\"")
            injected += 1
        i += 1
    if injected == 0 and "kind:" in manifest_text:
        # fallback: append env to first container block via comment marker
        return manifest_text + (
            f"\n# mws-pythonpath-injection-required: {pythonpath}\n"
        )
    return "\n".join(out)


def render_plan(
    *,
    session: dict[str, Any],
    profile: dict[str, Any],
    config_dir: Path,
    run_dir: Path,
) -> dict[str, Any]:
    if not DEPLOY_PY.exists():
        raise WorkspaceStateError(f"deployer not found: {DEPLOY_PY}")
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "python3",
        str(DEPLOY_PY),
        "--config_dir",
        str(config_dir),
        "--dry_run",
    ]
    env = os.environ.copy()
    pythonpath = pythonpath_for_session(session)
    if pythonpath:
        env["MWS_PYTHONPATH"] = pythonpath
    result = subprocess.run(
        cmd,
        cwd=str(DEPLOYER_ROOT),
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    stdout_path = run_dir / "deploy.stdout"
    stderr_path = run_dir / "deploy.stderr"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "returncode": result.returncode,
        "stdout_path": str(stdout_path.relative_to(ROOT)),
        "stderr_path": str(stderr_path.relative_to(ROOT)),
        "pythonpath": pythonpath,
    }


def apply_deploy(*, config_dir: Path) -> dict[str, Any]:
    cmd = ["python3", str(DEPLOY_PY), "--config_dir", str(config_dir)]
    result = subprocess.run(
        cmd,
        cwd=str(DEPLOYER_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def pod_readiness_probe(profile: dict[str, Any], namespace: str) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    cmd = [*kubectl, "get", "pods", "-n", namespace, "-o", "json"]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode:
        return {"ready": False, "error": result.stderr.strip()}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ready": False, "error": "invalid kubectl json"}
    items = data.get("items", [])
    total = len(items)
    ready = sum(
        1
        for pod in items
        if all(
            cond.get("status") == "True"
            for cond in pod.get("status", {}).get("conditions", [])
            if cond.get("type") == "Ready"
        )
    )
    return {"ready": total > 0 and ready == total, "pods_total": total, "pods_ready": ready}
