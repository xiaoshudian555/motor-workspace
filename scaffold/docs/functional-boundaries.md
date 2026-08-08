# 功能边界

本文先按用户工作流划分 motor-workspace 的一级责任边界。这里定义的是
“每一部分最终要证明什么”，不是 skill、脚本或内部模块的拆分方案。

当前确定三个主要部分：

## 1. 远程开发准备与代码同步

**核心目标：** 让远端具备继续开发和部署目标代码的条件。

本部分进一步拆成 `repo-init → machine-management →
remote-code-parity` 三个阶段。详细功能归属见
[remote-development-and-parity.md](remote-development-and-parity.md)。

负责：

- 连接并识别目标机器。
- 检查远程开发依赖的基础环境状态。
- 建立本地 workspace 到远端共享源码根的一对一固定映射。
- 将本地当前工作区（包括未提交修改）同步到远端共享目录。
- 交付本地与远端内容一致的证据。

明确不负责：

- 拉起 Motor 服务。
- 声明 Pod 已经使用同步后的代码。
- 执行功能、性能或 profiling 测试。

完成标志：

> 目标代码已正确出现在远端指定目录，但尚未承诺任何 Pod 正在使用它。

向下一部分交付：

- 确定的远端代码目录。
- 本次代码同步结果及一致性证据。
- 后续部署所需的目标机器和 parity run 引用。

## 2. Motor Deploy

**核心目标：** 拉起 Motor 服务，并证明 Pod 实际运行的是上一部分交付的
目标代码。

第二部分与第一部分一样，固定拆成四个有独立名称、独立入口、
独立完成标准和独立交付物的步骤：

详细功能归属见 [motor-deploy.md](motor-deploy.md)。

```text
1. motor-config-edit
    → 交付完整的 Motor 原生 user_config.json + env.json 配置目录

2. motor-deploy-preflight
    → 交付 deploy-environment-ready

3. motor-deploy-configure
    → 交付 deploy-config-ready + 不可变配置包

4. motor-k8s-deploy
    → 交付 deploy run + Ready + 运行代码证据
```

面向用户的“一次完成部署”工作流可以依次调用四个步骤，但这只是编排关系，
不能把四个步骤的职责、结果或失败边界合并。

### 2.1 Motor 部署配置生成

责任单元（目标 skill）：`motor-config-edit`

**核心目标：** 把用户「要起什么、开哪些配置」的意图翻译成完整、合法的 Motor
原生 `user_config.json` + `env.json`。

负责：

- 从意图解析要改的字段（部署形态 + 特性开关），关键字段首次必问，不猜默认值。
- 字段出处以 Motor 官方 `config_reference.md` 和 `config_sample.json` 为准，
  feature 映射表快路径命中优先；未命中再搜源码，搜不到则停问用户，不发明字段。
- 复制模板或已有配置到 `generated/<job_id>/`，在副本上修改，不碰原件。
- 自检关键字段（`image_name`/`hardware_type`/`job_id`/`weight_mount_path` 非空、
  PD 的 `kv_role`/`kv_port` 配对、`tensor_parallel_size <= Pod NPU 数`、
  `env.json` 两节 env）。
- 交付字段 diff 和每个改动的源码出处。

明确不负责：

- 部署、dry-run、hostPath/`PYTHONPATH` 注入或 server-side 校验（属第三、四步）。
- 创建 namespace、修改 Kubernetes 资源。

完成标志：

> 已产出一份完整、自检通过的 Motor 原生配置，可以进入环境验证与配置准备。

本步骤产出配置目录而非 `*-ready` run 记录，作为第四步 configure 的输入引用
被绑定。

### 2.2 K8s 与 MindCluster 环境前置验证

责任单元（目标 skill）：`motor-deploy-preflight`

**核心目标：** 判断目标 K8s、MindCluster、Volcano 和 Ascend NPU 基础
环境是否可用，是否可以开始准备 Motor 部署配置。

负责：

- 消费成功的 `machine-ready`、kube context 和 workspace 固定的环境契约。
- 检查 Kubernetes API、基础读取权限、MindCluster/Volcano 组件、CRD、
  scheduler、device plugin 和集群报告的 NPU resource 类型。
- 记录集群身份、组件版本、检查时间和环境契约版本。
- `warning` 记录后继续；`error` 或 `unavailable` 立即中断。

明确不负责：

- 读取或验证 Motor user config、parity、namespace、模型或镜像。
- 生成、替换、校验或 dry-run Motor/Kubernetes 配置。
- 判断本次配置的精确 RBAC、调度、候选节点或 hostPath 条件。
- 创建、修改或删除 Kubernetes 资源。
- 声明任何一份 Motor 配置可以 apply。

完成标志：

> K8s 与 MindCluster 基础环境可用，可以进入 Motor 部署配置准备。

交付：

