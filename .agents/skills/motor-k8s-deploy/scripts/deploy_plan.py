#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import load_profile, render_plan, resolve_deploy_base_image  # noqa: E402
from mws_lock import load_lock, verify_lock  # noqa: E402
from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_machine_target import pythonpath_for_machine, resolve_machine  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import deploy_run_dir, new_run_id, write_deploy_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def run_parity(machine: str) -> dict:
    script = ROOT / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--machine",
            machine,
            "--approved-overwrite",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"status": "error", "errors": [result.stderr.strip() or result.stdout.strip()]}
        return payload
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--skip-parity", action="store_true")
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    config_dir = Path(args.config_dir) if args.config_dir else ROOT / "motor/examples/infer_engines/vllm"
    lock = verify_lock(require_base_image=False, strict_commits=False)
    try:
        base_image_ref = resolve_deploy_base_image(config_dir, lock=load_lock())
    except WorkspaceStateError as exc:
        return emit({"status": "error", "errors": [str(exc)]})

    parity_run_id = None
    if not args.skip_parity:
        progress("running parity before plan")
        parity = run_parity(alias)
        if parity.get("status") != "ok":
            return emit(parity)
        parity_run_id = parity.get("parity_run_id")

    profile_path = args.profile
    profile = load_profile(ROOT / profile_path)
    run_id = new_run_id("deploy")
    run_dir = deploy_run_dir(run_id) / "plan"
    progress("rendering deploy plan")
    try:
        plan_body = render_plan(
            machine=machine,
            profile=profile,
            profile_path=profile_path,
            config_dir=config_dir,
            run_dir=run_dir,
            base_image_ref=base_image_ref,
            parity_run_id=parity_run_id,
            lock_verify=lock,
        )
    except WorkspaceStateError as exc:
        return emit({"status": "error", "errors": [str(exc)]})

    deploy_status = plan_body.get("deploy_dry_run", {}).get("status")
    k8s_status = plan_body.get("kubernetes", {}).get("status")
    overall = "ok"
    if deploy_status == "error":
        overall = "error"
    elif k8s_status == "warning":
        overall = "warning"
    payload = {
        "status": overall,
        "deploy_run_id": run_id,
        "parity_run_id": parity_run_id,
        "machine": alias,
        "namespace": plan_body.get("namespace"),
        "job_id": plan_body.get("job_id"),
        "pythonpath": pythonpath_for_machine(machine),
        "base_image_ref": base_image_ref,
        "plan_dir": str(run_dir.relative_to(ROOT)),
        "manifest_files": plan_body.get("manifest_files", []),
        "lock_warnings": lock.get("warnings", []),
        "read_only": True,
        "next": (
            f"review plan then deploy_apply.py --machine {alias} "
            f"--deploy-run-id {run_id} --approved-by-user"
        ),
    }
    write_deploy_run(run_id, payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
