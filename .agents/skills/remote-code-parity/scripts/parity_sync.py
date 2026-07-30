#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_machine_target import resolve_machine  # noqa: E402
from mws_parity import sync_workspace_fanout  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import new_run_id, parity_run_dir, write_parity_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--approved-overwrite", action="store_true")
    parser.add_argument("--parity-run-id", default="")
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
    progress("syncing local dirty tree to machine fixed remote directories")
    manifest = sync_workspace_fanout(machine)
    status = manifest.get("status", "error")
    run_id = args.parity_run_id or new_run_id("parity")
    run_dir = parity_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_parity_run(
        run_id,
        {
            "status": status,
            "machine": alias,
            "manifest_path": str(manifest_path.relative_to(ROOT)),
            "manifest": manifest,
        },
    )
    if status != "ok":
        return emit(
            {
                "status": "error",
                "parity_run_id": run_id,
                "machine": alias,
                "errors": manifest.get("errors", []),
                "targets": manifest.get("targets", []),
            }
        )
    paths = manifest.get("source_dirs", {})
    return emit(
        {
            "status": "ok",
            "parity_run_id": run_id,
            "machine": alias,
            "remote_workspace_root": manifest.get("remote_workspace_root"),
            "source_dirs": paths,
            "pythonpath": manifest.get("pythonpath"),
            "targets": manifest.get("targets", []),
            "artifacts": [str(manifest_path.relative_to(ROOT))],
            "next": "motor-k8s-deploy plan/apply (first deploy) or deploy_restart (code-only updates)",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
