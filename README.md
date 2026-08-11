# motor-workspace

MindIE Motor + vLLM + vLLM Ascend 的远端开发工作区。它复用 Motor 原生 deployer，
不实现第二套部署平台。

## 主流程

```text
Git/gh 初始化
→ machine inventory + remote.* 当前状态检查
→ remote-code-parity（仅 local-control 需要）
→ motor-build-wheel（仅 Motor 包替换需要）
→ 原生 user_config.json + env.json
→ deploy.py --dry-run
→ 用户授权
→ deploy.py
→ kubectl / readiness / functional / benchmark / diagnosis
```

Skill 默认直接调用 Git、`gh`、`.remote-dev`、`kubectl` 和 Motor upstream
deployer。Skill 目录不再保留 scripts；parity 的复杂算法由
`scaffold/bin/motorws parity` 提供；wheel 构建按 `motor-build-wheel` skill 用
远端 docker/`build.sh` 和原子远端工具直接完成。

## 固定远端目录

```text
/mnt/motor-workspace/motor
/mnt/motor-workspace/vllm
/mnt/motor-workspace/vllm-ascend
/mnt/motor-workspace/python-overlay
```

这些目录用于内容证明和 wheel 构建。Pod 运行时使用 image package，或由
`boot.sh` 安装显式构建的 Motor wheel；禁止源码树 `PYTHONPATH`。

## 目录

```text
sources/                       上游源码 submodule
scaffold/.agents/skills/       Agent 工作流说明
scaffold/.agents/lib/          parity、inventory 等保留实现
scaffold/.remote-dev/          通用远端原子工具
scaffold/profiles/             可评审模板
scaffold/tools/build/          可选 image build bypass 说明
scaffold/docs/                 当前架构和操作边界
.motor-workspace-local/        ignored machine inventory、parity state
```

## Quick start

```bash
git submodule update --init
```

随后让 Agent 读取对应 Skill。repo-init、machine、deploy、smoke、functional、
benchmark 和 diagnosis 没有仓库 wrapper script。

## 安全边界

- 不把凭据、kubeconfig 内容、token 或本地 inventory 写进 tracked 文件；
- parity 覆盖、配置修改、apply、restart、stop 分别需要明确授权；
- dirty tree 可参与 parity，不要求日常改动 commit；
- 本地机器不能验证 `torch`/`torch_npu` runtime，真实运行验证在远端 Host/Pod；
- `scaffold/bin/motorws` 只是内部 Skill backend，不是产品 CLI。

详见 [architecture](scaffold/docs/architecture.md)、
[functional boundaries](scaffold/docs/functional-boundaries.md) 和
[Motor deploy](scaffold/docs/motor-deploy.md)。
