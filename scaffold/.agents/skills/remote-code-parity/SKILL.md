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

## Entry point

```bash
python3 .agents/skills/remote-code-parity/scripts/parity_sync.py \
  --machine <alias> \
  --approved-overwrite
```

Do not run `pip install -e` on the SSH host and claim Pods are updated.
