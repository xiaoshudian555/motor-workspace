#!/usr/bin/env python3
"""Release-grade Motor wheel build helpers (TD-P2-07).

The daily Python loop uses fixed shared hostPath + PYTHONPATH; that fast path
cannot provide protobuf-generated code (``*_pb2.py``) or the Rust kv-conductor
binary. This module implements the **build path**: build a ``motor`` wheel from
the motor source tree, always inside a Docker container based on the runtime
image so the produced wheel is ABI-compatible with the deployed environment.

Rules (hard):
- Wheel builds MUST run inside Docker; the local WSL host lacks the CANN,
  grpcio-tools, and Rust toolchains and would produce a non-runtime wheel.
- The build container image defaults to the runtime ``base_image_ref`` so the
  wheel targets the exact same Python / CANN / libc environment as the Pods.
- The build root MUST be a fixed shared path under the machine mount root so the
  produced wheel can be consumed by deployment (hostPath into Pods).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from mws_execution import ExecutionAdapter, execution_adapter_for_machine
from mws_local_state import WorkspaceStateError
from mws_machine_target import build_fixed_source_paths
from mws_result import utc_now_iso
from mws_transport import shell_quote

MOTOR_SOURCE_SUBDIR = "motor"
KV_CONDUCTOR_REL = PurePosixPath("motor") / "kv_conductor" / "bin" / "kv-conductor"
BUILD_SCRIPT_REL = "build.sh"
WHEEL_GLOB = "motor-*.whl"

# Build outputs live under the shared mount root so Pods can reach them via
# hostPath without shipping a second artifact copy.
BUILD_OUTPUT_SUBDIR = "motor-wheel-builds"


def motor_source_root(machine: dict[str, Any]) -> str:
    paths = build_fixed_source_paths(machine)
    return str(paths["motor_source"]).rstrip("/")


def build_output_root(machine: dict[str, Any]) -> str:
    paths = build_fixed_source_paths(machine)
    return f"{str(paths['remote_workspace_root']).rstrip('/')}/{BUILD_OUTPUT_SUBDIR}"


def wheel_dist_dir(machine: dict[str, Any], source_sha: str) -> str:
    """Shared dist/ directory holding motor-*.whl for MOTOR_WHEEL_DIR / boot.sh."""
    normalized = re.sub(r"[^0-9a-fA-F]", "", str(source_sha))
    if len(normalized) < 8:
        raise WorkspaceStateError("source_sha must be a git commit sha (>=8 hex chars)")
    return f"{build_output_root(machine)}/{normalized}/dist"


def motor_wheel_dir_from_build_run(run: dict[str, Any]) -> str:
    """Resolve MOTOR_WHEEL_DIR from a motor-wheel-build run envelope."""
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    for key in ("wheel_dir",):
        value = extra.get(key) or run.get(key)
        if value:
            return str(value).rstrip("/")
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), list) else []
    for item in artifacts:
        if isinstance(item, dict) and item.get("name") == "motor-wheel" and item.get("path"):
            return str(item["path"]).rstrip("/")
    raise WorkspaceStateError("motor-wheel-build run missing wheel_dir artifact")


def detect_build_gaps(source_root: str) -> dict[str, Any]:
    """Detect artifacts the fast path (hostPath/PYTHONPATH) cannot provide.

    Returns a list of gap records. A non-empty ``missing`` list means the source
    tree is NOT self-sufficient via the daily Python loop and the build path must
    be used.
    """
    root = Path(source_root)
    missing: list[dict[str, str]] = []

    proto_files = list(root.rglob("*.proto"))
    if proto_files:
        missing_pb2: list[str] = []
        for proto in proto_files:
            pb2 = Path(str(proto)[:-6] + "_pb2.py")
            if not pb2.exists():
                missing_pb2.append(str(proto.relative_to(root)))
        if missing_pb2:
            missing.append(
                {
                    "artifact": "protobuf-generated",
                    "reason": f"{len(missing_pb2)} .proto without generated _pb2.py",
                    "detail": "; ".join(missing_pb2[:5]),
                    "path": "build path (docker build.sh runs generate_proto.sh)",
                }
            )

    kv_bin = root / KV_CONDUCTOR_REL
    if not kv_bin.exists():
        missing.append(
            {
                "artifact": "kv-conductor",
                "reason": "Rust kv-conductor binary not found in source tree",
                "detail": str(KV_CONDUCTOR_REL),
                "path": "build path (docker build.sh runs cargo build)",
            }
        )

    build_script = root / BUILD_SCRIPT_REL
    if not build_script.exists():
        missing.append(
            {
                "artifact": "build.sh",
                "reason": "motor build.sh missing",
                "detail": str(build_script),
                "path": "cannot build wheel without upstream build.sh",
            }
        )

    return {
        "source_root": source_root,
        "missing": missing,
        "build_required": bool(missing),
    }


def _remote_wheel_exists(adapter: ExecutionAdapter, build_dir: str) -> bool:
    """True when a completed wheel build already exists remotely.

    The build is keyed by source sha, so a marker file plus a wheel file on the
    shared dir is sufficient to declare the artifact reusable.
    """
    probe = adapter.run(
        f"test -f {shell_quote(build_dir)}/wheel.sha256 && "
        f"ls {shell_quote(build_dir)}/dist/motor-*.whl >/dev/null 2>&1 && echo WHEEL_OK"
    )
    return probe.returncode == 0 and "WHEEL_OK" in probe.stdout


def build_motor_wheel_in_docker(
    *,
    machine: dict[str, Any],
    base_image_ref: str,
    source_sha: str,
    reuse: bool = True,
) -> dict[str, Any]:
    """Build a ``motor`` wheel inside a Docker container on the machine host.

    The container is based on the runtime image and mounts the already-synced
    fixed motor source tree (read-only) plus a fixed shared build output
    directory. Inside the container it runs the upstream ``build.sh`` (which
    generates protobuf files and builds the Rust kv-conductor binary) and copies
    the resulting ``motor-*.whl`` into the shared output dir.

    Returns a build record with the remote wheel path, sha256, container image
    and source sha. When ``reuse`` is true and a wheel with the same source sha
    already exists, the build is skipped (idempotent).
    """
    if not base_image_ref or base_image_ref == "UNRESOLVED":
        raise WorkspaceStateError(
            "base_image_ref is required to build a motor wheel; set runtime.base_image_ref "
            "in workspace.lock.yaml or motor_deploy_config.image_name"
        )

    source_root = motor_source_root(machine)
    output_root = build_output_root(machine)
    source_sha = re.sub(r"[^0-9a-fA-F]", "", str(source_sha))
    if len(source_sha) < 8:
        raise WorkspaceStateError("source_sha must be a git commit sha (>=8 hex chars)")

    gaps = detect_build_gaps(source_root)
    build_dir = f"{output_root}/{source_sha}"
    remote_wheel_dir = f"{build_dir}/dist"
    wheel_digest = f"mws-motor-wheel-{source_sha[:12]}"

    adapter = execution_adapter_for_machine(machine)

    if reuse and _remote_wheel_exists(adapter, build_dir):
        return _build_record(
            machine=machine,
            source_root=source_root,
            base_image_ref=base_image_ref,
            source_sha=source_sha,
            build_dir=build_dir,
            wheel_digest=wheel_digest,
            reused=True,
            status="ok",
        )

    if not gaps.get("build_required"):
        # Nothing to build: the fast path is sufficient.
        return _build_record(
            machine=machine,
            source_root=source_root,
            base_image_ref=base_image_ref,
            source_sha=source_sha,
            build_dir=build_dir,
            wheel_digest=wheel_digest,
            reused=False,
            status="ok",
            message="source tree has no pb2/Rust gaps; fast path sufficient",
        )

    probe = adapter.run(
        f"test -f {shell_quote(source_root + '/build.sh')} && echo OK && "
        f"command -v docker >/dev/null 2>&1 && echo DOCKER_OK"
    )
    if probe.returncode != 0 or "OK" not in probe.stdout:
        raise WorkspaceStateError(
            "docker or upstream build.sh unavailable on machine host for wheel build"
        )
    if "DOCKER_OK" not in probe.stdout:
        raise WorkspaceStateError("docker CLI is not available on the machine host")

    adapter.mkdir(remote_wheel_dir)

    # Run build.sh inside a runtime-based container. The motor source is mounted
    # read-only; build.sh writes generated pb2 / kv-conductor binary into its own
    # copy inside the container, and only the final wheel is copied to the shared
    # output dir so the fixed source tree is never mutated.
    inner_build = (
        "set -euo pipefail; "
        f"cp -r {shell_quote('/src/motor')} /work/motor; "
        "cd /work/motor; "
        "bash build.sh; "
        "cp dist/motor-*.whl /out/ 2>/dev/null || { echo 'no wheel produced' >&2; exit 1; }; "
        "echo BUILD_DONE"
    )
    docker_cmd = (
        f"docker run --rm --network=host "
        f"-v {shell_quote(source_root)}:/src/motor:ro "
        f"-v {shell_quote(remote_wheel_dir)}:/out "
        f"-w /work "
        f"{shell_quote(base_image_ref)} "
        f"bash -c {shell_quote(inner_build)}"
    )
    result = adapter.run(docker_cmd)
    if result.returncode != 0 or "BUILD_DONE" not in result.stdout:
        raise WorkspaceStateError(
            "docker motor wheel build failed: "
            + (result.stderr[-4000:] or result.stdout[-4000:])
        )

    # Compute sha256 of the produced wheel on the remote and persist a marker.
    marker_script = (
        f"cd {shell_quote(remote_wheel_dir)} && "
        "for f in motor-*.whl; do "
        "sha256sum \"$f\" | awk -v n=\"$f\" '{print $1 \"  \" n}' "
        "> {shell_quote(build_dir)}/wheel.sha256; "
        "done"
    )
    adapter.run(marker_script)

    return _build_record(
        machine=machine,
        source_root=source_root,
        base_image_ref=base_image_ref,
        source_sha=source_sha,
        build_dir=build_dir,
        wheel_digest=wheel_digest,
        reused=False,
        status="ok",
    )


def _build_record(
    *,
    machine: dict[str, Any],
    source_root: str,
    base_image_ref: str,
    source_sha: str,
    build_dir: str,
    wheel_digest: str,
    reused: bool,
    status: str,
    message: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "reused": reused,
        "source_root": source_root,
        "source_sha": source_sha,
        "base_image_ref": base_image_ref,
        "build_dir": build_dir,
        "wheel_digest": wheel_digest,
        "wheel_dir": f"{build_dir}/dist",
        "built_at": utc_now_iso(),
        "machine": machine.get("alias") or machine.get("host"),
    }


def _inject_wheel_pythonpath(pythonpath: str, wheel_dir: str) -> str:
    """Prepend an extracted-wheel site dir to PYTHONPATH.

    When a wheel is available we prefer the installed package; the caller is
    responsible for actually installing/extracting it. This is a helper for the
    replace step (used by the skill).
    """
    if not wheel_dir:
        return pythonpath
    return f"{wheel_dir}:{pythonpath}" if pythonpath else wheel_dir


def render_wheel_replace_manifest(
    *,
    wheel_dir: str,
    namespace: str,
    container: str,
    image: str,
    replace_path: str,
) -> dict[str, Any]:
    """Render an ephemeral Job that pip-installs the built wheel.

    This is the ``replace`` half of the build path: the wheel built in Docker is
    installed into a fresh runtime container so the Pods' runtime code is the
    wheel build (protobuf + Rust included), not the raw source tree.

    Returns a single-document manifest dict for a namespaced Job. The wheel dir
    is expected to live on the shared mount root so the Job can mount it as a
    hostPath volume.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "mws-wheel-replace", "namespace": namespace},
        "spec": {
            "backoffLimit": 2,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "wheel-replace",
                            "image": image,
                            "command": [
                                "bash",
                                "-c",
                                f"pip install --no-cache-dir --force-reinstall "
                                f"{replace_path}/motor-*.whl",
                            ],
                            "volumeMounts": [
                                {
                                    "name": "wheel-store",
                                    "mountPath": replace_path,
                                    "readOnly": True,
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "wheel-store",
                            "hostPath": {"path": wheel_dir, "type": "Directory"},
                        }
                    ],
                }
            },
        },
    }


