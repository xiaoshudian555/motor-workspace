#!/usr/bin/env python3
"""Diagnosis entry that queries experience cards before cluster collection.

The motor-diagnosis skill runs this command. A keyword hit is not a conclusion.
APPLIED appears only after the caller passes --check CARD=pass for that card. A miss stays visible
in the experience_query line.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

LOOKUP = Path.home() / "projects" / "doc" / "scripts" / "lookup-experience.py"


def load_lookup():
    if not LOOKUP.is_file():
        return None
    spec = importlib.util.spec_from_file_location("lookup_experience", LOOKUP)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    lookup = load_lookup()
    if lookup is None:
        print(
            "diagnosis_entry=motor-diagnosis experience_query "
            "hit=0 applied=0 check_failed=0 no_match=true limit=2 error=missing_lookup"
        )
        print(f"missing lookup: {LOOKUP}", file=sys.stderr)
        return 2
    print("diagnosis_entry=motor-diagnosis")
    args = list(argv if argv is not None else sys.argv[1:])
    if "--entry" not in args:
        args = ["--entry", "motor-diagnosis", *args]
    lookup_argv = [str(LOOKUP), *args]
    sys.argv = lookup_argv
    return lookup.main()


if __name__ == "__main__":
    raise SystemExit(main())
