---
name: remote-code-parity
description: Sync local dirty tree to fixed remote directories under the shared mount root before deploy or verification. Not a build context or image step.
---

# remote-code-parity

Sync committed + staged + unstaged + untracked non-ignored files for motor,
vllm, and vllm-ascend to the machine's fixed remote directories:

```text
/mnt/motor-workspace/motor
/mnt/motor-workspace/vllm
/mnt/motor-workspace/vllm-ascend
/mnt/motor-workspace/python-overlay
```

Mechanism: each repo's dirty working tree is captured as a git synthetic
snapshot (temp-index `read-tree HEAD` + `add -A` + `write-tree` + `commit-tree`),
transferred incrementally as a `git bundle` into a bare mirror
(`{remote_workspace_root}/.mws-mirrors/<repo>.git`), and materialized into the
fixed directory via `checkout -f -B parity/current` + `reset --hard` +
`clean -ffd`. No snapshot directories, no `current` symlink, and no requirement
to `git commit` your local changes. First sync is the full tree; later syncs
transfer only the object delta.

## Prerequisites

- Successful `machine-ready` evidence from `machine-management verify`
  (stored under `.motor-workspace-local/machine-runs/`).
- User consent to overwrite existing remote fixed directories (sync parity
  only; identity parity never overwrites).

## Entry point

```bash
python3 .agents/skills/remote-code-parity/scripts/parity_sync.py \
  --machine <alias> \
  --approved-overwrite
```

Optional:

- `--machine-run-id <id>` — pin a specific machine-ready run instead of the
  latest successful run for the machine.
- `--skip-fast-path` — force full sync even when local and remote digests match.

## Remote-native identity parity

When the machine record has `executor: native` (the Agent runs directly on the
target NPU host), the local working tree **is** the machine's fixed source
paths, so there is nothing to sync. Run identity parity instead:

```bash
python3 .agents/skills/remote-code-parity/scripts/parity_identity.py \
  --machine <alias>
```

Identity parity proves source readiness without copying or overwriting:

- each local source repo resolves to the machine's fixed source dir
- the fixed source dirs exist on the current host
- content digests are captured and published as an immutable
  `parity-complete(source_mode=identity)` run

It fails closed: a local repo that does not resolve to the fixed source dir, a
missing fixed dir, or a digest failure never publishes a ready proof. No
`--approved-overwrite` is accepted — there is no overwrite.

## Deliverables

- Parity run record under `.motor-workspace-local/parity-runs/{parity_run_id}/`
- Manifest with local source digests, fixed remote paths, post-sync remote
  content digests, and remote proof results
- `parity_complete: true` only when sync and post-sync proof both succeed

## Out of scope

- `pip install -e`, editable install, native rebuild, or package replacement
- Pod `PYTHONPATH`, hostPath mount verification, or in-Pod import proof
- Deploy, preflight, or configure steps

Do not run `pip install -e` on the SSH host and claim Pods are updated.
