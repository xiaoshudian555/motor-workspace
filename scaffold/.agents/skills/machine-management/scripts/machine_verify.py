#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import load_profile  # noqa: E402
from mws_local_state import (  # noqa: E402
    WorkspaceStateError,
    get_machine,
    load_inventory,
    save_inventory,
    utc_now_iso,
)
from mws_machine_target import run_machine_ready_checks  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_transport import transport_for_machine  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402
from repo_paths import SCAFFOLD_ROOT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    alias = require_safe_id(args.alias, label="alias")
    machine = get_machine(alias)

    profile_path = SCAFFOLD_ROOT / args.profile
    profile = load_profile(profile_path) if profile_path.exists() else {}
    profile_context = profile.get("kubernetes", {}).get("context", "")

    progress("checking SSH connectivity and remote development substrate")
    try:
        transport = transport_for_machine(machine)
        result = run_machine_ready_checks(
            machine,
            transport,
            profile_kube_context=str(profile_context or ""),
        )
    except WorkspaceStateError as exc:
        return emit(
            {
                "status": "error",
                "alias": alias,
                "ready": False,
                "checks": [],
                "errors": [str(exc)],
            }
        )

    inventory = load_inventory()
    inventory["machines"][alias]["last_verified_at"] = utc_now_iso()
    inventory["machines"][alias]["last_verify_errors"] = result["errors"]
    save_inventory(inventory)

    return emit(
        {
            "status": "ok" if result["ready"] else "error",
            "alias": alias,
            "ready": result["ready"],
            "checks": result["checks"],
            "errors": result["errors"],
            "machine_ref": result["machine_ref"],
            "endpoint": result["endpoint"],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
