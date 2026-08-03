#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_result import CheckRunner, build_result_envelope, emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, validation_run_dir  # noqa: E402
from mws_smoke import (  # noqa: E402
    PortForward,
    build_ssl_context,
    discover_coordinator_services,
    ensure_service_endpoints,
    request_json,
    resolve_smoke_context,
    validate_non_stream_response,
    validate_stream_response,
    wait_for_readiness,
)
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def _artifact_ref(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _write_response(path: Path, response: dict) -> None:
    path.write_text(str(response.get("body") or ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--smoke-run-id", default="")
    parser.add_argument("--ready-timeout", type=float, default=600)
    parser.add_argument("--request-timeout", type=float, default=600)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--prompt", default="Reply briefly with MOTOR_SMOKE_OK.")
    parser.add_argument("--api-key-env", default="MOTOR_SMOKE_API_KEY")
    parser.add_argument("--ca-file", default="")
    parser.add_argument("--mgmt-ca-file", default="")
    parser.add_argument("--infer-ca-file", default="")
    parser.add_argument("--client-cert-file", default="")
    parser.add_argument("--client-key-file", default="")
    parser.add_argument("--client-key-password-env", default="")
    parser.add_argument("--mgmt-tls-server-name", default="")
    parser.add_argument("--infer-tls-server-name", default="")
    args = parser.parse_args()

    alias = require_safe_id(args.machine, label="machine")
    deploy_run_id = require_safe_id(args.deploy_run_id, label="deploy_run_id")
    smoke_run_id = require_safe_id(
        args.smoke_run_id.strip() or new_run_id("smoke"), label="smoke_run_id"
    )
    if args.ready_timeout <= 0 or args.request_timeout <= 0:
        return emit_result(
            build_result_envelope(
                kind="motor-smoke",
                run_id=smoke_run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=utc_now_iso(),
                errors=["timeouts must be positive"],
                status="failed",
            )
        )
    if args.max_tokens <= 0:
        return emit_result(
            build_result_envelope(
                kind="motor-smoke",
                run_id=smoke_run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=utc_now_iso(),
                errors=["max_tokens must be positive"],
                status="failed",
            )
        )

    started_at = utc_now_iso()
    runner = CheckRunner()
    out_dir = validation_run_dir(smoke_run_id)
    run_path = out_dir / "run.json"
    if run_path.exists():
        envelope = build_result_envelope(
            kind="motor-smoke",
            run_id=smoke_run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=[f"smoke run already exists and is immutable: {run_path}"],
            status="failed",
        )
        return emit_result(envelope)

    context: dict = {}
    services: dict = {}
    artifacts: list[dict[str, str]] = []
    mgmt_forward = None
    infer_forward = None
    try:
        context = resolve_smoke_context(machine_alias=alias, deploy_run_id=deploy_run_id)
        safe_context = dict(context)
        context_path = out_dir / "context.json"
        atomic_write_json(context_path, safe_context)
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

        if context["api_key_enabled"]:
            api_key = os.environ.get(args.api_key_env, "")
            if not api_key:
                raise WorkspaceStateError(
                    f"Motor API-key authentication is enabled; set plaintext key in {args.api_key_env}"
                )
            auth_headers = {
                context["api_key_header"]: f"{context['api_key_prefix']}{api_key}"
            }
        else:
            auth_headers = {}

        password = os.environ.get(args.client_key_password_env) if args.client_key_password_env else None
        mgmt_ssl = None
        infer_ssl = None
        if context["mgmt_tls_enabled"]:
            mgmt_ssl = build_ssl_context(
                ca_file=args.mgmt_ca_file or args.ca_file,
                client_cert_file=args.client_cert_file,
                client_key_file=args.client_key_file,
                password=password,
            )
        if context["infer_tls_enabled"]:
            infer_ssl = build_ssl_context(
                ca_file=args.infer_ca_file or args.ca_file,
                client_cert_file=args.client_cert_file,
                client_key_file=args.client_key_file,
                password=password,
            )

        progress("discovering Motor Coordinator Services")
        machine = get_machine(alias)
        services = discover_coordinator_services(
            machine=machine,
            kube_context=context["kube_context"],
            namespace=context["namespace"],
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
                "name": "coordinator_services",
                "status": "ok",
                "message": "Coordinator inference and management Services have ready endpoints",
                "evidence": endpoint_evidence,
            }
        )

        with ExitStack() as stack:
            mgmt_forward = stack.enter_context(
                PortForward(
                    machine,
                    context["kube_context"],
                    context["namespace"],
                    services["mgmt"]["name"],
                    services["mgmt"]["port"],
                )
            )
            infer_forward = stack.enter_context(
                PortForward(
                    machine,
                    context["kube_context"],
                    context["namespace"],
                    services["infer"]["name"],
                    services["infer"]["port"],
                )
            )
            progress("waiting for Motor readiness body ready=true")
            readiness = wait_for_readiness(
                lambda: request_json(
                    port=mgmt_forward.local_port,
                    path="/readiness",
                    timeout=min(args.request_timeout, 30),
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
                    "name": "motor_readiness",
                    "status": "ok",
                    "message": "GET /readiness returned ready=true",
                    "evidence": readiness.get("json", {}),
                }
            )

            base_payload = {
                "model": context["model"],
                "prompt": args.prompt,
                "temperature": 0,
                "max_tokens": args.max_tokens,
            }
            request_path = out_dir / "requests.json"
            atomic_write_json(
                request_path,
                {
                    "endpoint": "/v1/completions",
                    "non_stream": {**base_payload, "stream": False},
                    "stream": {**base_payload, "stream": True},
                    "authentication": "enabled (redacted)" if context["api_key_enabled"] else "disabled",
                },
            )
            artifacts.append({"path": _artifact_ref(request_path)})

            progress("running non-stream Motor inference smoke")
            non_stream = request_json(
                port=infer_forward.local_port,
                path="/v1/completions",
                method="POST",
                payload={**base_payload, "stream": False},
                headers=auth_headers,
                timeout=args.request_timeout,
                tls=context["infer_tls_enabled"],
                ssl_context=infer_ssl,
                tls_server_name=args.infer_tls_server_name
                or f"{services['infer']['name']}.{context['namespace']}.svc",
            )
            non_stream_path = out_dir / "non-stream-response.json"
            _write_response(non_stream_path, non_stream)
            artifacts.append({"path": _artifact_ref(non_stream_path)})
            non_stream_summary = validate_non_stream_response(non_stream)
            runner.append(
                {
                    "name": "non_stream_inference",
                    "status": "ok",
                    "message": "real non-stream inference returned generated output",
                    "evidence": non_stream_summary,
                }
            )

            progress("running streaming Motor inference smoke")
            stream = request_json(
                port=infer_forward.local_port,
                path="/v1/completions",
                method="POST",
                payload={**base_payload, "stream": True},
                headers=auth_headers,
                timeout=args.request_timeout,
                tls=context["infer_tls_enabled"],
                ssl_context=infer_ssl,
                tls_server_name=args.infer_tls_server_name
                or f"{services['infer']['name']}.{context['namespace']}.svc",
            )
            stream_path = out_dir / "stream-response.sse"
            _write_response(stream_path, stream)
            artifacts.append({"path": _artifact_ref(stream_path)})
            stream_summary = validate_stream_response(stream)
            runner.append(
                {
                    "name": "stream_inference",
                    "status": "ok",
                    "message": "real streaming inference returned output and data: [DONE]",
                    "evidence": stream_summary,
                }
            )
    except Exception as exc:  # noqa: BLE001
        if runner.continue_ok:
            runner.append({"name": "smoke_execution", "status": "error", "message": str(exc)})

    if mgmt_forward is not None or infer_forward is not None:
        port_forward_path = out_dir / "port-forward.log"
        port_forward_path.write_text(
            f"[mgmt]\n{getattr(mgmt_forward, 'log', '')}\n"
            f"[infer]\n{getattr(infer_forward, 'log', '')}\n",
            encoding="utf-8",
        )
        artifacts.append({"path": _artifact_ref(port_forward_path)})

    status = "ready" if runner.continue_ok else "failed"
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
        status=status,
        extra={
            "machine": alias,
            "deploy_run_id": deploy_run_id,
            "config_run_id": context.get("config_run_id", ""),
            "namespace": context.get("namespace", ""),
            "model": context.get("model", ""),
            "services": services,
            "validation_run_dir": _artifact_ref(out_dir),
        },
    )
    atomic_write_json(run_path, envelope)
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
