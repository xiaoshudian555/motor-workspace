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

from mws_deploy import load_profile, pod_readiness_probe  # noqa: E402
from mws_local_state import get_machine, load_inventory, save_inventory, utc_now_iso  # noqa: E402
from mws_parity import remote_mkdir, ssh_base  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    alias = require_safe_id(args.alias, label="alias")
    machine = get_machine(alias)
    progress("checking SSH connectivity")
    probe = ssh_base(machine) + ["echo", "ok"]
    result = subprocess.run(probe, check=False, text=True, capture_output=True)
    errors: list[str] = []
    if result.returncode or result.stdout.strip() != "ok":
        errors.append("SSH probe failed")
    progress("checking mount root writable")
    try:
        test_dir = f"{machine['mount_root']}/motor-workspace/.verify-{alias}"
        remote_mkdir(machine, test_dir)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    k8s = {}
    profile_path = ROOT / args.profile
    if profile_path.exists():
        profile = load_profile(profile_path)
        namespace = profile.get("kubernetes", {}).get("namespace", "")
        context = machine.get("kube_context") or profile.get("kubernetes", {}).get("context", "")
        kubectl = ["kubectl"]
        if context:
            kubectl.extend(["--context", context])
        cmd = [*kubectl, "auth", "can-i", "get", "pods", "-n", namespace]
        auth = subprocess.run(cmd, check=False, text=True, capture_output=True)
        k8s["namespace_auth"] = auth.stdout.strip()
        if auth.stdout.strip().lower() != "yes":
            errors.append(f"cannot get pods in namespace {namespace}")
        k8s["pods"] = pod_readiness_probe(profile, namespace)
    inventory = load_inventory()
    machine = inventory["machines"][alias]
    machine["last_verified_at"] = utc_now_iso()
    machine["last_verify_errors"] = errors
    save_inventory(inventory)
    return emit(
        {
            "status": "error" if errors else "ok",
            "alias": alias,
            "errors": errors,
            "k8s": k8s,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
