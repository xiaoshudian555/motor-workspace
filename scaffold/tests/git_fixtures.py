"""Real-git fixtures for parity tests.

The git-based parity path (`mws_parity` synthetic snapshots, bundles, remote
mirror) only makes sense against a real git repository, so tests build a real
repo with `git init` + commit instead of monkeypatching `mws_parity._git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def init_repo(
    path: Path,
    *,
    files: dict[str, str],
    commit: bool = True,
) -> Path:
    """Create a real git repository at `path` with the given files.

    When `commit` is True the initial files are committed; afterwards the repo
    may be dirtied by writing new/changed files directly."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "parity-test@localhost"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "parity-test"], cwd=path, check=True)
    for name, content in files.items():
        (path / name).write_text(content, encoding="utf-8")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return path
