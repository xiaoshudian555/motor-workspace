#!/usr/bin/env python3
"""Shared Motor validation target access and Coordinator readiness helpers."""

from __future__ import annotations

import http.client
import json
import ssl
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from mws_deploy import load_config_bundle, verify_bundle_digest
from mws_kubectl import KubectlRunner, RemoteKubectlPortForward, build_kubectl_runner
from mws_local_state import WorkspaceStateError, get_machine
from mws_run_state import load_deploy_run, load_run, relative_repo
from repo_paths import REPO_ROOT

COORDINATOR_PORTS = {"infer": 1025, "mgmt": 1026, "obs": 1027}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceStateError(f"cannot read JSON object from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{path} must contain a JSON object")
    return data


def resolve_model_name(user_config: dict[str, Any]) -> str:
    """Resolve the public model name using Motor's active engine sections."""
    names: list[str] = []
    for section_name in (
        "motor_engine_union_config",
        "motor_engine_prefill_config",
        "motor_engine_decode_config",
        "motor_engine_encode_config",
    ):
        section = user_config.get(section_name)
        if not isinstance(section, dict) or not section:
            continue
        engine_config = section.get("engine_config")
        if not isinstance(engine_config, dict):
            engine_config = {}
        name = engine_config.get("served_model_name") or engine_config.get("served-model-name")
        if not name:
            legacy = section.get("model_config")
            if isinstance(legacy, dict):
                name = legacy.get("model_name")
        if name:
            names.append(str(name))
    unique = list(dict.fromkeys(names))
    if not unique:
        raise WorkspaceStateError(
            "cannot resolve served model name from Motor union/prefill/decode/encode engine config"
        )
    if len(unique) != 1:
        raise WorkspaceStateError(f"Motor engine sections expose different model names: {unique}")
    return unique[0]


def _tls_config(user_config: dict[str, Any], name: str) -> dict[str, Any]:
    deploy = user_config.get("motor_deploy_config")
    if isinstance(deploy, dict):
        tls = deploy.get("tls_config")
        if isinstance(tls, dict) and isinstance(tls.get(name), dict):
            return dict(tls[name])
    coordinator = user_config.get("motor_coordinator_config")
    if isinstance(coordinator, dict) and isinstance(coordinator.get(name), dict):
        return dict(coordinator[name])
    return {}


def _api_key_config(user_config: dict[str, Any]) -> dict[str, Any]:
    coordinator = user_config.get("motor_coordinator_config")
    if not isinstance(coordinator, dict):
        return {}
    config = coordinator.get("api_key_config")
    return dict(config) if isinstance(config, dict) else {}


def _tracer_config(user_config: dict[str, Any]) -> dict[str, Any]:
    coordinator = user_config.get("motor_coordinator_config")
    if not isinstance(coordinator, dict):
        return {}
    config = coordinator.get("tracer_config")
    return dict(config) if isinstance(config, dict) else {}


def _endpoint_host(endpoint: str) -> str:
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}")
    return str(parsed.hostname or "")


