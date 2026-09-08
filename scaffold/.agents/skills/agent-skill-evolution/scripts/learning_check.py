#!/usr/bin/env python3
"""Read-only learning-state gate. Does not evaluate an LLM or execute evidence."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def required(data, field):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing/non-text {field}")
    return value


def local_file(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"missing or outside-root artifact: {value}")
    return path


def read(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected object: {path}")
    return data


def candidate(root, path):
    data = read(path)
    if not ID.fullmatch(required(data, "id")) or data["id"] != path.stem:
        raise ValueError("candidate id must match kebab-case filename")
    for field in ("owner", "expected", "actual", "scope"):
        required(data, field)
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(item, str) or not item.strip() for item in evidence
    ):
        raise ValueError("evidence must be a nonempty text list")
    status = data.get("status")
    if status == "pending":
        required(data, "blocker")
        required(data, "next_action")
    elif status == "rejected":
        required(data, "reason")
    elif status == "promoted":
        required(data, "verification")
        required(data, "change")
        lesson = local_file(root, required(data, "lesson"))
        index = local_file(root, required(data, "index"))
        if lesson.name not in index.read_text(encoding="utf-8"):
            raise ValueError("promoted lesson is absent from index")
    else:
        raise ValueError(f"unsupported candidate status: {status}")
    return data


def check_receipt(root, path):
    data = read(local_file(root, str(path)))
    required(data, "task")
    events = data.get("events")
    if not isinstance(events, list) or any(not isinstance(x, str) or not ID.fullmatch(x) for x in events):
        raise ValueError("events must contain kebab-case candidate IDs")
    if len(events) != len(set(events)):
        raise ValueError("duplicate event")
    if not events:
        required(data, "no_event_reason")
    pending = []
    for event in events:
        item = candidate(root, root / ".motor-workspace-local/learning/candidates" / f"{event}.json")
        if item["status"] == "pending":
            pending.append({"id": event, "next_action": item["next_action"]})
    consumed = data.get("consumed")
    if not isinstance(consumed, list):
        raise ValueError("consumed must be a list")
    for item in consumed:
        if not isinstance(item, dict):
            raise ValueError("invalid consumption record")
        local_file(root, required(item, "lesson"))
        required(item, "result")
    return {"receipt_valid": True, "pending": pending, "consumed_count": len(consumed)}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("command", choices=("pending", "check"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "check":
            if args.receipt is None:
                raise ValueError("check requires --receipt")
            output = check_receipt(root, args.receipt)
        else:
            items = [candidate(root, p) for p in sorted((root / ".motor-workspace-local/learning/candidates").glob("*.json"))]
            output = {"pending": [x for x in items if x["status"] == "pending"]}
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"receipt_valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
