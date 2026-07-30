# Architecture and boundaries

The user-workflow boundaries are defined in
[functional-boundaries.md](functional-boundaries.md). This document describes
the implementation layers and runtime constraints underneath those boundaries.

## Implementation layers

1. `.agents/skills/` provides Agent-facing workflow entry points.
2. `.agents/lib/` provides shared workflow implementation and contracts.
3. `.remote-dev/` provides generic remote endpoint operations without Motor or
   Kubernetes workflow semantics.
4. `.motor-workspace-local/` stores untracked machine state and
   parity/deploy/validation run evidence.

The three functional boundaries do not map one-to-one to these directories.
For example, `.remote-dev` may be used by machine verification, diagnosis, or
ad hoc remote development.

## Primary runtime path

1. Remote development preparation binds one local workspace to one fixed remote
   source root; parity updates those fixed directories and produces content
   evidence.
2. `motor-k8s-deploy` consumes those directories, reuses the upstream Motor
   deployer, injects mount/PYTHONPATH configuration, and proves which code Pods
   actually load.
3. Validation consumes a successful deploy run for formal smoke, benchmark,
   profiling, and diagnosis.

Minimal connectivity/readiness checks belong to deploy acceptance. Formal
workloads and their pass/fail criteria belong to validation.

## Shared mount root

- Profile field `mount_root`, default `/mnt`.
- Fixed one-to-one remote source directories:

```text
/mnt/motor-workspace/motor
/mnt/motor-workspace/vllm
/mnt/motor-workspace/vllm-ascend
/mnt/motor-workspace/python-overlay
```

- No workspace ID, session ID, or run ID is part of the remote source path.
- Motor deployer templates already mount hostPath `/mnt:/mnt` on Controller,
  Coordinator, Engine and related roles. The wrapper verifies/reuses that mount
  and injects `PYTHONPATH` on runtime containers only.
- Pure Python changes: parity overwrite + `deploy_restart`.
- Editable install / ABI-sensitive changes: bootstrap Pod/Job or image bypass.

## Explicit non-goals

- No runtime snapshot directories.
- No `current` symlink.
- No plan digest gate.
- No Git commit requirement for daily deploy/restart.
- No node-local fanout (unsupported until explicitly implemented).
- No default session-management layer.

## Parity vs image bypass

| Path | When |
|------|------|
| remote-code-parity → fixed remote dirs → hostPath → PYTHONPATH | Default daily development |
| tools/build/ image bypass | Release, no shared storage, explicit user request |

## Extension contracts

`motor-k8s-deploy` consumes Motor deployer dry-run output, processes only newly
generated YAML, injects hostPath/PYTHONPATH, and applies after user approval.
It must not rewrite AscendJob/HCCL/ranktable business logic.

`tools/build/` is optional and non-default.

## machine-management vs preflight

Machine inventory records SSH endpoints, kube context references, `mount_root`,
`remote_workspace_root`, and parity backend (`shared-hostpath` only).
MindCluster/Volcano/CRD checks run as verify steps on a registered machine —
not as a replacement for inventory.
