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

from mws_functional import (  # noqa: E402
    compile_validation_spec,
    dispatch_validation_spec,
    validate_non_stream_response,
    validate_stream_response,
    write_validation_spec,
)
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_result import aggregate_result_status, build_result_envelope, emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, validation_run_dir  # noqa: E402
from mws_smoke import (  # noqa: E402
    PortForward,
    build_ssl_context,
    discover_coordinator_services,
    ensure_service_endpoints,
    request_json,
    resolve_validation_context,
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
    parser.add_argument("--request", required=True)
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--functional-run-id", default="")
    parser.add_argument("--request-timeout", type=float, default=600)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--prompt", default="Reply briefly with MOTOR_FUNCTIONAL_OK.")
    parser.add_argument("--api-key-env", default="MOTOR_FUNCTIONAL_API_KEY")
    parser.add_argument("--ca-file", default="")
    parser.add_argument("--infer-ca-file", default="")
    parser.add_argument("--client-cert-file", default="")
    parser.add_argument("--client-key-file", default="")
    parser.add_argument("--client-key-password-env", default="")
    parser.add_argument("--infer-tls-server-name", default="")
    args = parser.parse_args()

    alias = require_safe_id(args.machine, label="machine")
    deploy_run_id = require_safe_id(args.deploy_run_id, label="deploy_run_id")
    run_id = require_safe_id(
        args.functional_run_id.strip() or new_run_id("functional"),
        label="functional_run_id",
    )
    started_at = utc_now_iso()
    if args.request_timeout <= 0 or args.max_tokens <= 0:
        return emit_result(
            build_result_envelope(
                kind="motor-functional",
                run_id=run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=started_at,
                errors=["request_timeout and max_tokens must be positive"],
                status="failed",
            )
        )

    out_dir = validation_run_dir(run_id)
    run_path = out_dir / "run.json"
    if run_path.exists():
        return emit_result(
            build_result_envelope(
                kind="motor-functional",
                run_id=run_id,
                workflow_run_id="workflow-unset",
                checks=[],
                started_at=started_at,
                errors=[f"functional run already exists and is immutable: {run_path}"],
                status="failed",
            )
        )

    context: dict = {}
    services: dict = {}
    checks: list[dict] = []
    artifacts: list[dict[str, str]] = []
    infer_forward = None
    try:
        spec = compile_validation_spec(
            user_request=args.request,
            machine=alias,
            deploy_run_id=deploy_run_id,
            selected_features=args.feature or None,
            selected_cases=args.case or None,
        )
        spec_path = write_validation_spec(out_dir / "validation-spec.json", spec)
        artifacts.append({"path": _artifact_ref(spec_path)})

        context = resolve_validation_context(machine_alias=alias, deploy_run_id=deploy_run_id)
        context_path = out_dir / "context.json"
        atomic_write_json(context_path, context)
        artifacts.append({"path": _artifact_ref(context_path)})
        checks.append(
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

        inference_cases = [
            case for case in spec["cases"] if case.get("adapter") == "inference-http"
        ]
        handlers = {}
        if inference_cases:
            machine = get_machine(alias)
            progress("discovering Motor Coordinator inference Service")
            services = discover_coordinator_services(
                machine=machine,
                kube_context=context["kube_context"],
                namespace=context["namespace"],
                roles=("infer",),
            )
            endpoint_evidence = ensure_service_endpoints(
                machine=machine,
                kube_context=context["kube_context"],
                namespace=context["namespace"],
                services=services,
            )
            service_path = out_dir / "services.json"
            atomic_write_json(
                service_path,
                {"services": services, "endpoints": endpoint_evidence},
            )
            artifacts.append({"path": _artifact_ref(service_path)})
            checks.append(
                {
                    "name": "inference_service",
                    "status": "ok",
                    "message": "Coordinator inference Service has ready endpoints",
                    "evidence": endpoint_evidence,
                }
            )

            if context["api_key_enabled"]:
                api_key = os.environ.get(args.api_key_env, "")
                if not api_key:
                    raise WorkspaceStateError(
                        "inference request requires deployment API key; "
                        f"set plaintext key in {args.api_key_env}"
                    )
                auth_headers = {
                    context["api_key_header"]: f"{context['api_key_prefix']}{api_key}"
                }
            else:
                auth_headers = {}

            password = (
                os.environ.get(args.client_key_password_env)
                if args.client_key_password_env
                else None
            )
            infer_ssl = None
            if context["infer_tls_enabled"]:
                infer_ssl = build_ssl_context(
                    ca_file=args.infer_ca_file or args.ca_file,
                    client_cert_file=args.client_cert_file,
                    client_key_file=args.client_key_file,
                    password=password,
                )

            base_payload = {
                "model": context["model"],
                "prompt": args.prompt,
                "temperature": 0,
                "max_tokens": args.max_tokens,
            }
            infer_forward = PortForward(
                machine,
                context["kube_context"],
                context["namespace"],
                services["infer"]["name"],
                services["infer"]["port"],
            )

            def inference_http(case: dict, run_spec: dict) -> dict:
                del run_spec
                case_id = str(case["id"])
                stream = case_id == "inference-request.stream"
                if case_id not in {
                    "inference-request.non-stream",
                    "inference-request.stream",
                }:
                    raise WorkspaceStateError(f"unsupported inference request case: {case_id}")
                case_dir = out_dir / "cases" / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                payload = {**base_payload, "stream": stream}
                request_path = case_dir / "request.json"
                atomic_write_json(
                    request_path,
                    {
                        "endpoint": "/v1/completions",
                        "payload": payload,
                        "authentication": (
                            "enabled (redacted)" if context["api_key_enabled"] else "disabled"
                        ),
                    },
                )
                artifacts.append({"path": _artifact_ref(request_path)})
                progress(f"running Motor functional case {case_id}")
                response = request_json(
                    port=infer_forward.local_port,
                    path="/v1/completions",
                    method="POST",
                    payload=payload,
                    headers=auth_headers,
                    timeout=args.request_timeout,
                    tls=context["infer_tls_enabled"],
                    ssl_context=infer_ssl,
                    tls_server_name=args.infer_tls_server_name
                    or f"{services['infer']['name']}.{context['namespace']}.svc",
                )
                response_path = case_dir / ("response.sse" if stream else "response.json")
                _write_response(response_path, response)
                artifacts.append({"path": _artifact_ref(response_path)})
                summary = (
                    validate_stream_response(response)
                    if stream
                    else validate_non_stream_response(response)
                )
                return {
                    "status": "ok",
                    "message": f"{case_id} returned generated output",
                    "evidence": summary,
                    "artifact_refs": [
                        _artifact_ref(request_path),
                        _artifact_ref(response_path),
                    ],
                }

            handlers["inference-http"] = inference_http
            with infer_forward:
                checks.extend(dispatch_validation_spec(spec, handlers=handlers))
        else:
            checks.extend(dispatch_validation_spec(spec, handlers=handlers))
    except Exception as exc:  # noqa: BLE001
        checks.append(
            {
                "name": "functional_execution",
                "status": "error",
                "message": str(exc),
            }
        )

    if infer_forward is not None:
        port_forward_path = out_dir / "port-forward.log"
        port_forward_path.write_text(
            f"[infer]\n{getattr(infer_forward, 'log', '')}\n",
            encoding="utf-8",
        )
        artifacts.append({"path": _artifact_ref(port_forward_path)})

    status = aggregate_result_status(checks)
    warnings = [check["message"] for check in checks if check.get("status") == "warning"]
    errors = [
        check["message"]
        for check in checks
        if check.get("status") in {"error", "unavailable"}
    ]
    envelope = build_result_envelope(
        kind="motor-functional",
        run_id=run_id,
        workflow_run_id=str(context.get("workflow_run_id") or "workflow-unset"),
        checks=checks,
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-complete", "run_id": deploy_run_id}],
        warnings=warnings,
        errors=errors,
        artifacts=artifacts,
        status=status,
        extra={
            "machine": alias,
            "deploy_run_id": deploy_run_id,
            "config_run_id": context.get("config_run_id", ""),
            "namespace": context.get("namespace", ""),
            "features": spec.get("features", []) if "spec" in locals() else [],
            "services": services,
            "validation_run_dir": _artifact_ref(out_dir),
        },
    )
    atomic_write_json(run_path, envelope)
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