def resolve_validation_context(*, machine_alias: str, deploy_run_id: str) -> dict[str, Any]:
    """Resolve and integrity-check a successful deploy plus its Motor request config."""
    deploy_run = load_deploy_run(deploy_run_id, allow_failed=False)
    recorded_machine = str(deploy_run.get("machine") or deploy_run.get("alias") or "")
    if recorded_machine != machine_alias:
        raise WorkspaceStateError(
            f"deploy run {deploy_run_id} is for {recorded_machine!r}, not {machine_alias!r}"
        )
    config_run_id = str(deploy_run.get("config_run_id") or "").strip()
    if not config_run_id:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing config_run_id")
    config_run = load_run("deploy-config-ready", config_run_id)
    bundle_ref = str(deploy_run.get("bundle_dir") or config_run.get("bundle_dir") or "").strip()
    if not bundle_ref:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing bundle_dir")
    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_dir
    bundle = load_config_bundle(bundle_dir)
    bundle_digest = str(deploy_run.get("bundle_digest") or config_run.get("bundle_digest") or "")
    verify_bundle_digest(bundle_dir, bundle_digest)
    user_config_path = bundle_dir / "user_config.json"
    if not user_config_path.exists():
        raise WorkspaceStateError(f"config bundle missing user_config.json: {bundle_dir}")
    user_config = _load_json_object(user_config_path)
    machine = get_machine(machine_alias)
    namespace = str(
        deploy_run.get("namespace") or config_run.get("namespace") or bundle.get("namespace") or ""
    ).strip()
    if not namespace:
        raise WorkspaceStateError(f"deploy run {deploy_run_id} missing namespace evidence")
    auth = _api_key_config(user_config)
    tracer = _tracer_config(user_config)
    trace_endpoint = str(tracer.get("endpoint") or "").strip()
    return {
        "machine": machine_alias,
        "deploy_run_id": deploy_run_id,
        "config_run_id": config_run_id,
        "workflow_run_id": str(deploy_run.get("workflow_run_id") or "workflow-unset"),
        "bundle_dir": relative_repo(bundle_dir),
        "bundle_digest": bundle_digest,
        "namespace": namespace,
        "kube_context": str(machine.get("kube_context") or ""),
        "model": resolve_model_name(user_config),
        "mgmt_tls_enabled": bool(_tls_config(user_config, "mgmt_tls_config").get("enable_tls")),
        "infer_tls_enabled": bool(_tls_config(user_config, "infer_tls_config").get("enable_tls")),
        "api_key_enabled": bool(auth.get("enable_api_key")),
        "api_key_header": str(auth.get("header_name") or "Authorization"),
        "api_key_prefix": str(auth.get("key_prefix") or "Bearer "),
        "tracing_enabled": bool(trace_endpoint),
        "tracing_export_host": _endpoint_host(trace_endpoint),
        "tracing_remote_parent_sampled": float(
            tracer.get("remote_parent_sampled", 1.0)
        ),
    }


def resolve_smoke_context(*, machine_alias: str, deploy_run_id: str) -> dict[str, Any]:
    """Compatibility name for the shared post-deploy validation context."""
    return resolve_validation_context(
        machine_alias=machine_alias,
        deploy_run_id=deploy_run_id,
    )


def _service_score(item: dict[str, Any], role: str) -> int:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    name = str(metadata.get("name") or "").lower()
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    selector = spec.get("selector") if isinstance(spec.get("selector"), dict) else {}
    searchable = " ".join([name, *map(str, labels.values()), *map(str, selector.values())]).lower()
    score = 0
    if "coordinator" in name:
        score += 6
    if "coordinator" in searchable:
        score += 4
    if role in name:
        score += 3
    if name == f"mindie-motor-coordinator-{role}":
        score += 10
    return score


def discover_coordinator_services(
    *,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    roles: tuple[str, ...] = ("infer", "mgmt"),
    kubectl: KubectlRunner | None = None,
) -> dict[str, dict[str, Any]]:
    """Find live Coordinator Services by their Motor port and role identity."""
    run_kubectl = kubectl or build_kubectl_runner(machine, kube_context=kube_context)
    result = run_kubectl("get", "services", "-n", namespace, "-o", "json")
    if result.returncode:
        raise WorkspaceStateError(result.stderr.strip() or "failed to list Kubernetes Services")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError("kubectl returned invalid Services JSON") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise WorkspaceStateError("kubectl Services response has no items array")

    resolved: dict[str, dict[str, Any]] = {}
    unknown_roles = [role for role in roles if role not in COORDINATOR_PORTS]
    if unknown_roles:
        raise WorkspaceStateError(f"unknown Coordinator service role(s): {unknown_roles}")
    for role in roles:
        expected_port = COORDINATOR_PORTS[role]
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
            ports = spec.get("ports") if isinstance(spec.get("ports"), list) else []
            matching = next(
                (
                    port
                    for port in ports
                    if isinstance(port, dict)
                    and port.get("protocol", "TCP") == "TCP"
                    and port.get("port") == expected_port
                ),
                None,
            )
            if matching is None:
                continue
            name = str(item.get("metadata", {}).get("name") or "")
            score = _service_score(item, role)
            if name and score > 0:
                candidates.append((score, name, matching))
        if not candidates:
            raise WorkspaceStateError(
                f"cannot find Motor Coordinator {role} Service on port {expected_port} in namespace {namespace}"
            )
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            tied = [name for score, name, _ in candidates if score == candidates[0][0]]
            raise WorkspaceStateError(f"ambiguous Coordinator {role} Services: {tied}")
        _, name, port_spec = candidates[0]
        resolved[role] = {
            "name": name,
            "port": expected_port,
            "target_port": port_spec.get("targetPort", expected_port),
        }
    return resolved


