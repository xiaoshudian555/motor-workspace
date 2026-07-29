import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def invoke(*args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [str(ROOT / "bin" / "motorws"), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_status_is_machine_readable():
    code, payload = invoke("status")
    assert code == 0
    assert payload["status"] == "ok"
    assert len(payload["sources"]) == 3


def test_unresolved_lock_fails_closed():
    code, payload = invoke("lock", "verify")
    assert code == 1
    assert payload["status"] == "error"
    assert payload["errors"]

