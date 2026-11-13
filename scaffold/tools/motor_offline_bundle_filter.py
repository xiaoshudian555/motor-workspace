#!/usr/bin/env python3
"""Filter motor requirements.txt for offline bundle (exclude vllm-ascend overlap)."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _pkg_name(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    name = re.split(r"[<>=!;\[]", line, maxsplit=1)[0].strip().lower()
    return name or None


def _load_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: motor_offline_bundle_filter.py <motor-req> <vllm-ascend-req> <out-req>", file=sys.stderr)
        return 2
    motor_path, ascend_path, out_path = map(Path, sys.argv[1:4])
    ascend_names = {_pkg_name(l) for l in _load_lines(ascend_path)} - {None}
    skip = ascend_names | {"transformers", "pytest-asyncio", "pytest"}
    kept: list[str] = []
    excluded: list[str] = []
    for raw in _load_lines(motor_path):
        name = _pkg_name(raw)
        if name is None:
            kept.append(raw)
            continue
        if name in skip:
            excluded.append(raw)
            continue
        kept.append(raw)
    out_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    print(f"ascend_packages={len(ascend_names)} excluded_lines={len(excluded)} kept_lines={len([k for k in kept if _pkg_name(k)])}")
    for line in excluded:
        print(f"exclude: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
