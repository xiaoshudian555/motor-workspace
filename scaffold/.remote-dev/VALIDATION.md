# Remote-Dev Scaffold Validation

Last updated: 2026-08-11.

## Current local evidence

Run from `scaffold/`:

```bash
python3 .remote-dev/tools/validate_remote_dev_scaffold.py --local-only
```

Current result:

- compileall: passed;
- remote-dev: 72 tests passed, including 33 subtests;
- workspace executor tests: 12 passed;
- Claude Skill shim check: passed;
- scoped `git diff --check`: passed;
- MCP burden check: 18 tools, no per-tool CLI fallback, maximum three
  tool-specific required fields;
- overall local validation status: `ok`.

## Covered contracts

- direct endpoint and alias resolution; managed session selectors are rejected;
- SSH command execution and CRLF normalization;
- root/cwd path policy and path-escape rejection;
- read ledger scoping and edit/write concurrency behavior;
- read, write, edit, multi-edit, list, glob, and grep semantics;
- Codex and unified-diff patch validation, atomicity, rollback, symlink blocking,
  and file moves;
- background Bash, monitor, job status/tail/stop, and bounded output;
- artifact manifest/pull/push path and hash validation;
- MCP schemas, resources, result contract, and Claude/Codex hook guards;
- canonical Skill to Claude shim synchronization.

## Live endpoint validation

Use a concrete direct endpoint or configured alias:

```bash
python3 .remote-dev/tools/validate_remote_dev_scaffold.py \
  --host <host> --port <port> \
  --root /mnt/motor-workspace \
  --cwd /mnt/motor-workspace
```

The validator uses a unique scratch directory and exercises probe, context,
Bash, read/edit/write, search, patch, artifacts, jobs, MCP resources, and
parallel workers. Cleanup targets only that scratch directory.

No live endpoint was supplied during the 2026-08-11 script-reduction validation,
so the current refactor is locally verified but not newly revalidated against a
real remote host.

## Not covered by this validator

- Motor wheel ABI compatibility inside a real NPU runtime Pod;
- Kubernetes deploy, rollout, Service readiness, inference, or benchmark;
- destructive parity overwrite against a user machine;
- fault injection or Reliability recovery.

Those require the corresponding Motor Skill, a concrete endpoint, and explicit
authorization where state mutation is involved.
