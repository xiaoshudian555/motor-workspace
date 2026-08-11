# 目录责任已经收口为 Skill、远端工具和一个内部 backend

| 目录 | 责任 |
|---|---|
| `.agents/skills/` | Agent 操作说明、授权边界、通过标准和故障出口 |
| `.agents/lib/` | parity、wheel、inventory、transport、结果输出所需的共享代码 |
| `.remote-dev/` | 与 Motor 无关的通用远端原子工具 |
| `sources/` | motor、vllm、vllm-ascend 源码 submodule |
| `.motor-workspace-local/` | untracked inventory、parity 和 wheel 构建证据 |
| `docs/` | 当前架构与操作边界；不保存历史工作单和已废弃方案 |
| `profiles/` | 可评审模板，不保存密钥或运行状态 |
| `tools/build/` | 可选镜像构建旁路 |
| `tests/` | 保留执行器与公共工具的契约测试，不测试 Skill 文案流程 |

Skill 目录不保留 Python scripts。repo-init、machine、deploy、smoke、functional、
benchmark、diagnosis 直接调用已有工具；parity 和 wheel 复用同一个 `motorws`
内部 backend。

`src/motor_workspace/` 与 `bin/motorws` 只提供内部 status、lock、parity 和 wheel
能力，不是产品 CLI，也不得扩张成第二套部署入口。
