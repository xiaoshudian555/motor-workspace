#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_execution import ServiceTarget, execution_adapter_for_machine  # noqa: E402
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_result import CheckRunner, build_result_envelope, emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, validation_run_dir  # noqa: E402
from mws_smoke import (  # noqa: E402
    build_ssl_context,
    discover_coordinator_services,
    ensure_service_endpoints,
    request_json,
    resolve_smoke_context,
    wait_for_readiness,
)
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def _artifact_ref(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--smoke-run-id", default="")
    parser.add_argument("--ready-timeout", type=float, default=600)
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--ca-file", default="")
    parser.add_argument("--mgmt-ca-file", default="")
    parser.add_argument("--client-cert-file", default="")
    parser.add_argument("--client-key-file", default="")
    parser.add_argument("--client-key-password-env", default="")
    parser.add_argument("--mgmt-tls-server-name", default="")
    args = parser.parse_args()

    alias = require_safe_id(args.machine, label="machine")
    deploy_run_id = require_safe_id(args.deploy_run_id, label="deploy_run_id")
    smoke_run_id = require_safe_id(
        args.smoke_run_id.strip() or new_run_id("smoke"), label="smoke_run_id"
    )
    started_at = utc_now_iso()
    if args.ready_timeout <= 0 or args.request_timeout <= 0:
        return emit_result(
            build_result_envelope(
                kind="motor-smoke",
                run_id=smoke_run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=started_at,
                errors=["timeouts must be positive"],
                status="failed",
            )
        )

    runner = CheckRunner()
    out_dir = validation_run_dir(smoke_run_id)
    run_path = out_dir / "run.json"
    if run_path.exists():
        return emit_result(
            build_result_envelope(
                kind="motor-smoke",
                run_id=smoke_run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=started_at,
                errors=[f"smoke run already exists and is immutable: {run_path}"],
                status="failed",
            )
        )

    context: dict = {}
    services: dict = {}
    artifacts: list[dict[str, str]] = []
    mgmt_forward = None
    try:
        context = resolve_smoke_context(machine_alias=alias, deploy_run_id=deploy_run_id)
        context_path = out_dir / "context.json"
        atomic_write_json(context_path, context)
        artifacts.append({"path": _artifact_ref(context_path)})
        runner.append(
            {
                "name": "deploy_context",
                "status": "ok",
                "message": "ready deploy/config/bundle chain verified",
                "evidence": {
                    "deploy_run_id": deploy_run_id,
                    "config_run_id": context["config_run_id"],
                    "bundle_digest": context["bundle_digest"],
                },
            }
        )

        password = os.environ.get(args.client_key_password_env) if args.client_key_password_env else None
        mgmt_ssl = None
        if context["mgmt_tls_enabled"]:
            mgmt_ssl = build_ssl_context(
                ca_file=args.mgmt_ca_file or args.ca_file,
                client_cert_file=args.client_cert_file,
                client_key_file=args.client_key_file,
                password=password,
            )

        progress("discovering Motor Coordinator management Service")
        machine = get_machine(alias)
        services = discover_coordinator_services(
            machine=machine,
            kube_context=context["kube_context"],
            namespace=context["namespace"],
            roles=("mgmt",),
        )
        endpoint_evidence = ensure_service_endpoints(
            machine=machine,
            kube_context=context["kube_context"],
            namespace=context["namespace"],
            services=services,
        )
        service_path = out_dir / "services.json"
        atomic_write_json(service_path, {"services": services, "endpoints": endpoint_evidence})
        artifacts.append({"path": _artifact_ref(service_path)})
        runner.append(
            {
                "name": "coordinator_service",
                "status": "ok",
                "message": "Coordinator management Service has ready endpoints",
                "evidence": endpoint_evidence,
            }
        )

        if context.get("executor", "ssh") == "native":
            cluster_ip = str(services["mgmt"].get("cluster_ip") or "")
            if not cluster_ip:
                raise WorkspaceStateError(
                    "native smoke requires a ClusterIP Service, but mgmt Service has no "
                    f"clusterIP; service={services['mgmt']['name']}"
                )
        progress("opening Coordinator management Service access")
        adapter = execution_adapter_for_machine(machine)
        target = ServiceTarget(
            namespace=context["namespace"],
            service_name=services["mgmt"]["name"],
            service_port=services["mgmt"]["port"],
            kube_context=context["kube_context"],
            cluster_ip=services["mgmt"].get("cluster_ip", ""),
        )
        with adapter.port_forward(target) as mgmt_forward:
            progress("waiting for Coordinator readiness body ready=true")
            readiness = wait_for_readiness(
                lambda: request_json(
                    host=mgmt_forward.target_host,
                    port=mgmt_forward.local_port,
                    path="/readiness",
                    timeout=args.request_timeout,
                    tls=context["mgmt_tls_enabled"],
                    ssl_context=mgmt_ssl,
                    tls_server_name=args.mgmt_tls_server_name
                    or f"{services['mgmt']['name']}.{context['namespace']}.svc",
                ),
                timeout=args.ready_timeout,
            )
        readiness_path = out_dir / "readiness.json"
        atomic_write_json(readiness_path, readiness)
        artifacts.append({"path": _artifact_ref(readiness_path)})
        runner.append(
            {
                "name": "coordinator_readiness",
                "status": "ok",
                "message": "Coordinator GET /readiness returned ready=true",
                "evidence": readiness.get("json", {}),
            }
        )
    except Exception as exc:  # noqa: BLE001
        if runner.continue_ok:
            runner.append({"name": "smoke_execution", "status": "error", "message": str(exc)})

    if mgmt_forward is not None:
        port_forward_path = out_dir / "port-forward.log"
        port_forward_path.write_text(
            f"[mgmt]\n{getattr(mgmt_forward, 'log', '')}\n",
            encoding="utf-8",
        )
        artifacts.append({"path": _artifact_ref(port_forward_path)})

    envelope = build_result_envelope(
        kind="motor-smoke",
        run_id=smoke_run_id,
        workflow_run_id=str(context.get("workflow_run_id") or "workflow-unset"),
        checks=runner.checks,
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-complete", "run_id": deploy_run_id}],
        warnings=runner.warnings,
        errors=runner.errors,
        artifacts=artifacts,
        status="ready" if runner.continue_ok else "failed",
        extra={
            "machine": alias,
            "deploy_run_id": deploy_run_id,
            "config_run_id": context.get("config_run_id", ""),
            "namespace": context.get("namespace", ""),
            "services": services,
            "validation_run_dir": _artifact_ref(out_dir),
        },
    )
    atomic_write_json(run_path, envelope)
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
