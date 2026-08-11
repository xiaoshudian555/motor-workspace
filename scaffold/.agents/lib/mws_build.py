#!/usr/bin/env python3
"""Release-grade Motor wheel build helpers (TD-P2-07).

Motor runtime replacement requires protobuf-generated code (``*_pb2.py``) and
the Rust kv-conductor binary. This module builds a complete ``motor`` wheel from
the motor source tree inside a Docker container based on the runtime image, then
writes that wheel directory into the fixed remote ``boot.sh`` used by deploy.

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


def detect_build_gaps(source_root: str) -> dict[str, Any]:
    """Detect artifacts the parity source tree cannot provide at runtime.

    Returns a list of gap records. A non-empty ``missing`` list means the source
    tree is not directly importable at runtime (source-tree PYTHONPATH is
    forbidden), so the artifacts must come from the built wheel.
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

    The build is keyed by source sha. Reuse only a marker plus exactly one wheel
    so boot.sh cannot receive an ambiguous wheel directory.
    """
    probe = adapter.run(
        f"test -f {shell_quote(build_dir)}/wheel.sha256 && "
        f"set -- {shell_quote(build_dir)}/dist/motor-*.whl && "
        "test \"$#\" -eq 1 && test -f \"$1\" && echo WHEEL_OK"
    )
    return probe.returncode == 0 and "WHEEL_OK" in probe.stdout


_BOOT_SH_REL = "examples/deployer/startup/boot.sh"
_BOOT_WHEEL_BEGIN = "# >>> MWS_MOTOR_WHEEL_DIR_BEGIN"
_BOOT_WHEEL_END = "# <<< MWS_MOTOR_WHEEL_DIR_END"


def hardcode_motor_wheel_dir_in_boot_sh(
    adapter: Any,
    *,
    source_root: str,
    wheel_dir: str,
) -> str:
    """Write the just-built wheel dist path into remote Motor ``boot.sh``.

    Primary replace path: after each successful wheel build, patch the fixed
    remote motor tree's ``boot.sh`` so Pods that source it (via ConfigMap /
    hostPath) install that exact dist without needing K8s env injection.
    Re-running a build overwrites the hardcoded path (per-sha / per-user root).
    """
    boot_path = f"{source_root.rstrip('/')}/{_BOOT_SH_REL}"
    wheel_dir = str(wheel_dir).rstrip("/")
    # Remote Python patch keeps markers idempotent across rebuilds.
    script = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"boot = Path({boot_path!r})\n"
        f"wheel_dir = {wheel_dir!r}\n"
        f"begin = {_BOOT_WHEEL_BEGIN!r}\n"
        f"end = {_BOOT_WHEEL_END!r}\n"
        "block = (\n"
        "    begin + '\\n'\n"
        "    + f'MOTOR_WHEEL_DIR=\"{wheel_dir}\"\\n'\n"
        "    + end + '\\n'\n"
        ")\n"
        "if not boot.is_file():\n"
        "    raise SystemExit(f'missing boot.sh: {boot}')\n"
        "text = boot.read_text(encoding='utf-8')\n"
        "if begin in text and end in text:\n"
        "    pre, rest = text.split(begin, 1)\n"
        "    _, post = rest.split(end, 1)\n"
        "    if post.startswith('\\n'):\n"
        "        post = post[1:]\n"
        "    text = pre + block + post\n"
        "else:\n"
        "    needle = 'if [ -n \"${MOTOR_WHEEL_DIR:-}\" ]; then'\n"
        "    idx = text.find(needle)\n"
        "    if idx < 0:\n"
        "        raise SystemExit('boot.sh missing MOTOR_WHEEL_DIR install block')\n"
        "    text = text[:idx] + block + text[idx:]\n"
        "boot.write_text(text, encoding='utf-8')\n"
        "print('BOOT_WHEEL_DIR_HARDCODED=' + wheel_dir)\n"
        "PY"
    )
    result = adapter.run(script)
    if result.returncode != 0 or "BOOT_WHEEL_DIR_HARDCODED=" not in (result.stdout or ""):
        raise WorkspaceStateError(
            "failed to hardcode MOTOR_WHEEL_DIR into remote boot.sh: "
            + ((result.stderr or result.stdout or "")[-2000:])
        )
    return boot_path


def _remove_motor_wheel_dir_block_in_boot_sh(
    adapter: Any,
    *,
    source_root: str,
) -> str:
    """Remove the MWS_MOTOR_WHEEL_DIR marker block from remote ``boot.sh``.

    The block is a pure insert around the upstream install ``if`` line, so
    deleting the BEGIN..END segment restores boot.sh byte-for-byte. Idempotent:
    a boot.sh without the block is left untouched.
    """
    boot_path = f"{source_root.rstrip('/')}/{_BOOT_SH_REL}"
    script = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"boot = Path({boot_path!r})\n"
        f"begin = {_BOOT_WHEEL_BEGIN!r}\n"
        f"end = {_BOOT_WHEEL_END!r}\n"
        "if not boot.is_file():\n"
        "    raise SystemExit(f'missing boot.sh: {boot}')\n"
        "text = boot.read_text(encoding='utf-8')\n"
        "if begin in text and end in text:\n"
        "    pre, rest = text.split(begin, 1)\n"
        "    _, post = rest.split(end, 1)\n"
        "    if post.startswith('\\n'):\n"
        "        post = post[1:]\n"
        "    boot.write_text(pre + post, encoding='utf-8')\n"
        "    print('BOOT_WHEEL_DIR_REMOVED')\n"
        "else:\n"
        "    print('BOOT_WHEEL_DIR_ABSENT')\n"
        "PY"
    )
    result = adapter.run(script)
    if result.returncode != 0 or (
        "BOOT_WHEEL_DIR_REMOVED" not in (result.stdout or "")
        and "BOOT_WHEEL_DIR_ABSENT" not in (result.stdout or "")
    ):
        raise WorkspaceStateError(
            "failed to remove MOTOR_WHEEL_DIR block from remote boot.sh: "
            + ((result.stderr or result.stdout or "")[-2000:])
        )
    return boot_path


def _read_remote_boot_sh(adapter: Any, *, source_root: str) -> str:
    boot_path = f"{source_root.rstrip('/')}/{_BOOT_SH_REL}"
    result = adapter.run(f"cat {shell_quote(boot_path)}")
    if result.returncode != 0:
        raise WorkspaceStateError(
            "cannot read remote boot.sh for verification: "
            + ((result.stderr or result.stdout or "")[-2000:])
        )
    return result.stdout or ""


def _verify_boot_sh_wheel_override(content: str, *, wheel_dir: str | None) -> None:
    has_block = _BOOT_WHEEL_BEGIN in content and _BOOT_WHEEL_END in content
    if wheel_dir is None:
        if has_block:
            raise WorkspaceStateError(
                "boot.sh still carries a MWS_MOTOR_WHEEL_DIR block after image-mode reconcile"
            )
        return
    expected = f'{_BOOT_WHEEL_BEGIN}\nMOTOR_WHEEL_DIR="{wheel_dir}"\n{_BOOT_WHEEL_END}'
    if expected not in content:
        raise WorkspaceStateError(
            "boot.sh MOTOR_WHEEL_DIR block does not match bundle wheel_dir "
            f"{wheel_dir!r} after motor-wheel reconcile"
        )


def reconcile_motor_wheel_override(
    adapter: Any,
    *,
    source_root: str,
    wheel_dir: str | None,
) -> str:
    """Converge remote ``boot.sh`` to the desired wheel override state.

    ``wheel_dir`` set writes/refreshes the MWS_MOTOR_WHEEL_DIR block (delegates
    to the build-path hardcode helper); ``None`` removes the block. The remote
    content is verified against the desired state afterwards. Returns boot.sh
    path. Apply-time callers treat the bundle as the source of truth and pass
    its wheel_dir (or None for image mode) here unconditionally.
    """
    if wheel_dir is None:
        boot_path = _remove_motor_wheel_dir_block_in_boot_sh(adapter, source_root=source_root)
    else:
        boot_path = hardcode_motor_wheel_dir_in_boot_sh(
            adapter, source_root=source_root, wheel_dir=wheel_dir
        )
    content = _read_remote_boot_sh(adapter, source_root=source_root)
    _verify_boot_sh_wheel_override(content, wheel_dir=wheel_dir)
    return boot_path


def build_motor_wheel_in_docker(
    *,
    machine: dict[str, Any],
    base_image_ref: str,
    source_sha: str,
    reuse: bool = False,
) -> dict[str, Any]:
    """Build a ``motor`` wheel inside a Docker container on the machine host.

    The container is based on the runtime image and mounts the already-synced
    fixed motor source tree (read-only during docker build) plus a fixed shared
    build output directory. Inside the container it runs the upstream
    ``build.sh`` (which generates protobuf files and builds the Rust
    kv-conductor binary) and copies the resulting ``motor-*.whl`` into the
    shared output dir. After a successful build (or reuse), the fixed remote
    motor ``boot.sh`` is updated to hardcode ``MOTOR_WHEEL_DIR`` to that dist.

    Returns a build record with the remote wheel path, sha256, container image
    and source sha. Builds run by default so dirty source changes sharing the
    same Git sha cannot accidentally reuse an older wheel. Set ``reuse`` only
    when the caller explicitly accepts sha-keyed reuse.
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

    build_dir = f"{output_root}/{source_sha}"
    remote_wheel_dir = f"{build_dir}/dist"
    wheel_digest = f"mws-motor-wheel-{source_sha[:12]}"

    adapter = execution_adapter_for_machine(machine)

    if reuse and _remote_wheel_exists(adapter, build_dir):
        boot_path = hardcode_motor_wheel_dir_in_boot_sh(
            adapter, source_root=source_root, wheel_dir=remote_wheel_dir
        )
        record = _build_record(
            machine=machine,
            source_root=source_root,
            base_image_ref=base_image_ref,
            source_sha=source_sha,
            build_dir=build_dir,
            wheel_digest=wheel_digest,
            reused=True,
            status="ok",
        )
        record["boot_sh_path"] = boot_path
        return record

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
    cleanup = adapter.run(
        f"rm -f {shell_quote(remote_wheel_dir)}/motor-*.whl "
        f"{shell_quote(build_dir)}/wheel.sha256"
    )
    if cleanup.returncode != 0:
        raise WorkspaceStateError("failed to clean previous motor wheel build outputs")

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
        "set -- motor-*.whl; "
        "[ \"$#\" -eq 1 ] && [ -f \"$1\" ] || { "
        "echo 'expected exactly one motor wheel' >&2; exit 1; }; "
        f"sha256sum \"$1\" > {shell_quote(build_dir)}/wheel.sha256"
    )
    marker = adapter.run(marker_script)
    if marker.returncode != 0:
        raise WorkspaceStateError(
            "motor wheel build must produce exactly one motor-*.whl artifact"
        )

    boot_path = hardcode_motor_wheel_dir_in_boot_sh(
        adapter, source_root=source_root, wheel_dir=remote_wheel_dir
    )
    record = _build_record(
        machine=machine,
        source_root=source_root,
        base_image_ref=base_image_ref,
        source_sha=source_sha,
        build_dir=build_dir,
        wheel_digest=wheel_digest,
        reused=False,
        status="ok",
    )
    record["boot_sh_path"] = boot_path
    return record


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
