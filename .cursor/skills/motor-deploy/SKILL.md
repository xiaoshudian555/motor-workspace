---
name: motor-deploy
description: "Use only when the request explicitly invokes $motor-deploy, or explicitly asks to use Motor's native deployer for a named service/model in a concrete target environment and perform a live lifecycle action. Covers Motor preflight, deploy, status, restart, stop, scale, fault injection, and recovery validation. Do not trigger for generic start/deploy wording, Docker-only or RBG workflows, local scripts/guides/config examples, or code-only work."
---

# Motor Deploy Dispatcher（motor-workspace Cursor 入口）

用户全局 catalog 已隐藏 `motor-deploy`（live 源 `disable-model-invocation: true`），避免漏进 VAWS / doc 等仓库。本文件只在 **motor-workspace** 被 Cursor 发现。

Canonical skill source:

`scaffold/.agents/skills/motor-deploy/SKILL.md`

Live authoring source（改执行合同改这里，再同步本仓库镜像）:

`~/.hermes/skills/local/motor-deploy/SKILL.md`

Before using this skill:

1. Read the canonical skill file above.
2. Follow its routing rules, entrypoints, guardrails, and acceptance criteria.
3. Use `.remote-dev` companion tools for ordinary remote endpoint read/edit/bash/search/patch work.
4. Apply the canonical three-anchor entry gate before selecting any atomic deploy or validation Skill.