- 独立的 `deploy-environment-ready` 和环境检查证据。
- machine、kube context、环境契约版本和集群身份引用。
- 本结果只允许同一工作流后续步骤引用，不跨工作流复用。

### 2.3 Motor 部署配置准备与验证

责任单元（目标 skill）：`motor-deploy-configure`

**核心目标：** 基于可用环境和第一部分交付的目标代码，生成或复用一份替换
正确、通过 dry-run、可供下一步原样 apply 的不可变配置包。

负责：

- 消费同一工作流的 `deploy-environment-ready`、machine、parity，以及 Motor
  原生 `user_config.json` 和 `env.json`。
- 调用 Motor upstream deployer dry-run，在 staging 中生成本次配置。
- namespace 只取 `motor_deploy_config.job_id`，要求已经存在；workspace 不
  提供 deploy profile 或字段级 CLI override。
- 完成共享 hostPath、volumeMount 和 `PYTHONPATH` 注入。
- 验证最终 manifest、精确 RBAC、结构路径和代码路径映射。
- 执行 manifest 校验和 Kubernetes server-side dry-run。
- 通过明确的 fingerprint 判断历史配置包能否复用。
- 交付不可变配置包、diff、dry-run 和配置—parity 对应证据。

明确不负责：

- 重新承担第二步的环境基线检查。
- apply、restart、stop 或删除 Kubernetes 资源。
- 等待 Pod Ready 或证明 Pod 实际加载目标代码。
- 重新同步或制造 parity 结果。
- apply 前验证镜像实际拉取、模型容器内可读或候选节点 hostPath 可见。
- 创建临时诊断 Pod/Job。

完成标志：

> 最终配置已经生成或经 fingerprint 证明可复用，所有必需替换和 dry-run
> 通过，可以原样交给实际部署步骤。

交付：

- 独立的 `deploy-config-ready`。
- immutable config bundle、`config_fingerprint`、最终 manifest 和 diff。
- 当前 parity 与最终 hostPath/`PYTHONPATH` 的对应证据。

### 2.4 Motor 实际部署与运行验收

责任单元：`motor-k8s-deploy`

**核心目标：** 原样 apply 已通过验证的配置包，等待 Motor 达到 Ready，并
证明 Pod 实际运行目标代码。

负责：

- 校验并原样 apply `deploy-config-ready` 引用的不可变配置包。
- 创建、更新、重启、停止并查看本次部署。
- 等待 Motor 关键资源、组件和 Pod 达到约定 Ready 状态。
- 验证服务最小可访问性。
- 在 Pod 内验证实际加载的代码路径并与当前 parity 对应。
- 保存部署状态转换、失败位置和运行证据。

明确不负责：

- 重新执行环境前置验证。
- 重新 render、替换或 dry-run 配置。
- 再次定义或制造代码同步结果。
- 以正式 workload 判断功能和性能是否达标。
- 将 benchmark 或 profiling 结果算作部署成功条件。
- 重写 Motor 的 P/D 控制器或现有部署语义。

完成标志：

> Motor 服务已经拉起，并有运行时证据证明 Pod 使用的是目标代码。

向下一部分交付：

- 可识别的部署运行记录。
- 服务访问地址。
- Pod 就绪状态和目标代码加载证据。
- 后续验证所需的日志、资源和运行引用。

部署阶段可以包含最小连通性探测，但它只用于证明服务基本可访问，不代替
正式验证。

第二部分第二步（preflight）消费 `machine-ready`，但二者含义不同：

- `machine-ready` 只证明机器可连接、固定远端目录可写、可以执行 parity。
- `deploy-environment-ready` 证明目标 K8s 与 MindCluster 基础环境可用。
- `deploy-config-ready` 才结合 parity 固定路径和 Motor 原生配置证明本次配置
  可以交给 apply。

前三步（配置生成、环境验证、配置准备）不修改 Kubernetes 状态。第四步取得
consent 后才 apply，并承担 Pod、服务和运行代码验证。

## 3. 部署后验证与测试

**核心目标：** 判断已经运行的服务在指定场景下是否正确、性能如何，以及
失败时留下足够的分析证据。

负责：

- 功能 smoke 和正确性验证。
- benchmark。
- profiling。
- 验证失败时的诊断材料收集。
- 保存可判断、可追溯的测试结果。

明确不负责：

- 同步本地代码。
- 创建远端开发目录。
- 代替 Deploy 修复挂载、代码加载或 Pod 拉起问题。
- 在没有成功部署运行记录时假定服务已经可用。

完成标志：

> 指定测试已经执行，并交付可判断、可追溯的结果或失败证据。

## 三部分的交接关系

