# 第二部分：Motor 环境前置验证、配置准备与实际部署

第二部分从第一部分交付远端目标代码开始，到 Motor 服务已经拉起、达到约定
Ready 状态并证明 Pod 使用目标代码结束。

本部分固定拆成三个完整步骤，与第一部分
`repo-init → machine-management → remote-code-parity` 的拆分方式相同。三个
步骤使用独立顺序编号，也不是一个 skill 内部的三个函数：

```text
1. K8s 与 MindCluster 环境前置验证
   motor-deploy-preflight
        ↓ 交付：deploy-environment-ready + 环境检查证据

2. Motor 部署配置准备与验证
   motor-deploy-configure
        ↓ 交付：deploy-config-ready + 可 apply 的配置包

3. Motor 实际部署与运行验收
   motor-k8s-deploy
        ↓ 交付：deploy run + Ready 证据 + 运行代码证据
```

三个步骤有独立职责、独立入口、独立结果和独立失败边界。面向用户的“一次完成
部署”工作流可以顺序调用三个步骤，但不得合并它们的责任、结果或证据。

## 1. K8s 与 MindCluster 环境前置验证

责任单元（目标 skill）：`motor-deploy-preflight`

核心问题：

> 当前目标 K8s 与 MindCluster 基础环境是否可用，足以进入某次 Motor 配置
> 准备？

### 输入

- 成功的 `machine-ready` 结果和 machine-ref。
- kube context 引用。
- workspace 固定版本的 K8s、MindCluster、Volcano 和 Ascend NPU 环境契约。

本步骤不消费 parity manifest、Motor user config、namespace、模型路径、
镜像引用或最终 Kubernetes manifest。这些都属于第二步。

### 负责

- 验证 kube context 和 Kubernetes API 可访问。
- 验证当前身份具备读取环境状态所需的基础权限。
- 检查 MindCluster、Volcano、AscendJob、PodGroup、NPU device plugin 等
  基础组件、controller、scheduler 和 CRD 是否存在并处于可判断状态。
- 检查集群是否报告环境契约要求的 NPU resource 类型和基础节点
  信息。
- 记录集群身份、组件版本、检查时间和环境输入摘要，形成可追溯的环境证据。
- 检查结果为 `warning` 时记录后继续；为 `error` 或 `unavailable` 时立即
  中断并报错。

### 明确不负责

- 读取或验证本次 Motor user config。
- 选择 namespace、模型、镜像、NPU 数量、affinity 或候选节点。
- 生成、修改或 dry-run 本次 Motor 配置和 Kubernetes manifest。
- 验证本次部署所需的精确 RBAC、可调度性、hostPath、模型或镜像拉取条件。
- 创建、修改或删除 Kubernetes 资源。
- 判断 Motor Pod 是否能够拉起。

### 输出和完成标准

交付独立的 `deploy-environment-ready` 结果，包括：

- machine-ref、kube context 和环境契约版本；
- 集群身份与环境输入摘要；
- 逐项检查状态和证据；
- 明确的 ready 判定与失效信息。

完成只代表：

> K8s 与 MindCluster 基础环境可用，可以进入 Motor 部署配置准备。

它不代表任何一份 Motor 配置已经正确，也不代表本次部署一定可以 apply。
第一版结果只允许同一 workflow 的后续步骤引用；新的 configure/deploy
workflow 必须重新执行本步骤，不使用 TTL 或历史 `last_verified_at` 复用。

### 失败停止位置

任一关键环境依赖失败或无法验证时停在本步骤，不进入配置生成，也不修改
Kubernetes 状态。

## 2. Motor 部署配置准备与验证

责任单元（目标 skill）：`motor-deploy-configure`

核心问题：

> 如何基于目标代码和可用环境，生成一份输入对应明确、替换正确、经过 dry-run
> 且可供第三步原样 apply 的 Motor 部署配置？

### 输入

