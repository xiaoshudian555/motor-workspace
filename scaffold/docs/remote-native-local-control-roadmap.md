# Remote-native 与 local-control 应共享一套 Workflow Core——双拓扑适配 Roadmap

> **状态：Roadmap / 待专项分支实施**  
> **日期：2026-08-03**  
> **关联技术债：** [`TD-P1-06`](technical-debt.md#td-p1-06未定义-remote-native-与-local-control-两种-agent-拓扑的统一契约)  
> **范围：** 统一 Motor Deploy 与部署后验证在两种 Agent 运行拓扑下的执行方式、源码证明和工作流状态；本文不直接修改现有实现。

## 1. 结论

motor-workspace 不应维护两套 Deploy/Validation 系统，而应形成：

```text
一套 Workflow Core
  + 两种 Execution Adapter
  + 两种 Source Adapter
  + 同一份目标侧 Workflow State
```

支持的两种拓扑为：

```text
local-control
  Agent + source workspace 位于本地 Windows/WSL/Linux
  → 通过 SSH 操作目标 K8s Host
  → 将源码同步到目标共享目录

remote-native
  Agent 直接运行在目标 Linux Host
  → 原生操作本机文件、kubectl 和 port-forward
  → 对目标共享目录生成 identity parity proof
```

两种拓扑必须向下游交付相同的 `machine-ready`、`parity-complete`、
`deploy-environment-ready`、`deploy-config-ready`、`deploy-complete` 和 validation
结果契约。Configure、Deploy、Smoke、Functional、Benchmark、Diagnosis 等业务逻辑
不得因 Agent 物理位置复制或分叉。

## 2. 目标与非目标

### 2.1 目标

1. Agent 可以在 local-control 与 remote-native 间显式切换。
2. 两种拓扑复用同一套第二部分 Motor Deploy 和第三部分 Validation workflow。
3. remote-native 不依赖“SSH 登录自己”来执行本机命令。
4. local-control 通过同步生成 parity 证据，remote-native 通过 identity 校验生成
   同结构 parity 证据。
5. 两个执行端可以读取同一条 run chain，并从对方完成的位置继续工作。
6. 切换拓扑不会误覆盖源码、误绑定 machine 或丢失 config bundle/run evidence。
7. 现有 local-control 路径在迁移过程中保持兼容，并继续保留 mutation consent。

### 2.2 非目标

- 不新增第二套 Motor `user_config.json`、`env.json` 或 deploy profile。
- 不复制 `motor-deploy-*`、`motor-smoke`、`motor-functional` 等 skill。
- 不引入通用 distributed workflow framework、调度中心或服务端数据库。
- 不恢复 VAWS managed Docker session、container sshd、NPU lease 或 per-session
  source path。
- 不改变共享 hostPath + 固定 source root + `PYTHONPATH` 的默认开发模型。
- 不以本 Roadmap 宣称当前尚未实现的 Benchmark、Correctness、Stability、
  Reliability 或 Profiling 已经可用。

## 3. 为什么不能只增加一个 transport 开关

“Agent 在哪里运行”同时影响三个彼此独立的边界：

| 边界 | 要回答的问题 | local-control | remote-native |
|---|---|---|---|
| Execution | 命令、文件、kubectl 在哪里执行 | SSH/远端执行 | 当前 Host 原生执行 |
| Source | 目标共享目录中的源码如何就绪 | sync + post-sync proof | identity/no-op proof |
| State | run、bundle、validation artifact 从哪里读取 | 目标侧统一状态 | 同一目标侧状态 |

只增加 `NativeTransport` 可以消除 self-SSH，但不能解决：

- remote-native 是否还要运行 parity sync；
- local Agent 生成的 config/deploy run 如何被 remote Agent 接手；
- 两个执行端如何证明操作的是同一 machine、同一 source root 和同一 run chain。

因此本 Roadmap 将边界收敛为 `ExecutionAdapter`、`SourceAdapter` 和
`WorkflowStateStore`，而不是把所有差异塞入一个巨型 topology adapter。

## 4. 目标架构

```text
Agent-facing Skills
  repo-init / machine-management / remote-code-parity
  motor-deploy-preflight / motor-deploy-configure / motor-k8s-deploy
  motor-smoke / motor-functional / motor-benchmark / motor-diagnosis
                              │
                              ▼
                       Workflow Core
                 deploy / validation / diagnosis
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
ExecutionAdapter         SourceAdapter       WorkflowStateStore
       │                      │                      │
  ┌────┴────┐            ┌────┴────┐            ┌────┴────┐
  │         │            │         │            │         │
 SSH      Native       Sync     Identity       Remote    Native
  │         │          Parity    Parity         Store     Store
local-    remote-      local-    remote-        同一个目标侧
control   native       control   native         state root
```

Workflow Core 只能表达业务动作，例如：

```python
context.executor.kubectl(...)
context.source.ensure_parity(...)
context.state.load_run(...)
```

不得在业务路径中扩散：

```python
if topology == "remote-native":
    deploy_remote_native()
else:
    deploy_local_control()
```

## 5. 核心契约

### 5.1 WorkflowContext

建议用一个薄的运行上下文完成依赖注入：

```python
@dataclass(frozen=True)
class WorkflowContext:
    topology: Literal["local-control", "remote-native"]
    machine: MachineRef
    executor: ExecutionAdapter
    source: SourceAdapter
    state: WorkflowStateStore
```

`WorkflowContext` 只负责装配，不拥有 Deploy 或 Validation 业务语义。创建过程必须
校验 topology、machine、source root 和 state root 的一致性。

### 5.2 ExecutionAdapter

建议的最小能力：

```python
class ExecutionAdapter(Protocol):
    def run(self, command: str) -> CommandResult: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def stage_files(self, paths: Sequence[Path], *, prefix: str) -> StagedFiles: ...
    def kubectl(self, *args: str) -> CommandResult: ...
    def port_forward(self, target: ServiceTarget) -> PortForwardHandle: ...
```

实现：

| 实现 | 行为 |
|---|---|
| `SshExecutionAdapter` | 复用唯一 SSH transport；远端执行命令、stage 文件和 kubectl；port-forward 使用远端 listener + SSH tunnel |
| `NativeExecutionAdapter` | 使用当前 Host 的 subprocess 和文件系统；直接执行 kubectl；直接建立本机 port-forward |

约束：

- Workflow Core 不得通过 `isinstance(..., SshScpTransport)` 决定业务行为。
- `SshExecutionAdapter` 与 `.remote-dev` 必须收敛到同一底层 SSH 实现，不能长期
  维护第二套 SSH 参数、重试和 path policy。
- Native 模式仍执行与 SSH 模式相同的路径校验、命令结果封装和 consent 检查。

### 5.3 SourceAdapter

统一入口：

```python
class SourceAdapter(Protocol):
    def prove_ready(self, context: WorkflowContext) -> ParityResult: ...
```

#### SyncParityAdapter

用于 local-control：

```text
本地 dirty tree
  → 明确 overwrite consent
  → 同步到 machine 固定 source paths
  → 远端内容摘要与 post-sync 校验
  → parity-complete(source_mode=sync)
```

继续支持 tracked、untracked、delete、remote drift、锁和 no-change fast path。

#### IdentityParityAdapter

用于 remote-native：

```text
当前 Agent source paths
  → 校验位于目标 machine 的 mount_root/remote_workspace_root
  → 校验固定 motor/vllm/vllm-ascend 路径
  → 计算内容摘要
  → 证明不需要复制或覆盖
  → parity-complete(source_mode=identity)
```

Identity parity 不是 `--skip-parity`。它必须发布真实证据，至少包括：

```json
{
  "kind": "parity-complete",
  "status": "ready",
  "source_mode": "identity",
  "machine": "npu-dev-01",
  "mount_root": "/mnt",
  "remote_workspace_root": "/mnt/motor-workspace",
  "source_paths": {
    "motor": "/mnt/motor-workspace/motor",
    "vllm": "/mnt/motor-workspace/vllm",
    "vllm_ascend": "/mnt/motor-workspace/vllm-ascend"
  },
  "content_digests": {}
}
```

下游只校验统一的 parity contract、machine identity、固定路径和 digest，不根据
`source_mode` 分叉 Deploy/Validation 业务逻辑。

### 5.4 WorkflowStateStore

要实现跨执行端接手，environment/config/deploy/validation run 和 config bundle
必须有同一个目标侧权威源。建议逻辑结构为：

```text
<workflow_state_root>/
  workspace-runs/
  machine-runs/
  parity-runs/
  environment-runs/
  config-runs/
  config-bundles/
  deploy-runs/
  validation-runs/
  locks/
```

具体物理路径必须在实施分支中结合 `mount_root`、权限和多 workspace 隔离要求确认，
不能在本文中把 `/mnt/motor-workspace-state` 直接冻结为硬编码默认值。

实现：

| 实现 | 行为 |
|---|---|
| `RemoteWorkflowStateStore` | local-control 通过 `ExecutionAdapter` 访问目标侧权威 state |
| `NativeWorkflowStateStore` | remote-native 直接访问相同 state root |

本地 `.motor-workspace-local/` 可以保留以下内容：

- machine endpoint inventory；
- credential reference；
- 目标侧 state 的索引或只读缓存；
- 当前 Agent 的临时日志。

以下内容不能只存在于某个执行端的本地目录：

- 被下游消费的 ready run；
- immutable config bundle；
- deploy/validation 的权威 evidence；
- 支持拓扑切换所需的 upstream refs。

安全和一致性要求：

- state 中禁止保存 token、密码、私钥或 kubeconfig 明文；
- ready run 和 config bundle 保持 immutable；
- 写入使用目标侧原子 rename 和 file lock；
- 每次读取校验 machine identity、run kind、run ID、digest 和 upstream refs；
- `created_by_topology` 只用于审计，不能成为下游业务分支条件；
- 并发执行不得覆盖已有 run 或发布半完成 evidence。

## 6. 拓扑选择与安全切换

拓扑选择优先级：

```text
用户显式选择
  → workspace/profile 中已确认的默认值
  → 自动 probe 只给出建议，不静默切换
```

不应仅凭 hostname、`/mnt` 是否存在或是否能连接 SSH 自动认定 remote-native，
因为误判可能导致源码覆盖或绑定错误 machine。

建议提供统一只读 probe，输出：

```text
topology candidate
Agent execution host identity
target machine identity
mount_root / remote_workspace_root
source paths and ownership
state root and accessibility
kubectl/context availability
recommended ExecutionAdapter / SourceAdapter / StateStore
```

切换前必须通过：

1. 当前 Agent 与目标 machine identity 可证明一致或明确不同；
2. source paths 位于允许的固定路径边界内；
3. state root 属于同一 machine/workspace binding；
4. 上游 run chain 和 bundle digest 完整；
5. 当前 topology 能生成新的合法 parity proof；
6. mutation 操作仍分别取得 overwrite/apply/restart/stop consent。

## 7. 两种拓扑下的工作流

### 7.1 local-control

```text
repo-init
→ machine-management
→ SyncParityAdapter
→ parity-complete(source_mode=sync)
→ motor-deploy-preflight
→ motor-deploy-configure
→ motor-k8s-deploy
→ validation / diagnosis
```

### 7.2 remote-native

```text
remote-native topology probe
→ machine/source/state identity validation
→ IdentityParityAdapter
→ parity-complete(source_mode=identity)
→ 同一个 motor-deploy-preflight
→ 同一个 motor-deploy-configure
→ 同一个 motor-k8s-deploy
→ 同一个 validation / diagnosis
```

### 7.3 执行端接力

```text
local Agent
  → sync parity → configure → deploy
  → 权威 run/bundle 写入目标侧 StateStore

remote Agent 接手
  → 读取同一 deploy run
  → identity parity → restart → functional/diagnosis

local Agent 再接手
  → 读取最新 run chain
  → 继续 validation 或后续 deploy
```

切换只改变 Adapter 装配，不改变已有 run 的业务含义。

## 8. 对当前代码的预期影响

以下是实施分支的候选改造面，不是本 Roadmap 对文件所有权的最终冻结：

| 当前区域 | 预期改造方向 |
|---|---|
| `scaffold/.agents/lib/mws_transport.py` | 从只有 SSH/Fake 的 transport 收敛为 ExecutionAdapter 接口或其兼容层；新增 Native 实现 |
| `scaffold/.agents/lib/mws_kubectl.py` | 移除 remote-only 假设；kubectl runner、staging、port-forward 依赖 ExecutionAdapter |
| `scaffold/.agents/lib/mws_parity.py` | 保留 sync parity；增加统一 parity contract 和 identity parity 实现 |
| `scaffold/.agents/lib/mws_run_state.py` | 从固定本地 Path 访问逐步迁移为 WorkflowStateStore |
| `scaffold/.agents/lib/mws_local_state.py` | 保留本地 inventory/credential reference；明确本地缓存与权威 workflow state 边界 |
| `scaffold/.agents/lib/mws_environment.py` | 通过 context/executor 执行 kubectl，不感知 topology |
| `scaffold/.agents/lib/mws_deploy.py` | staging、dry-run、apply、runtime proof 依赖 context/executor；不得复制 deploy 实现 |
| `scaffold/.agents/lib/mws_smoke.py` | 使用统一 port-forward handle，消除 SSH 类型判断 |
| Deploy/Validation skill scripts | 解析 topology/machine 后构造 WorkflowContext，业务参数和结果契约保持一致 |
| `.remote-dev` | 作为 SSH/direct endpoint 的底层通用能力；避免与 `.agents/lib` 长期保留两套 SSH 实现 |
| `scaffold/tests/` | 增加双拓扑 contract fixture、切换和错误绑定测试 |

实施前必须先审计当前工作树和 `TD-P0-03` 的 transport 收口进度，避免在尚未稳定
的 `.remote-dev` 与 `mws_transport.py` 之间再增加第三套实现。

## 9. 分阶段实施 Roadmap

### R0：冻结契约与建立回归基线

目标：先冻结边界，不修改业务行为。

工作项：

- 确认 machine identity、workspace binding 和 workflow state root schema；
- 冻结 `WorkflowContext`、Execution/Source/StateStore 最小接口；
- 冻结 sync/identity 两种 parity 的公共字段；
- 记录当前 local-control 测试基线；
- 建立现有 preflight/configure/apply/smoke/functional 的行为快照。

验收：

- 接口 review 通过；
- 没有新增第二套 Deploy/Validation 参数；
- 当前 local-control fixture 不回退。

### R1：抽取 ExecutionAdapter，保持 local-control 行为不变

目标：先把现有 SSH 执行包装进新接口，再增加 Native 实现。

工作项：

- 建立 `SshExecutionAdapter`；
- 建立 `NativeExecutionAdapter`；
- kubectl、file staging、deployer dry-run、apply、runtime proof 迁移到 adapter；
- port-forward 抽象为统一 handle；
- 删除 Workflow Core 对具体 SSH class 的判断。

验收：

- local-control 原有 contract fixture 全部通过；
- Native adapter 有 command/file/kubectl/port-forward 单元测试；
- 两种 adapter 输出同一 CommandResult/error contract；
- remote-native 路径不发起 self-SSH。

### R2：实现 IdentityParityAdapter

目标：remote-native 获得正式的 source readiness 证据。

工作项：

- 抽取 sync/identity 共用 parity schema；
- 实现 machine/source root identity 校验；
- 实现 fixed paths、digest、dirty/untracked 状态采集；
- remote-native 发布 immutable `parity-complete`；
- configure 只依赖公共 parity contract。

验收：

- sync parity 与 identity parity 均能被同一个 configure consumer 接受；
- source root 不一致、路径越界、machine 不匹配、digest 失败均 fail closed；
- identity 模式不复制、不覆盖、不清理固定 source paths；
- `--skip-parity` 不再承担 remote-native 正常路径。

### R3：迁移到目标侧 WorkflowStateStore

目标：两个 Agent 执行端能够读取同一条 run chain。

工作项：

- 确认 state root、workspace/machine 隔离和迁移规则；
- 实现 Remote/Native 两种 StateStore；
- 迁移 run、bundle、validation artifacts 的读写入口；
- 增加 atomic write、lock、immutable 和 digest 校验；
- 提供现有 `.motor-workspace-local/` 证据的显式迁移/导入工具；
- 明确本地 cache 失效与刷新规则。

验收：

- local-control 生成的 deploy run 可由 remote-native 读取并继续 validation；
- remote-native 生成的 deploy run 可由 local-control 读取并继续 diagnosis；
- 并发写不会产生半文件、覆盖 ready run 或 bundle collision；
- state 中不包含明文 secret；
- 旧 state 不会被静默移动或删除。

### R4：统一 topology 入口与双向接力验收

目标：把 adapter 装配变成稳定的 Agent workflow 入口。

工作项：

- 增加 topology profile/probe；
- 所有第二、三部分 skill 通过同一 factory 构造 WorkflowContext；
- 更新 Agent 文档、SKILL 和操作示例；
- 增加拓扑切换、错误 machine、错误 state root 和 consent 测试；
- 在真实 Ascend/K8s 环境完成双拓扑验收。

最低纵向验收矩阵：

| 起始执行端 | 接手执行端 | 必须验证的链路 |
|---|---|---|
| local-control | local-control | sync parity → configure → deploy → smoke |
| remote-native | remote-native | identity parity → configure → deploy → smoke |
| local-control | remote-native | deploy 完成后接手 restart → functional |
| remote-native | local-control | deploy/validation 失败后接手 diagnosis |

所有真实 mutation 仍按现有规则逐次取得明确授权。Fixture 通过不能替代真实集群
验收结论。

## 10. 兼容与迁移策略

1. 第一阶段默认继续使用 local-control，避免一次性改变现有用户路径。
2. `SshExecutionAdapter` 先包装现有行为，确认无回归后再迁移调用方。
3. remote-native 初期通过显式 topology 启用，不做自动默认切换。
4. 旧 `.motor-workspace-local/` state 只读兼容；迁移必须由显式命令执行并输出
   manifest，不得静默复制、覆盖或删除。
5. 结果 schema 如需升级，必须同时提供旧 run 的读取策略；不能要求所有历史
   deploy/config evidence 立即失效。
6. topology 切换不得改变 Motor 原生配置、config fingerprint 或 bundle digest
   计算语义；只有确实影响配置内容的字段才能进入 fingerprint。

## 11. 风险与控制措施

| 风险 | 后果 | 控制措施 |
|---|---|---|
| topology 自动误判 | 覆盖错误机器上的 source root | 显式选择优先；probe 只建议；machine/source identity fail closed |
| 两个 Agent 同时写 state | run 或 bundle 损坏 | 目标侧 lock、atomic rename、immutable ready run |
| topology 进入业务分支 | 两套 Deploy/Validation 行为漂移 | topology 只参与 adapter factory；核心 consumer contract 测试 |
| StateStore 包含 secret | 凭据泄漏到共享盘 | state schema 禁止明文 secret；仅保存 reference/脱敏摘要 |
| Native 模式绕过路径安全 | 当前 Host 文件被越界修改 | Native 与 SSH 共用 path policy 和允许根目录 |
| Identity parity 被当作无条件 no-op | 未证明源码就进入 Deploy | 必须计算路径、machine 和 digest 证据，失败不发布 ready |
| `.remote-dev` 与 `.agents/lib` 重复 transport | 参数、重试和安全策略漂移 | R0/R1 先确定唯一底层 transport 所有权 |
| 共享 state root 隔离不足 | 不同 workspace/run 串链 | machine/workspace binding 写入并校验每条 run |

## 12. 开工前待确认决策

下列问题应在专项分支 R0 阶段确认，不能由实现者静默猜测：

1. 目标侧 `workflow_state_root` 的最终默认路径和权限模型。
2. 一个 machine 是否允许绑定多个 motor-workspace；若允许，state 隔离键是什么。
3. remote-native 的 meta-repository 根目录与固定 runtime source root 是否必须相同；
   若不同，二者的显式映射如何记录。
4. local machine inventory 与目标侧 machine identity 的权威字段是什么。
5. 旧 `.motor-workspace-local/` run/bundle 的迁移保留周期和读取兼容范围。
6. `.remote-dev` 与 `mws_transport.py` 的最终所有权：谁提供唯一 SSH transport，
   谁只负责 Motor machine adapter。

## 13. Roadmap 完成定义

只有同时满足以下条件，才能宣布双拓扑兼容完成：

```text
同一套 Deploy/Validation consumer
+ SSH 与 Native 两种 ExecutionAdapter
+ sync 与 identity 两种 parity proof
+ 两个执行端共享同一条权威 run chain
+ local → remote 和 remote → local 接力测试
+ 错误 machine/source/state 绑定 fail closed
+ mutation consent 与 secret 边界不回退
+ 真实 Ascend/K8s 双拓扑纵向验收
```

仅仅做到“远程 Agent 可以 SSH 自己并运行脚本”，不属于本 Roadmap 的完成状态。
