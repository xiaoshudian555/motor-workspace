# 目录责任

本文说明当前目录在推荐架构中的责任归属。它描述逻辑所有权，不表示已经完成
物理目录重组。

三个产品闭环见
[functional-boundaries.md](functional-boundaries.md)。三个闭环定义“最终
证明什么”，目录按实现角色组织，二者不要求一一对应。

第一部分的三阶段交接和具体功能归属见
[remote-development-and-parity.md](remote-development-and-parity.md)。
第二部分的环境、配置和实际部署三步见
[motor-deploy.md](motor-deploy.md)。

## Agent 工作流

`.agents/skills/` 是用户自然语言请求的主要入口。

| Skill | 定位 | 所在闭环或层次 |
|---|---|---|
| `repo-init` | 首次初始化本地三仓工作区 | 第一部分的前置支撑 |
| `machine-management` | 登记并验证远程 Motor 目标 | 第一部分 |
| `remote-code-parity` | 同步本地 dirty workspace 并证明远端目录内容 | 第一部分 |
| `remote-toolbox` | 尚未迁移能力的兼容入口 | 兼容层 |
| `motor-deploy-preflight` | 验证 K8s 与 MindCluster 基础环境 | 第二部分第一步 |
| `motor-deploy-configure` | 生成或复用不可变配置包并完成配置验证 | 第二部分第二步 |
| `motor-k8s-deploy` | 原样 apply 配置包并证明 Ready 和 Pod 加载目标代码 | 第二部分第三步 |
| `motor-smoke` | 用 Motor readiness 与真实推理证明服务可运行 | 第三部分 |
| `motor-benchmark` | 对成功 deploy run 执行正式 benchmark | 第三部分 |
| `motor-diagnosis` | 收集 run-scoped 失败证据；诊断目标见 [diagnosis/](diagnosis/) | 跨闭环失败处理，不属于 validation 场景 |

`remote-toolbox` 不再扩张成全能远端工作流。通用
read/edit/bash/search/job/artifact 能力属于 `.remote-dev/`；Motor 和
Kubernetes 生命周期属于对应业务 skill。

## 公共工作流实现

`.agents/lib/` 是公共实现层，不是用户主要入口。当前文件暂时保持扁平：

| 逻辑责任 | 当前实现 |
|---|---|
| 本地状态和运行目录 | `mws_local_state.py` |
| machine 固定远端路径与 PYTHONPATH | `mws_machine_target.py` |
| parity/deploy run 记录 | `mws_run_state.py` |
| 通用 JSON/lock 工具 | `mws_state.py` |
| 代码 parity | `mws_parity.py` |
| Motor 环境、配置、deploy 和 smoke 公共能力 | `mws_environment.py`（环境 preflight）、`mws_deploy.py`（configure/apply）、`mws_smoke.py`（Motor readiness/推理响应判定） |
| 结果输出 | `mws_result.py` |
| 输入和边界校验 | `mws_validate.py` |
| 历史 lock 诊断 | `mws_lock.py` |

公共运行记录、结果契约和 consent/safety 都属于公共支撑能力，不应各自
包装成独立业务 skill。

## 通用远端底座

`.remote-dev/` 负责远端原子操作以及对应的 endpoint、job、artifact 和
结果契约。

它不理解：

- Motor 角色；
- Kubernetes deploy run；
- parity 工作流；
- Pod 生命周期；
- benchmark 或 profiling。

machine 到 endpoint 的解析应由 `.agents/lib/` 中的适配层完成，
而不是让 `.remote-dev/` 反向依赖 Motor 工作流。

## 状态和证据

`.motor-workspace-local/` 保存未跟踪的本地运行状态。逻辑上应区分：

```text
machines
workspace runs
machine runs
parity runs
environment runs
config runs
deploy runs
validation runs
```

每类记录由产生它的责任单元拥有。下游通过明确引用消费上游结果，不通过
模糊的 `last_*` 文件猜测当前目标。

该目录不得保存需要协作评审的配置，也不得把密钥、token 或 kubeconfig
内容写入 tracked 文件。

## 其他目录

| 目录 | 定位 |
|---|---|
| `docs/validation/` | 第三层部署后验证场景目标（smoke…profiling）；不含 diagnosis |
| `docs/diagnosis/` | 跨闭环失败出口与诊断 skill 族目标；独立于 validation |
| `profiles/` | 可评审的硬件和 MindCluster 配置模板；不保存凭据和一次性状态 |
| `tools/build/` | 镜像构建旁路；不进入默认 parity 主路径 |
| `tools/deploy/` | 过渡期薄辅助工具；deploy skill 和共享 deploy 实现才是工作流源头 |
| `src/motor_workspace/`、`bin/motorws` | skill 使用的内部 CLI 后端，不是产品入口 |
| `tests/` | motor-workspace 自身实现和 fixture 测试，不等于集群环境验收 |
| `.claude/skills/` | Agent 适配或生成结果，不是实现 source of truth |
| `motor/`、`vllm/`、`vllm-ascend/` | 被开发和部署的源码子模块 |

实际物理重组和三步迁移尚未执行，目标与缺口统一记录在
[technical-debt.md](technical-debt.md)。
