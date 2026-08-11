# 目录责任已经收口为 Skill、远端工具和一个内部 backend

| 目录 | 责任 |
|---|---|
| `.agents/skills/` | Agent 操作说明、授权边界、通过标准和故障出口 |
| `.agents/lib/` | parity、inventory、transport、结果输出所需的共享代码 |
| `.remote-dev/` | 与 Motor 无关的通用远端原子工具 |
| `sources/` | motor、vllm、vllm-ascend 源码 submodule |
| `.motor-workspace-local/` | untracked machine inventory 和 parity state |
| `docs/` | 当前架构与操作边界；不保存历史工作单和已废弃方案 |
| `profiles/` | 可评审模板，不保存密钥或运行状态 |
| `tools/build/` | 可选镜像构建旁路说明；实际复用 Motor upstream Dockerfile/Makefile |
| `tests/` | 保留执行器与公共工具的契约测试，不测试 Skill 文案流程 |

Skill 目录不保留 Python scripts。repo-init、machine、build-wheel、deploy、smoke、
functional、benchmark、diagnosis 直接调用已有工具；只有 parity 复用 `motorws`
内部 backend。

`bin/motorws` 只提供 parity 能力，不是产品 CLI，也不得扩张成第二套部署入口。