def build_wheel_run_envelope(
    *,
    run_id: str,
    workflow_run_id: str,
    build_result: dict[str, Any],
    started_at: str,
    upstream_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Wrap a wheel build result into an mws.result.v1 envelope for the skill."""
    from mws_result import build_result_envelope

    checks: list[dict[str, Any]] = [
        {
            "name": "wheel_build",
            "status": "ok" if build_result.get("status") == "ok" else "error",
            "message": build_result.get("message") or "motor wheel built in docker",
        }
    ]
    artifacts: list[dict[str, Any]] = []
    if build_result.get("wheel_dir"):
        artifacts.append(
            {
                "name": "motor-wheel",
                "path": build_result["wheel_dir"],
                "source_sha": build_result.get("source_sha", ""),
                "base_image_ref": build_result.get("base_image_ref", ""),
            }
        )
    extra = {
        "source_sha": build_result.get("source_sha"),
        "base_image_ref": build_result.get("base_image_ref"),
        "wheel_dir": build_result.get("wheel_dir"),
        "build_dir": build_result.get("build_dir"),
        "reused": build_result.get("reused", False),
    }
    if build_result.get("machine"):
        extra["machine"] = build_result["machine"]
    return build_result_envelope(
        kind="motor-wheel-build",
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        checks=checks,
        started_at=started_at,
        upstream_refs=upstream_refs,
        artifacts=artifacts,
        extra=extra,
    )