- 成功且仍有效的 `deploy-environment-ready`。
- 成功的 `machine-ready` 和 machine-ref。
- 成功的 parity manifest、固定远端源码目录和内容一致性证据。
- Motor upstream deployer 原生的 `user_config.json` 和 `env.json`。
- 可选的历史 `deploy-config-ready`，用于判断是否可以复用已有配置包。

### 负责

- 原样读取 Motor 原生配置，不创建 workspace 自有 deploy profile 或字段级
  CLI override。
- 从 `user_config.json` 的 `motor_deploy_config.job_id` 取得 namespace，并
  要求该 namespace 已经存在。
- 验证本次操作需要的精确 namespace/RBAC 和配置依赖。
- 复制到 run-scoped staging 目录，不直接修改用户原始配置。
- 调用 Motor upstream deployer dry-run 生成本次新增的 YAML。
- 对最终 YAML 完成共享 hostPath、volumeMount 和 `PYTHONPATH` 注入；业务
  镜像、模型、NPU 和调度配置继续由 Motor 原生配置和 deployer 决定。
- 只处理并保存本次生成的 manifest，展示最终配置和相对上一次的 diff。
- 校验 manifest 结构并执行 Kubernetes server-side dry-run。
- 验证最终配置引用的固定远端目录与当前 parity manifest 完全对应；证明
  “配置将目标代码路径提供给 Pod”，但不声称 Pod 已经实际加载。
- 生成不可变的配置包、`config_fingerprint` 和独立
  `deploy-config-ready` 结果。

本步骤不检查候选节点 hostPath 实际可见、镜像实际可拉取或模型在容器内可读，
也不创建临时诊断 Pod/Job。这些不是 `deploy-config-ready` 的门禁。

### 配置复用

如果本次规范化配置输入与历史配置包相同，可以省略重新生成 YAML 和重复
dry-run，但不能仅凭“上次部署成功”猜测相同。

复用必须：

- 对比由 Motor 原生配置、machine 固定路径、upstream deployer 版本和
  workspace 注入器版本计算的 `config_fingerprint`；
- 确认 machine 的固定路径映射和配置生成器版本仍兼容；
- 通过独立 `bundle_digest` 确认包括最终 manifest 在内的不可变配置包仍完整；
- 将当前成功 parity manifest 重新绑定到本次 `deploy-config-ready`；
- 记录 `reused_config_bundle_id` 和本次复用证据。

代码内容不属于 config fingerprint。代码变化但固定路径和 Motor 原生配置未
变化时，可以复用配置包；第二步只重新绑定当前 parity 的固定路径引用，第三步
再验证 Pod 实际加载路径。

### 明确不负责

- 重新承担第一步的 K8s/MindCluster 基础环境检查。
- 创建、修改或删除 Kubernetes 资源。
- apply、restart、scale、stop 或 cleanup。
- 等待 Pod Ready。
- 在 apply 前验证镜像实际拉取、模型在容器内实际可读、候选节点 hostPath
  可见，或声明 Pod 已加载目标代码。
- 重新同步本地源码或制造 parity 结果。
- 修改 Motor upstream P/D 控制器语义。

### 输出和完成标准

交付：

- 独立的 `deploy-config-ready`；
- config run/config bundle ID 和 `config_fingerprint`；
- 输入引用及其摘要；
- staging config、最终 manifest、diff 和 dry-run 证据；
- Motor 原生配置副本、namespace/job-id 和最终资源摘要；
- 当前 parity manifest 与最终 hostPath/`PYTHONPATH` 的对应证据；
- 是否复用历史配置包及复用依据。

完成必须同时满足：

```text
当前工作流的环境证据成功
+ Motor 原生配置可被 upstream deployer 接受
+ 最终 manifest 已生成或经 fingerprint 确认可复用
+ 替换、挂载、PYTHONPATH 和 parity 路径对应正确
+ 必需的 manifest 校验与 dry-run 通过
```

本步骤的输出必须是第三步可以原样 apply 的不可变配置包。第三步不得重新
render 后再 apply。

### 失败停止位置

