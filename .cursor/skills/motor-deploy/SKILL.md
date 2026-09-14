---
name: motor-deploy
description: "Motor deployment dispatcher for 拉起一个服务, 启动/部署/重启/停止/查看 Motor, 部署前检查, 检查部署环境, wheel/whl 替换, 构造故障, 故障注入, or 验证故障恢复. Route to repo-local skills and Motor's native deployer."
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
4. Do not select an atomic deploy or validation Skill before this dispatcher.
