#!/usr/bin/env python3
"""K8s / MindCluster environment preflight (3+3 part-2 step 1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_environment import load_profile_from_path, run_environment_preflight_checks  # noqa: E402
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True, help="machine alias from inventory")
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument(
        "--skip-pod-readiness",
        action="store_true",
        help="skip namespace pod readiness probe",
    )
    args = parser.parse_args()

    alias = require_safe_id(args.alias, label="alias")
    try:
        machine = get_machine(alias)
    except WorkspaceStateError as exc:
        return emit({"status": "error", "alias": alias, "ready": False, "errors": [str(exc)]})

    profile = load_profile_from_path(args.profile)
    if not profile:
        return emit(
            {
                "status": "error",
                "alias": alias,
                "ready": False,
                "errors": [f"deploy profile not found: {args.profile}"],
            }
        )

    progress("checking Kubernetes and MindCluster base environment")
    result = run_environment_preflight_checks(
        machine=machine,
        profile=profile,
        include_pod_readiness=not args.skip_pod_readiness,
    )

    return emit(
        {
            "status": "ok" if result["ready"] else "error",
            "alias": alias,
            "ready": result["ready"],
            "result": "deploy-environment-ready" if result["ready"] else "failed",
            "checks": result["checks"],
            "errors": result["errors"],
            "namespace": result.get("namespace"),
            "kube_context": result.get("kube_context"),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
