<!-- Generated Claude Code shim from scaffold/.agents/skills/motor-image-distribution-check/SKILL.md. Do not edit. -->
---
name: motor-image-distribution-check
description: Verify which cluster nodes actually have a given container image on their local runtime. Use when the user asks to check image distribution across nodes, verify an image exists on all nodes, diagnose ErrImagePull/ImagePullBackOff risk before apply, or confirm 镜像分发/镜像覆盖. The agent runs the kubectl/ssh commands below directly; this skill provides the procedure, not a script.
---

# motor-image-distribution-check

Canonical skill source:

`scaffold/.agents/skills/motor-image-distribution-check/SKILL.md`

Before using this skill:

1. Read the canonical skill file above.
2. Follow its routing rules, entrypoints, guardrails, and acceptance criteria.
3. Use `.remote-dev` companion tools for ordinary remote endpoint read/edit/bash/search/patch work.
4. Use this Claude project skill only for the domain workflow described by the canonical source.
