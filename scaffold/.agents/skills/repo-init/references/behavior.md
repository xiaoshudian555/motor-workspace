# Repo-init behavior reference (motor-workspace)

## Core contract

- Probe first; probe never mutates remotes, submodules, or local profile files.
- Ask before each mutation category; apply scripts require explicit consent flags.
- Preserve user choices and extra remotes.
- Keep user-specific topology local, not tracked.
- Prefer helper scripts over ad-hoc shell pipelines.

## Local-state contract

Repo-local runtime state lives under `.motor-workspace-local/`.

Repo-init may read `workspace_id` from the profile but must not create or modify
the profile during probe (`load_profile(persist_missing=False)`).

## Stage model

### Stage 1: read-only probe

`repo_init_probe.py` collects:

- platform and tool availability (`git`, `gh`, …)
- GitHub CLI install and auth state
- recursive submodule status
- lock alignment via `verify_lock()` (diagnostic, not a deploy gate)
- remote topology for workspace, motor, vllm, vllm-ascend
- GitHub fork hints for vllm / vllm-ascend when authenticated

### Stage 2: decision checkpoint

Before mutating a broad init or topology-changing task, stop once and ask:

- repo topology mode: keep current, recommended fork mode, or community-only
- whether to initialize submodules now

Machine username/profile and vLLM CI pin alignment are out of scope for repo-init.

### Stage 3: apply with consent flags

`repo_init_apply.py`:

- `--submodules` — sync + init direct workspace submodules
- `--recursive-submodules` — also initialize nested third-party submodules
- `--configure-remotes --repo <role> [--origin-url …] [--upstream-url …]` — conservative origin/upstream updates

`repo_topology.py` provides lower-level compare/configure/ensure-main helpers.

Apply order for broad init:

1. direct workspace submodule init
2. remote rewiring for workspace (if approved)
3. remote rewiring for motor / vllm / vllm-ascend (only after submodule init)

## Remote topology rules

- Only `origin` and `upstream` are configured by apply helpers.
- Extra remotes are never removed.
- Re-running configure with the same URLs is a no-op.
- Submodule repos must be initialized before `configure --repo` targets them.
