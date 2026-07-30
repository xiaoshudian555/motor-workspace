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

No snapshot directories, no `current` symlink, no Git commit requirement.

## Prerequisites

- Successful `machine-ready` evidence from `machine-management verify`
  (stored under `.motor-workspace-local/machine-runs/`).
- User consent to overwrite existing remote fixed directories.

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
