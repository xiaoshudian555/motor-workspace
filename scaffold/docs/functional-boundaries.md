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

负责：

- 在 plan/apply 前执行本次部署相关的 preflight。
- 检查 kube context、namespace/RBAC、MindCluster/Volcano/CRD 和 NPU
  调度依赖。
- 检查候选节点共享目录可见性、模型路径、运行镜像和拉取条件。
- 复用 Motor upstream deployer 准备和执行部署。
- 将共享代码目录挂载到需要的 Pod。
- 配置开发态代码加载路径。
- 创建、更新、重启、停止并查看本次部署。
- 等待服务达到最基本的可用状态。
- 在 Pod 内验证实际加载的代码路径。

明确不负责：

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

Deploy preflight 消费 `machine-ready`，但二者含义不同：

- `machine-ready` 只证明机器可连接、固定远端目录可写、可以执行 parity。
- `deploy-preflight-ready` 结合本次 deploy profile、Motor user config、
  parity manifest、模型和镜像，证明已经具备尝试本次 Motor 部署的条件。

preflight 是只读检查，不创建或修改 Kubernetes 资源。它只能降低 apply
失败概率，不能代替 apply 后的 Pod、服务和运行代码验证。

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
  证明：Pod 实际运行的是目标代码
  交付：deploy run + 服务地址 + 就绪状态 + 代码加载证据
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
| `motor-k8s-deploy` | 第二部分 | 完成服务拉起和 Pod 目标代码运行证明 |
| `motor-benchmark` | 第三部分 | 对成功 deploy run 执行正式 benchmark |
| `motor-diagnosis` | 跨闭环失败处理 | 收集 run-scoped 证据，可被 Deploy 或 Validation 调用 |

公共运行记录、结果契约和 consent/safety 属于 `.agents/lib/` 支撑能力，
不单独成为业务 skill。

后续分析顺序：

1. 拆解“远程开发准备与代码同步”的内部责任边界。
2. 对照当前实现和 VAWS，判断哪些能力复用、合并或删除。
3. 再依次分析 Motor Deploy 和部署后验证。
