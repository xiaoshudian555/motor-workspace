#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import kubectl_base, load_profile, pod_readiness_probe  # noqa: E402
from mws_local_state import get_machine, load_inventory, save_inventory, utc_now_iso  # noqa: E402
from mws_parity import fanout_nodes  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_transport import shell_quote, transport_for_machine, validate_machine_transport_fields  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402

API_RESOURCE_GROUPS = {
    "ascendjobs": "mindx.huawei.com",
    "podgroups": "scheduling.volcano.sh",
}


def check_item(name: str, *, status: str, evidence: str = "", error: str = "") -> dict:
    return {"name": name, "status": status, "evidence": evidence, "error": error}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    alias = require_safe_id(args.alias, label="alias")
    machine = get_machine(alias)
    checks: list[dict] = []
    errors: list[str] = []

    progress("checking SSH connectivity")
    try:
        validate_machine_transport_fields(machine)
        transport = transport_for_machine(machine)
        ssh = transport.run("echo ok")
        if ssh.returncode == 0 and ssh.stdout.strip() == "ok":
            checks.append(check_item("ssh", status="ok", evidence=f"{machine.get('user','root')}@{machine['host']}"))
        else:
            msg = ssh.stderr.strip() or ssh.stdout.strip() or "SSH probe failed"
            checks.append(check_item("ssh", status="error", error=msg))
            errors.append(msg)
    except Exception as exc:  # noqa: BLE001
        checks.append(check_item("ssh", status="error", error=str(exc)))
        errors.append(str(exc))
        transport = None

    progress("checking mount root writable")
    verify_token = uuid.uuid4().hex[:12]
    verify_dir = f"{machine['mount_root']}/motor-workspace/.mws-verify-{verify_token}"
    probe_file = f"{verify_dir}/write-test"
    if transport is not None:
        try:
            transport.mkdir(verify_dir)
            payload = f"mws-verify-{verify_token}".encode()
            transport.upload_bytes(probe_file, payload)
            read_back = transport.read_bytes(probe_file)
            if read_back != payload:
                raise RuntimeError("remote write verification failed: content mismatch")
            transport.run(f"rm -rf {shell_quote(verify_dir)}")
            if transport.run(f"test -d {shell_quote(verify_dir)}").returncode == 0:
                raise RuntimeError("remote verify directory was not cleaned up")
            checks.append(check_item("mount_root", status="ok", evidence=machine["mount_root"]))
        except Exception as exc:  # noqa: BLE001
            checks.append(check_item("mount_root", status="error", error=str(exc)))
            errors.append(str(exc))
            transport.run(f"rm -rf {shell_quote(verify_dir)}")

    profile_path = ROOT / args.profile
    profile = load_profile(profile_path) if profile_path.exists() else {}
    machine_context = machine.get("kube_context") or profile.get("kubernetes", {}).get("context", "")
    profile_context = profile.get("kubernetes", {}).get("context", "")
    if machine_context != profile_context and machine_context and profile_context:
        checks.append(
            check_item(
                "kube_context_consistency",
                status="error",
                error=f"machine kube_context={machine_context!r} != profile context={profile_context!r}",
            )
        )
        errors.append("kube context mismatch between machine inventory and profile")
    else:
        checks.append(
            check_item(
                "kube_context_consistency",
                status="ok",
                evidence=machine_context or profile_context or "default",
            )
        )

    namespace = profile.get("kubernetes", {}).get("namespace", "")
    kubectl = kubectl_base(profile)
    if shutil.which("kubectl"):
        auth_cmd = [*kubectl, "auth", "can-i", "get", "pods", "-n", namespace]
        auth = subprocess.run(auth_cmd, check=False, text=True, capture_output=True)
        auth_ok = auth.stdout.strip().lower() == "yes"
        checks.append(
            check_item(
                "namespace_auth",
                status="ok" if auth_ok else "error",
                evidence=auth.stdout.strip(),
                error="" if auth_ok else f"cannot get pods in namespace {namespace}",
            )
        )
        if not auth_ok:
            errors.append(f"cannot get pods in namespace {namespace}")

        for resource in profile.get("mindcluster", {}).get("required_api_resources", []):
            api_group = API_RESOURCE_GROUPS.get(resource)
            if not api_group:
                checks.append(
                    check_item(
                        f"api_resource:{resource}",
                        status="warning",
                        error=f"unknown api group mapping for {resource}",
                    )
                )
                continue
            cmd = [*kubectl, "api-resources", f"--api-group={api_group}", "-o", "name"]
            api = subprocess.run(cmd, check=False, text=True, capture_output=True)
            found = resource in api.stdout
            checks.append(
                check_item(
                    f"api_resource:{resource}",
                    status="ok" if found else "error",
                    evidence=api.stdout.strip()[:200],
                    error="" if found else f"{resource} not found in api-group {api_group}",
                )
            )
            if not found:
                errors.append(f"{resource} not found in api-group {api_group}")

        pods = pod_readiness_probe(profile, namespace)
        checks.append(
            check_item(
                "pod_readiness",
                status="ok" if pods.get("ready") else "warning",
                evidence=json.dumps(pods),
            )
        )
    else:
        checks.append(check_item("kubectl", status="skipped", evidence="kubectl not in PATH"))

    try:
        fanout_nodes(machine, machine.get("candidate_nodes", []))
        if transport is not None:
            visible = transport.run(f"test -d {shell_quote(machine['mount_root'])}")
            checks.append(
                check_item(
                    "shared_hostpath_root",
                    status="ok" if visible.returncode == 0 else "error",
                    evidence=machine["mount_root"],
                )
            )
            if visible.returncode:
                errors.append("shared mount root not visible on login host")
    except Exception as exc:  # noqa: BLE001
        checks.append(check_item("parity_backend", status="error", error=str(exc)))
        errors.append(str(exc))

    inventory = load_inventory()
    inventory["machines"][alias]["last_verified_at"] = utc_now_iso()
    inventory["machines"][alias]["last_verify_errors"] = errors
    save_inventory(inventory)
    return emit(
        {
            "status": "error" if errors else "ok",
            "alias": alias,
            "checks": checks,
            "errors": errors,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