```text
远程开发准备与代码同步
  证明：远端目录中是目标代码
  交付：目标目录 + 同步结果 + 一致性证据
                         |
                         v
Motor Deploy
  第一步证明：产出一份完整合法的 Motor 原生配置
  第一步交付：user_config.json + env.json 配置目录
                         |
                         v
  第二步证明：K8s 与 MindCluster 基础环境可用
  第二步交付：deploy-environment-ready + 环境证据
                         |
                         v
  第三步证明：最终部署配置正确并可供原样 apply
  第三步交付：deploy-config-ready + immutable config bundle
                         |
                         v
  第四步证明：Motor Ready 且 Pod 实际运行目标代码
  第四步交付：deploy run + 服务地址 + Ready + 代码加载证据
                         |
                         v
部署后验证与测试
  证明：运行中的服务满足指定测试要求
  交付：smoke / benchmark / profiling / 诊断结果
```

边界判断的关键区别：

- “文件已经同步正确”属于第一部分。
- “Pod 实际加载了这些文件”属于第二部分。
- “加载这些文件的服务通过了指定测试”属于第三部分。

## 与内部支撑能力的关系

`repo-init`、machine、`.remote-dev`、运行记录、结果契约以及
consent/safety 都可能支撑上述闭环，但不因此自动成为同级业务闭环。

这些能力如何拆分、由哪个 skill 或内部模块承载，将在逐块分析时决定。当前
文档不预先固定它们的实现边界，也不把 VAWS 已有 skill 列表直接映射为
Motor 的责任单元。

## 推荐实现分层

三个业务闭环定义“最终要证明什么”，目录则按实现角色组织。二者不要求
一一对应。

```text
业务闭环
  远程开发准备与代码同步 → Motor Deploy → 部署后验证与测试

Agent 工作流入口
  .agents/skills/

公共工作流实现
  .agents/lib/

通用远端原子操作
  .remote-dev/

本地状态和运行证据
  .motor-workspace-local/
```

目录责任建议：

| 目录 | 责任 | 不负责 |
|---|---|---|
| `.agents/skills/` | 面向用户自然语言请求的工作流入口 | 承载所有公共底层实现 |
| `.agents/lib/` | machine adapter、parity、deploy、validation、状态、结果与安全等共享实现 | 成为用户主要入口 |
| `.remote-dev/` | 远端 read/edit/bash/search/job/artifact 等通用原子操作 | 理解 Motor、parity 或 Kubernetes 部署语义 |
| `.motor-workspace-local/` | 保存未跟踪的机器和各类 run 证据 | 保存源码、密钥或需要协作评审的配置 |
| `profiles/` | 保存可评审的硬件和 MindCluster 配置模板 | 保存凭据和一次性运行状态 |
| `tools/build/` | 镜像构建旁路 | 进入默认开发闭环 |

当前 skill 的建议定位：

| Skill | 所在闭环或层次 | 定位 |
|---|---|---|
| `repo-init` | 第一部分的前置支撑 | 独立的首次初始化工作流 |
| `machine-management` | 第一部分 | 独立的机器登记与环境验证工作流 |
| `remote-code-parity` | 第一部分 | 完成本地到远端共享目录的代码一致性闭环 |
| `remote-toolbox` | 兼容层 | 仅保留尚未被 `.remote-dev` 和内部 adapter 覆盖的能力 |
| `motor-config-edit`（目标 skill） | 第二部分第一步 | 把用户意图翻译为完整的 Motor 原生 `user_config.json` + `env.json` 配置目录 |
| `motor-deploy-preflight`（目标 skill） | 第二部分第二步 | 独立检查 K8s 与 MindCluster 基础环境，交付 `deploy-environment-ready` |
| `motor-deploy-configure`（目标 skill） | 第二部分第三步 | 生成或复用配置，完成替换、dry-run 和配置—代码对应验证，交付 `deploy-config-ready` |
| `motor-k8s-deploy` | 第二部分第四步 | 原样 apply 配置包，等待 Ready 并证明 Pod 运行目标代码 |
| `motor-smoke` | 第三部分 | 只校验 Coordinator management readiness 响应体为 `ready=true` |
| `motor-functional` | 第三部分 | 执行真实 inference 请求并验证 metrics、tracing 等功能语义 |
| `motor-benchmark` | 第三部分 | 对成功 deploy run 执行正式 benchmark |
| `motor-diagnosis` | 跨闭环失败处理（见 [diagnosis/](diagnosis/)，不属于 validation 场景） | 收集 run-scoped 证据和 deploy 对应的 upstream `auto_log_collect` session，可被 Deploy 或 Validation 调用 |
| `motor-diagnosis-controller-recovery-terminate` | `motor-diagnosis` 的 PyMotor 专项诊断 | 按 Coordinator → Controller → Recovery → NodeManager 证据链定位 precision terminate 失败 |

公共运行记录、结果契约和 consent/safety 属于 `.agents/lib/` 支撑能力，
不单独成为业务 skill。

后续分析顺序：

1. 拆解“远程开发准备与代码同步”的内部责任边界。
2. 对照当前实现和 VAWS，判断哪些能力复用、合并或删除。
3. 再依次分析 Motor Deploy 和部署后验证。
