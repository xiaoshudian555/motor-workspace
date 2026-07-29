---
name: remote-code-parity
description: Sync local dirty tree to shared mount root session directory before deploy or verification. Not a build context or image step.
---

# remote-code-parity

Sync committed + staged + unstaged + untracked non-ignored files for motor,
vllm, and vllm-ascend to the session remote root.

## Entry point

```bash
python3 .agents/skills/remote-code-parity/scripts/parity_sync.py --session-id <id>
```

Do not run `pip install -e` on the SSH host and claim Pods are updated.
