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
   workspace/machine/parity/environment/config/deploy/validation run evidence.

The three functional boundaries do not map one-to-one to these directories.
For example, `.remote-dev` may be used by machine verification, diagnosis, or
ad hoc remote development.

## Primary runtime path

1. Remote development preparation binds one local workspace to one fixed remote
   source root; parity updates those fixed directories and produces content
   evidence.
2. Motor Deploy contains three explicit steps, mirroring the three-step
   decomposition of the first major phase:
   - `motor-deploy-preflight` proves that the K8s/MindCluster base environment
     is usable without reading or validating a concrete Motor deploy config;
   - `motor-deploy-configure` consumes environment and parity evidence,
     generates or reuses the immutable deploy bundle, performs all
     substitutions and dry-runs, and proves that the bundle points at the
     intended code paths;
   - `motor-k8s-deploy` applies that exact bundle after approval, waits for
     Ready, and proves which code Pods actually load.
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
- Pure Python changes: parity overwrite + config-bundle compatibility/rebind +
  `deploy_restart`.
- Editable install / ABI-sensitive changes: bootstrap Pod/Job or image bypass.

## Explicit non-goals

- No runtime snapshot directories.
- No `current` symlink.
- No source-content digest gate that forces YAML regeneration for every Python
  edit. Immutable deploy config bundles still require an integrity digest and
  `config_fingerprint`.
- No Git commit requirement for daily deploy/restart.
- No node-local fanout (unsupported until explicitly implemented).
- No default session-management layer.

## Parity vs image bypass

| Path | When |
|------|------|
| remote-code-parity → fixed remote dirs → hostPath → PYTHONPATH | Default daily development |
| tools/build/ image bypass | Release, no shared storage, explicit user request |

## Extension contracts

`motor-deploy-preflight` owns only K8s/MindCluster environment evidence and
does not read the Motor user config. `motor-deploy-configure` owns the upstream
deployer dry-run, run-scoped staging, final YAML, hostPath/PYTHONPATH/image
substitutions, config diff, manifest validation, server-side dry-run, and the
immutable bundle contract. `motor-k8s-deploy` applies that exact bundle after
user approval and owns post-apply Ready/runtime evidence. No step may rewrite
AscendJob/HCCL/ranktable business logic.

`tools/build/` is optional and non-default.

## machine-management vs environment preflight vs deploy config

Machine inventory records SSH endpoints, kube context references, `mount_root`,
`remote_workspace_root`, and parity backend (`shared-hostpath` only).

`machine-management` verifies only the stable remote-development facts needed
before parity: SSH/remote execution, safe fixed paths, directory read/write,
and required file-transfer tools. A recorded kube context or hardware profile
is a reference, not proof that a Motor deployment is ready.

`motor-deploy-preflight` owns the environment-level check. It combines the
machine reference, kube context, and environment profile to check Kubernetes
API access, baseline read permissions, MindCluster/Volcano/CRDs/controllers,
device plugins, and the NPU resource types reported by the cluster. It does not
consume parity, a Motor user config, namespace, model, image, or final
manifests.

`motor-deploy-configure` owns every check that depends on the concrete deploy
inputs or final manifests: exact namespace/RBAC, scheduling constraints,
candidate-node path visibility, model/image references, hostPath/PYTHONPATH
substitution, upstream deployer dry-run, manifest validation, and Kubernetes
server-side dry-run.

The results are deliberately different:

```text
machine-ready
  = remote development and parity can proceed

deploy-environment-ready
  = the K8s/MindCluster base environment is usable

deploy-config-ready
  = this immutable Motor config bundle is ready to be applied

deploy-complete
  = the bundle was applied, Motor is Ready, and runtime source use is proven
```

The first two steps do not mutate Kubernetes state. Run-scoped local staging
and evidence writes are allowed during configuration, but apply and all
post-apply runtime evidence remain separate deployment responsibilities.

The three steps are separate responsibility units and target skills. A
top-level workflow may invoke them in order, but no step owns another step's
checks or result:

```text
motor-deploy-preflight
  → deploy-environment-ready

motor-deploy-configure
  → deploy-config-ready + immutable config bundle

motor-k8s-deploy
  → deploy-complete + deploy run + Ready/runtime source evidence
```

None of these results may be reused as another step's completion result.