def ensure_service_endpoints(
    *,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    services: dict[str, dict[str, Any]],
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    run_kubectl = kubectl or build_kubectl_runner(machine, kube_context=kube_context)
    evidence: dict[str, Any] = {}
    for role, service in services.items():
        name = str(service["name"])
        result = run_kubectl("get", "endpoints", name, "-n", namespace, "-o", "json")
        if result.returncode:
            raise WorkspaceStateError(
                result.stderr.strip() or f"failed to inspect endpoints for Service {name}"
            )
        try:
            endpoint = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise WorkspaceStateError(f"invalid endpoints JSON for Service {name}") from exc
        subsets = endpoint.get("subsets") if isinstance(endpoint, dict) else None
        addresses = sum(
            len(subset.get("addresses", []))
            for subset in subsets or []
            if isinstance(subset, dict) and isinstance(subset.get("addresses", []), list)
        )
        if addresses < 1:
            raise WorkspaceStateError(f"Service {name} has no ready endpoint addresses")
        evidence[role] = {"service": name, "ready_addresses": addresses}
    return evidence


PortForward = RemoteKubectlPortForward


class _ServerNameHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, *, context: ssl.SSLContext, server_name: str, timeout: float):
        super().__init__(host, port, context=context, timeout=timeout)
        self._smoke_server_name = server_name

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._smoke_server_name)


def build_ssl_context(
    *, ca_file: str = "", client_cert_file: str = "", client_key_file: str = "", password: str | None = None
) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_file or None)
    if bool(client_cert_file) != bool(client_key_file):
        raise WorkspaceStateError("both client certificate and key files are required for mutual TLS")
    if client_cert_file:
        context.load_cert_chain(client_cert_file, client_key_file, password=password)
    return context


def request_json(
    *,
    port: int,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 600,
    tls: bool = False,
    ssl_context: ssl.SSLContext | None = None,
    tls_server_name: str = "localhost",
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    if tls:
        connection: http.client.HTTPConnection = _ServerNameHTTPSConnection(
            "127.0.0.1",
            port,
            context=ssl_context or build_ssl_context(),
            server_name=tls_server_name,
            timeout=timeout,
        )
    else:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WorkspaceStateError(f"response from {path} exceeds {MAX_RESPONSE_BYTES} bytes")
        return {
            "status": response.status,
            "content_type": response.getheader("Content-Type", ""),
            "body": raw.decode("utf-8", errors="replace"),
        }
    finally:
        connection.close()


def wait_for_readiness(
    request: Callable[[], dict[str, Any]], *, timeout: float, interval: float = 2.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {"status": 0, "body": "no response"}
    while time.monotonic() < deadline:
        try:
            last = request()
            body = json.loads(str(last.get("body") or "{}"))
            if last.get("status") == 200 and isinstance(body, dict) and body.get("ready") is True:
                return {**last, "json": body}
        except (OSError, TimeoutError, json.JSONDecodeError, http.client.HTTPException) as exc:
            last = {"status": 0, "body": str(exc)}
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise WorkspaceStateError(
        f"Coordinator readiness did not reach ready=true within {timeout:g}s; "
        f"last_status={last.get('status')} last_body={str(last.get('body'))[:500]}"
    )
