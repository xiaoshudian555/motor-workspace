# Repository instructions

Local `sources/motor` + `sources/vllm` + `sources/vllm-ascend` development
scaffold. All three are Git submodules under `sources/`.

Workflow code lives under `scaffold/`. This repository provides a remote
development substrate first, then Motor domain skills on top. The primary
development path is **remote-code-parity + shared mount root hostPath into Pods**,
not rebuilding an image for every code change.

## Repository layout

```text
sources/          motor, vllm, vllm-ascend submodules
scaffold/         skills, lib, remote-dev, profiles, tools, tests, docs
.motor-workspace-local/   untracked machine and workflow run evidence (repo root)
```

## Remote development model

Use native client tools for local files and local shell work.

Use `scaffold/.remote-dev` remote companion tools for remote endpoints on the
shared mount root (default `/mnt`, configurable via profile `mount_root`):

| Local tool | Remote tool |
|------------|-------------|
| Read | `remote.read` |
| Edit | `remote.edit` |
| Write | `remote.write` |
| Bash | `remote.bash` |
| Glob | `remote.glob` |
| Grep | `remote.grep` |
| LS | `remote.ls` |
| Monitor | `remote.monitor` |
| apply_patch | `remote.apply_patch` |

Default endpoint fields:

- `host`
- `port`
- `user`, default `root`
- `root`, default shared mount root
- `cwd`, default fixed remote workspace directory

Prefer `host + port` direct endpoints for ordinary remote development.
Machine inventory is resolved in `scaffold/.agents/lib` and passed as direct
endpoints to `scaffold/.remote-dev` tools.

## Skills

Repo-local skills live under `scaffold/.agents/skills/`. Each has its own
`SKILL.md` — read that before invoking.

| Skill | Purpose |
|-------|---------|
| `repo-init` | Initialize workspace: gh, GitHub auth, submodules, fork topology, lock; produces `workspace-ready` (audit only, not a downstream gate) |
| `machine-management` | Add / verify / repair / remove remote NPU machine + kube context + mount root |
| `remote-toolbox` | Remote target/probe/exec/job/sync/artifact/cleanup backend |
| `remote-code-parity` | Sync local dirty tree to fixed remote directories before deploy/verify |
| `motor-deploy-preflight` | K8s/MindCluster environment preflight (read-only); produces `deploy-environment-ready` |
| `motor-deploy-configure` | Motor native config → immutable bundle + dry-run; produces `deploy-config-ready` |
| `motor-k8s-deploy` | Apply immutable config bundle, Ready/runtime source proof; produces `deploy-complete` |
| `motor-smoke` | Prove a successful deploy is runnable using Motor readiness plus real non-stream/stream inference |
| `motor-functional` | Compile natural-language feature goals into catalog-backed functional validation specs and dispatch cases |
| `motor-benchmark` | Benchmark a successful deploy run (third major part) |
| `motor-diagnosis` | Collect run-scoped deploy/diagnostic artifacts |

None of these are gates for normal local coding or unrelated Git tasks.
For remote endpoint work, prefer `scaffold/.remote-dev` tools first and use
skills for domain workflows.

## Repo-wide rules

- Never write secrets, passwords, or tokens into tracked files.
- Keep runtime state under `.motor-workspace-local/` and remote-dev state under
  `scaffold/.remote-dev/state/`. Both are untracked.
- Keep `.gitmodules` on community upstream URLs.
- Prefer `scaffold/.remote-dev` or skill wrapper scripts over raw SSH for remote work.
- Skill wrappers: progress on `stderr`, final JSON on `stdout`.
- Development binds one local workspace to one machine and one fixed
  `remote_workspace_root` under the shared mount root.
- Development parity syncs source once to fixed directories under the shared
  mount root; Pods pick it up via existing hostPath (default `/mnt:/mnt`) and
  injected `PYTHONPATH`. Do not fan out copies to per-session paths. Image
  rebuild is an optional bypass for release/delivery, not the default loop.
- Reuse Motor's current deployer and MindCluster resources. Do not implement a
  competing P/D controller or generic serving engine.
- Environment preflight and deploy configuration must not mutate Kubernetes
  state. Apply, scale, delete, restart, and overwriting fixed remote source
  directories require explicit consent.
- Profiling integration is second-phase work.
- This repo targets Huawei Ascend NPU. Local machines cannot run
  `torch`/`torch_npu`-dependent code — validate on remote cluster/Pods.

## Maintenance

When changing a skill, update the whole package together: `SKILL.md`, `scripts/`,
`references/`, and supporting files. When the change affects shared state, also
update `scaffold/.agents/lib/mws_*.py` and `scaffold/.agents/scripts/` as applicable.

`scaffold/bin/motorws` is an internal skill backend only — not the product entry point.
