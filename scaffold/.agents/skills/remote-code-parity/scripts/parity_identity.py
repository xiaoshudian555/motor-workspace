#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_machine_target import resolve_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence, prove_identity_parity  # noqa: E402
from mws_result import build_result_envelope, emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, parity_run_dir, write_parity_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--machine-run-id", default="")
    parser.add_argument("--parity-run-id", default="")
    args = parser.parse_args()
    started_at = utc_now_iso()
    run_id = args.parity_run_id or new_run_id("parity")

    def _fail(errors: list, *, extra: dict | None = None) -> int:
        envelope = build_result_envelope(
            kind="parity-complete",
            run_id=run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=errors,
            status="failed",
            extra={"machine": args.machine, **(extra or {})},
        )
        return emit_result(envelope)

    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    machine_run_id = args.machine_run_id.strip() or None
    progress("loading machine-ready evidence")
    machine_ready = load_machine_ready_evidence(alias, machine_run_id=machine_run_id)
    progress("proving identity parity (remote-native, no copy/overwrite)")
    manifest = prove_identity_parity(
        machine,
        machine_ready=machine_ready,
    )
    status = manifest.get("status", "error")
    run_dir = parity_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    parity_complete = status == "ready" and manifest.get("remote_content_digest")
    run_status = "ready" if parity_complete else "failed"
    write_parity_run(
        run_id,
        {
            "status": run_status,
            "parity_complete": parity_complete,
            "machine": alias,
            "machine_run_id": machine_ready.get("machine_run_id"),
            "manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
            "manifest": manifest,
        },
    )
    paths = manifest.get("source_dirs", {})
    envelope = build_result_envelope(
        kind="parity-complete",
        run_id=run_id,
        workflow_run_id=str(machine_ready.get("workflow_run_id") or "workflow-unset"),
        checks=[
            {
                "name": "identity_parity",
                "status": "ok" if parity_complete else "error",
                "message": manifest.get("sync_mode") or "; ".join(manifest.get("errors", [])),
            }
        ],
        started_at=started_at,
        upstream_refs=[{"kind": "machine-ready", "run_id": str(machine_ready.get("machine_run_id"))}],
        errors=[] if parity_complete else [f"identity parity did not complete: {manifest.get('status')}"],
        artifacts=[{"path": str(manifest_path.relative_to(REPO_ROOT))}],
        status="ready" if parity_complete else "failed",
        extra={
            "parity_complete": parity_complete,
            "machine": alias,
            "machine_run_id": machine_ready.get("machine_run_id"),
            "source_mode": "identity",
            "remote_workspace_root": manifest.get("remote_workspace_root"),
            "source_dirs": paths,
            "local_content_digest": manifest.get("local_content_digest"),
            "remote_content_digest": manifest.get("remote_content_digest"),
            "content_digests": manifest.get("content_digests", {}),
            "sync_mode": manifest.get("sync_mode"),
            "next": "motor-deploy-configure then motor-k8s-deploy apply, or deploy_restart (code-only updates)",
        },
    )
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
