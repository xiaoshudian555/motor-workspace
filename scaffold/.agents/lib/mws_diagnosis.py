#!/usr/bin/env python3
"""Deploy diagnosis helpers (post-deploy evidence collection)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mws_deploy import load_config_bundle, verify_bundle_digest
from mws_local_state import WorkspaceStateError, get_machine
from mws_run_state import load_deploy_run, load_run
from repo_paths import REPO_ROOT


def resolve_diagnosis_context(
    *,
    machine_alias: str,
    deploy_run_id: str,
) -> dict[str, Any]:
    run = load_deploy_run(deploy_run_id)
    if str(run.get("machine") or run.get("alias") or "") != machine_alias:
        raise WorkspaceStateError(
            f"deploy run {deploy_run_id} is for {run.get('machine')!r}, not {machine_alias!r}"
        )

    config_run_id = str(run.get("config_run_id") or "").strip()
    if not config_run_id:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing config_run_id")

    config_run = load_run("deploy-config-ready", config_run_id)
    bundle_ref = str(run.get("bundle_dir") or config_run.get("bundle_dir") or "").strip()
    if not bundle_ref:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing bundle_dir")

    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_ref
    bundle = load_config_bundle(bundle_dir)

    expected_digest = str(run.get("bundle_digest") or config_run.get("bundle_digest") or "")
    if expected_digest:
        verify_bundle_digest(bundle_dir, expected_digest)

    machine = get_machine(machine_alias)
    kube_context = str(machine.get("kube_context") or "")
    namespace = str(
        run.get("namespace") or config_run.get("namespace") or bundle.get("namespace") or ""
    ).strip()
    if not namespace:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing namespace evidence")

    workload_names = list(bundle.get("workload_names") or config_run.get("workload_names") or [])
    return {
        "machine_alias": machine_alias,
        "deploy_run_id": deploy_run_id,
        "config_run_id": config_run_id,
        "bundle_dir": str(bundle_dir.relative_to(REPO_ROOT)) if bundle_dir.is_relative_to(REPO_ROOT) else str(bundle_dir),
        "bundle_digest": expected_digest,
        "kube_context": kube_context,
        "namespace": namespace,
        "workload_names": workload_names,
        "deploy_status": str(run.get("status") or ""),
    }
