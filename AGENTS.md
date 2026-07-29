# Repository instructions

Local `motor` + `vllm` + `vllm-ascend` development scaffold. All three are Git submodules.

This repository provides a remote development substrate first, then Motor domain
skills on top. The primary development path is **remote-code-parity + shared
mount root hostPath into Pods**, not rebuilding an image for every code change.

## Remote development model

Use native client tools for local files and local shell work.

Use `.remote-dev` remote companion tools for remote endpoints on the shared mount
root (default `/mnt`, configurable via profile `mount_root`):

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
- `root`, default session shared mount root
- `cwd`, default session workspace directory

Prefer `host + port` direct endpoints for ordinary remote development.
`session_id`, `session_file`, and `machine` remain managed-session paths.

## Skills

Repo-local skills live under `.agents/skills/`. Each has its own `SKILL.md` —
read that before invoking.

| Skill | Purpose |
|-------|---------|
| `repo-init` | Initialize workspace: gh, GitHub auth, submodules, fork topology, lock |
| `machine-management` | Add / verify / repair / remove remote NPU machine + kube context + mount root |
| `session-management` | Create / inspect / remove isolated sessions (worktree + remote dir + leases) |
| `remote-toolbox` | Managed session target/probe/exec/job/sync/artifact/cleanup backend |
| `remote-code-parity` | Sync local dirty tree to shared mount root before deploy/verify |
| `motor-k8s-deploy` | Plan / apply / status / stop Motor on Kubernetes via upstream deployer |
| `motor-benchmark` | Benchmark a successful deploy run (second phase) |
| `motor-diagnosis` | Collect run-scoped deploy/diagnostic artifacts (second phase) |

None of these are gates for normal local coding or unrelated Git tasks.
For remote endpoint work, prefer `.remote-dev` tools first and use skills for
domain workflows.

## Repo-wide rules

- Never write secrets, passwords, or tokens into tracked files.
- Keep runtime state under `.motor-workspace-local/` and remote-dev state under
  `.remote-dev/state/`. Both are untracked.
- Keep `.gitmodules` on community upstream URLs.
- Prefer `.remote-dev` or skill wrapper scripts over raw SSH for remote work.
- Skill wrappers: progress on `stderr`, final JSON on `stdout`.
- For parallel managed remote work, create or reuse a `session-management`
  session and pass `--session-id` through parity and deploy commands.
- Development parity syncs source to the shared mount root; Pods pick it up via
  existing hostPath (default `/mnt:/mnt`) and injected `PYTHONPATH`. Image
  rebuild is an optional bypass for release/delivery, not the default loop.
- Reuse Motor's current deployer and MindCluster resources. Do not implement a
  competing P/D controller or generic serving engine.
- Preflight and plan are read-only by default. Apply, scale, delete, rollback,
  and overwriting remote session directories require explicit consent.
- Profiling integration is second-phase work.
- This repo targets Huawei Ascend NPU. Local machines cannot run
  `torch`/`torch_npu`-dependent code — validate on remote cluster/Pods.

## Maintenance

When changing a skill, update the whole package together: `SKILL.md`, `scripts/`,
`references/`, and supporting files. When the change affects shared state, also
update `.agents/lib/mws_*.py` and `.agents/scripts/` as applicable.

`bin/motorws` is an internal skill backend only — not the product entry point.
