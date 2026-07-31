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
    inventory_lock,
    load_inventory,
    save_inventory,
    utc_now_iso,
)
from mws_machine_target import (  # noqa: E402
    build_machine_result_envelope,
    run_machine_ready_checks,
)
from mws_result import emit_result, progress, utc_now_iso as result_now  # noqa: E402
from mws_run_state import new_run_id, new_workflow_run_id, write_run  # noqa: E402
from mws_transport import transport_for_machine  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402
from repo_paths import SCAFFOLD_ROOT  # noqa: E402


def _persist_machine_run(
    *,
    machine_run_id: str,
    envelope: dict,
    alias: str,
    machine_ref: dict,
    endpoint: dict,
) -> None:
    record = {
        **envelope,
        "alias": alias,
        "machine": alias,
        "machine_ref": machine_ref,
        "endpoint": endpoint,
    }
    write_run("machine-ready", machine_run_id, record, immutable=True)


def _record_verify_result(alias: str, errors: list) -> None:
    with inventory_lock():
        inventory = load_inventory()
        machines = inventory.get("machines", {})
        if alias in machines:
            machines[alias]["last_verified_at"] = utc_now_iso()
            machines[alias]["last_verify_errors"] = list(errors)
            save_inventory(inventory)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--workflow-run-id", default="", help="optional workflow run id")
    parser.add_argument("--machine-run-id", default="", help="optional explicit machine run id")
    args = parser.parse_args()
    alias = require_safe_id(args.alias, label="alias")
    machine = get_machine(alias)

    started_at = result_now()
    machine_run_id = args.machine_run_id.strip() or new_run_id("machine")
    workflow_run_id = args.workflow_run_id.strip() or new_workflow_run_id()

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
        envelope = build_machine_result_envelope(
            run_id=machine_run_id,
            workflow_run_id=workflow_run_id,
            payload={
                "alias": alias,
                "checks": [],
                "warnings": [],
                "errors": [str(exc)],
                "machine_ref": None,
                "endpoint": None,
            },
            started_at=started_at,
        )
        envelope["status"] = "failed"
        _persist_machine_run(
            machine_run_id=machine_run_id,
            envelope=envelope,
            alias=alias,
            machine_ref={},
            endpoint={},
        )
        _record_verify_result(alias, [str(exc)])
        return emit_result(envelope)

    envelope = build_machine_result_envelope(
        run_id=machine_run_id,
        workflow_run_id=workflow_run_id,
        payload=result,
        started_at=started_at,
    )
    _persist_machine_run(
        machine_run_id=machine_run_id,
        envelope=envelope,
        alias=alias,
        machine_ref=result["machine_ref"],
        endpoint=result["endpoint"],
    )

    _record_verify_result(alias, result["errors"])

    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
