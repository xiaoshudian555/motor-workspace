from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_motorws_status_json() -> None:
    result = subprocess.run(
        [str(SCAFFOLD / "bin" / "motorws"), "status"],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    payload = __import__("json").loads(result.stdout)
    assert "status" in payload
    assert payload.get("backend") == "motorws-internal"


def test_repo_init_probe_json() -> None:
    script = SCAFFOLD / ".agents/skills/repo-init/scripts/repo_init_probe.py"
    result = subprocess.run(
        [sys.executable, str(script), "--compact"],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    payload = __import__("json").loads(result.stdout)
    assert "submodules" in payload
