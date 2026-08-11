<!-- Generated Claude Code shim from scaffold/.agents/skills/motor-config-edit/SKILL.md. Do not edit. -->
---
name: motor-config-edit
description: Translate deployment intent into a validated Motor native user_config.json + env.json. Use when the user says things like 开启精度检测、开 XX 开关、起 qwen-72B 8卡、验证 XX 功能、改下模型、帮我生成部署配置, or asks to edit Motor deploy configuration fields. Output is a native config directory ready for motor-deploy-configure.
---

# motor-config-edit

Canonical skill source:

`scaffold/.agents/skills/motor-config-edit/SKILL.md`

Before using this skill:

1. Read the canonical skill file above.
2. Follow its routing rules, entrypoints, guardrails, and acceptance criteria.
3. Use `.remote-dev` companion tools for ordinary remote endpoint read/edit/bash/search/patch work.
4. Use this Claude project skill only for the domain workflow described by the canonical source.
