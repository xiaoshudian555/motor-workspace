# reserve/ — 隔离储备代码

本目录存放**已从 Motor 默认工作流摘除、但不删除**的技术储备代码。这些代码
不构成 Motor 默认发现、路由、测试或验收的一部分，也不被任何主路径 import。

## 目录

| 目录 | 来源 | 内容 |
|---|---|---|
| `vaws-docker-session/` | 2026-07-31 VAWS 迁移（commit `3db99cd` 前后） | VAWS managed Docker session 整套体系 |

`vaws-docker-session/` 内含：

- `agents-skills-session-management/`：原 `.agents/skills/session-management`，
  local worktree + remote container + SSH/service port/NPU lease
- `claude-skills-session-management/`：对应的 Claude shim
- `agents-lib/`：`mws_session_id.py`、`mws_session_state.py`、
  `mws_remote_toolbox.py`（2255 行 managed target 解析 + 容器 endpoint）
- `agents-scripts/`：原 `.agents/scripts/` 20 个 remote_* CLI 薄壳
  （probe/exec/job/service/artifact/cleanup 的 managed session 入口）
- `tests/`：原 `.agents/tests/test_mws_scaffold_safety.py`

## 使用约束

1. **不要 import**：主路径（`.agents/skills`、`.agents/lib`、`.remote-dev`、
   `tools`、`src`）不得 import 本目录任何模块。
2. **不在默认发现路径**：本目录下的 SKILL.md 不应被 Agent 路由命中；如需启用，
   必须显式移回 `.agents/skills/` 并重新评估与 direct-host 模型的冲突。
3. **未来如需 docker-only 场景**：按 technical-debt.md TD-P0-03 的结论，应基于
   K8s namespace/job + shared mount 重新设计，只能把本目录代码当作实现参考，
   不能原样恢复 VAWS Docker session 语义。
