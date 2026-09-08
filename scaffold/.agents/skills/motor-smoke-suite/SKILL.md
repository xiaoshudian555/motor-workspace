---
name: motor-smoke-suite
description: Orchestrate the repeatable Pymotor validation suite covering online scale, RAS recovery, GPQA accuracy, and performance. Use for Pymotor 冒烟套组, 全量验收, full acceptance, or a nightly suite; minimal readiness alone belongs to motor-smoke.
---

# Motor smoke suite

Keep this Skill as a thin suite definition. It coordinates existing Pymotor test
cases or their owning atomic Skills; it does not introduce a workflow engine or
second Motor deployer.

The same acceptance contract is also mirrored under the Motor submodule at
`sources/motor/.agent/skills/motor-smoke-suite/` for product-repo discovery.
Keep both copies aligned when editing the suite definition.

Read [references/suite-profiles.md](references/suite-profiles.md) before planning
or running the suite. Read
[references/unattended-authorization.md](references/unattended-authorization.md)
before any scheduled/CI run. Read
[references/failure-routing.md](references/failure-routing.md) only after a case
fails or blocks.

## Execution source

Prefer the existing Pymotor automation suite when its real source and entrypoint
are supplied. Its setup, case body, restoration, and teardown remain
authoritative. The current workspace records that legacy flow in
`scaffold/docs/validation/pymotor-existing-smoke-flow.md`, but does not contain
the six legacy case implementations; do not claim a code-driven suite run from
the documentation alone.

When no executable suite is available, route each selected case to its existing
atomic Skill. Reuse native `deploy.py` / `delete.sh`, reliability, accuracy, and
benchmark contracts rather than copying their commands here.

## Modes

| Mode | Default trigger | Authorization |
|---|---|---|
| `interactive` | Chat request for the suite | Present the full concrete mutation/load plan and obtain consent before live work |
| `unattended` | Fixed outer entrypoint + signed profile | Do not re-prompt; enforce the profile scope or `BLOCKED` |

Ordinary deploy/scale/RAS chat requests are never `unattended`.

## Suite contract

1. The full suite means the six concrete cases listed in
   `references/suite-profiles.md`. A user may select a subset.
2. Service creation, readiness, inference probes, restoration, and cleanup are
   shared setup/teardown or case assertions. They are not separate suite stages.
3. Use the **Reference workload intent** in `suite-profiles.md` (model family,
   served name, P/D topology, TP/DP/EP, HA) as the suite's logical target.
   Resolve host paths, mounts, and image tags on the live endpoint; do not bake
   absolute addresses into this Skill.
4. Resolve the target, native configuration, case inputs, thresholds, timeouts,
   and software revisions before starting. Do not invent a missing path,
   baseline, dataset, or target.
5. **Interactive:** present the complete concrete mutation/load plan for
   authorization before a live run. Execution does not broaden that
   authorization.
6. **Unattended:** load a signed profile from
   `.motor-workspace-local/suite-profiles/`, validate it per
   `references/unattended-authorization.md`, then run without further consent.
   Pass suite pre-authorization into `motor-deploy` / `motor-k8s-deploy` /
   `motor-scale` / `motor-reliability` for in-scope actions only.
7. Preserve each case's raw logs and metrics. Determine PASS/FAIL from its owning
   code or atomic Skill contract, never from Agent prose.
8. After a failure, preserve evidence and run mandatory restoration/cleanup.
   Continue to an independent case only when the target is proven restored;
   otherwise stop and report `RESTORATION FAILED`.

## Scheduled entrypoint

Scheduling is an outer concern (cron, CI, or Automation). The fixed invocation
must name `unattended` mode and the profile path. Prefer one real automation
suite entrypoint with a versioned configuration when available; otherwise route
through this Skill's unattended mode. Publish ordinary logs, metrics, summary,
and a non-zero process/status on non-`PASS`.

Report the selected cases, mode, `profile_id` (when unattended), resolved
target/configuration, code and tool revisions, per-case result and evidence,
cleanup status, and overall `PASS`, `FAIL`, `BLOCKED`, or `RESTORATION FAILED`.
