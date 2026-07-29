---
name: motor-diagnosis
description: Collect run-scoped Motor deploy diagnostic artifacts (second phase).
---

# motor-diagnosis

Second-phase skill. Archives manifest, Pod/Event/log evidence for a deploy run.

```bash
python3 .agents/skills/motor-diagnosis/scripts/diagnosis_collect.py --session-id <id> --profile profiles/a2-dev.yaml
```
