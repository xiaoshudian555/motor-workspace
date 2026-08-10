---
name: motor-deploy
description: "Thin dispatcher for Motor deployment work in the motor-workspace repository. Use for service launch and lifecycle requests such as 拉起一个服务, 拉起/启动/部署 Motor, apply 部署, 重启/停止/查看 Motor 服务; read-only feasibility requests such as 能不能起服务, 是否具备部署条件, 部署前检查, 检查部署环境; config preparation and post-deploy readiness; and reliability wording such as 构造故障, 故障注入, 验证故障恢复, which must stop as unsupported instead of routing to adjacent validators."
---

<!-- Generated Claude Code shim from scaffold/.agents/skills/motor-deploy/SKILL.md. Do not edit. -->

# motor-deploy

Canonical workspace mirror:

`scaffold/.agents/skills/motor-deploy/SKILL.md`

Before using this skill:

1. Read the canonical workspace mirror above completely.
2. Follow its routing, consent, feasibility, and unsupported-capability boundaries.
3. Load only the repo-local atomic skills selected by that dispatcher.
4. Never fall back to a standalone `examples/deployer/deploy.py` workflow.
