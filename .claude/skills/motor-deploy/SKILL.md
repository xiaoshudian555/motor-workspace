<!-- Generated Claude Code shim from scaffold/.agents/skills/motor-deploy/SKILL.md. Do not edit. -->
---
name: motor-deploy
description: "Use only when the request explicitly invokes $motor-deploy, or explicitly asks to use Motor's native deployer for a named service/model in a concrete target environment and perform a live lifecycle action. Covers Motor preflight, deploy, status, restart, stop, scale, fault injection, and recovery validation. Do not trigger for generic start/deploy wording, Docker-only or RBG workflows, local scripts/guides/config examples, or code-only work."
---

# Motor Deploy Dispatcher

Canonical skill source:

`scaffold/.agents/skills/motor-deploy/SKILL.md`

Before using this skill:

1. Read the canonical skill file above.
2. Follow its routing rules, entrypoints, guardrails, and acceptance criteria.
3. Use `.remote-dev` companion tools for ordinary remote endpoint read/edit/bash/search/patch work.
4. Use this Claude project skill only for the domain workflow described by the canonical source.
