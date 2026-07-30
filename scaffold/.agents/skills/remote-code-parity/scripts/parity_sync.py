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
from mws_parity import load_machine_ready_evidence, sync_workspace_fanout  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import new_run_id, parity_run_dir, write_parity_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--approved-overwrite", action="store_true")
    parser.add_argument("--machine-run-id", default="")
    parser.add_argument("--parity-run-id", default="")
    parser.add_argument("--skip-fast-path", action="store_true")
    args = parser.parse_args()
    if not args.approved_overwrite:
        return emit(
            {
                "status": "error",
                "errors": [
                    "parity sync overwrites remote fixed directories; re-run with --approved-overwrite"
                ],
            }
        )
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    machine_run_id = args.machine_run_id.strip() or None
    progress("loading machine-ready evidence")
    machine_ready = load_machine_ready_evidence(alias, machine_run_id=machine_run_id)
    progress("syncing local dirty tree to machine fixed remote directories")
    manifest = sync_workspace_fanout(
        machine,
        machine_ready=machine_ready,
        skip_fast_path=args.skip_fast_path,
    )
    status = manifest.get("status", "error")
    run_id = args.parity_run_id or new_run_id("parity")
    run_dir = parity_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    parity_complete = status == "ok" and manifest.get("remote_content_digest")
    run_status = "ok" if parity_complete else "failed"
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
    if not parity_complete:
        return emit(
            {
                "status": "error",
                "parity_run_id": run_id,
                "parity_complete": False,
                "machine": alias,
                "machine_run_id": machine_ready.get("machine_run_id"),
                "errors": manifest.get("errors", ["parity sync did not complete"]),
                "targets": manifest.get("targets", []),
            }
        )
    paths = manifest.get("source_dirs", {})
    return emit(
        {
            "status": "ok",
            "parity_complete": True,
            "parity_run_id": run_id,
            "machine": alias,
            "machine_run_id": machine_ready.get("machine_run_id"),
            "remote_workspace_root": manifest.get("remote_workspace_root"),
            "source_dirs": paths,
            "pythonpath": manifest.get("pythonpath"),
            "local_content_digest": manifest.get("local_content_digest"),
            "remote_content_digest": manifest.get("remote_content_digest"),
            "sync_mode": manifest.get("sync_mode"),
            "targets": manifest.get("targets", []),
            "artifacts": [str(manifest_path.relative_to(REPO_ROOT))],
            "next": "motor-k8s-deploy plan/apply (first deploy) or deploy_restart (code-only updates)",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