- Motor 原生配置缺失或无效：停在输入解析。
- upstream deployer 或替换失败：停在 staging/render。
- namespace 不存在、manifest、dry-run、精确 RBAC 或结构路径检查失败：
  停在配置验证。
- 历史配置无法证明相同：不得错误复用，转为重新生成；重新生成仍失败则停止。

任何失败都不得进入实际 apply。

## 3. Motor 实际部署与运行验收

责任单元：`motor-k8s-deploy`

核心问题：

> 如何原样应用已经通过验证的配置包，拉起 Motor，并证明资源 Ready、服务
> 最小可访问且 Pod 实际使用第一部分同步的目标代码？

### 输入

- 成功且与当前输入匹配的 `deploy-config-ready`。
- 其引用的不可变配置包和当前 parity manifest。
- 用户对 apply、restart、stop 等修改操作的明确 consent。

### 负责

- 校验配置包完整性和 fingerprint，不重新生成配置。
- 在用户同意后原样 apply 配置包。
- 记录 apply 的每个资源、返回结果和失败位置。
- 等待并判断 Motor 关键资源、组件和 Pod 达到约定 Ready 状态。
- 验证服务最小可访问性。
- 在 Pod 内验证 `motor`、`vllm`、`vllm_ascend` 的实际加载路径，并与当前
  parity manifest 和固定远端目录对应。
- 拉取、调度、挂载或模型问题导致无法 Ready 时停止并保留现场，交给
  diagnosis；正常部署流程不额外创建诊断 workload。
- 支持与本次 deploy run 关联的 status、restart、stop 和诊断入口。
- 保存完整部署状态转换和运行证据。

### 明确不负责

- 重新执行第一步的环境基线检查。
- 重新 render、替换、dry-run 或冒充第二步的配置结果。
- 重新同步或制造 parity 结果。
- 用正式 workload、benchmark 或 profiling 判断部署是否完成。
- 修改 Motor upstream P/D 控制器语义。

### 输出和完成标准

交付：

- deploy run 和明确的状态转换；
- 实际 apply 的 config bundle/fingerprint；
- namespace、job-id 和资源引用；
- apply、Pod/组件 Ready 和最小服务可访问证据；
- Pod 内目标代码加载路径证据；
- status/restart/stop/diagnosis 所需的运行引用。

完成必须同时满足：

```text
配置包完整且与 deploy-config-ready 一致
+ 资源已实际 apply
+ Motor 关键 Pod/组件达到约定 Ready 状态
+ 服务具备最小可访问性
+ Pod 内实际加载当前 parity 对应的目标源码目录
```

只有环境通过、只有配置包、只 apply 成功或只有 Pod Ready，都不能声明本步骤
完成。

### 失败停止位置

- 配置包缺失、被修改或 fingerprint 不匹配：不 apply，返回第二步。
- 用户未 consent：停在 apply 前。
- apply 失败：保留 apply 和 K8s 证据，不进入部署完成状态。
- Pod、服务、模型、挂载或代码路径验证失败：部署存在但未完成，交付诊断入口。

## 三个步骤的交接

```text
motor-deploy-preflight
  → deploy-environment-ready
                         |
                         v
motor-deploy-configure
  → deploy-config-ready + immutable config bundle
                         |
                         v
motor-k8s-deploy
  → deploy run + Ready + runtime source evidence
```

每个下游步骤只消费上游的显式结果引用，不读取模糊的 `last_*` 指针来假定
ready。

## 与第一部分和第三部分的边界

第一部分证明远端固定目录中的内容与本地目标代码一致。第二部分的配置步骤证明
最终 manifest 正确引用这些路径，实际部署步骤证明 Pod 最终看到并加载这些
路径。

第二部分可以做最小服务连通性探测，用来判断部署是否完成。正式功能 smoke、
正确性测试、benchmark 和 profiling 属于第三部分。第三部分只消费成功的
deploy run，不反过来承担配置生成、挂载、Pod 拉起或目标代码加载修复责任。
