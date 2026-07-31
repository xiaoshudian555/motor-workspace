from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))


def test_deploy_apply_requires_consent() -> None:
    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_apply.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--machine", "dev1", "--config-run-id", "cfg-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert "approved-by-user" in payload["errors"][0]
