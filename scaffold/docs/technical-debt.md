# 当前技术债

本文只记录截至 2026-07-31 仍未解决、能够落实到代码或验收动作的技术债。
已完成事项、历史实施过程、冻结设计全文和旧 Agent 工作包不在本文保留。

## 基线与范围

当前本地基线：

- `scaffold/tests`：122 passed；
- `scaffold/.remote-dev/tests`：72 passed / 1 failed，唯一失败为
  `motor-k8s-deploy` Claude skill shim 过期；
- 第二部分的 preflight、configure、apply、Ready 和 runtime source proof
  已有实现与 fixture，但尚未完成真实集群验收。

以下约束已经确认，不再作为待讨论项：

- `workspace-ready` 是审计和编排证据，不是 `machine-ready`、parity 或部署的
  硬门禁；
- workspace 不提供 namespace、queue、scheduler、image、model、NPU 等第二套
  部署字段，部署配置只来自 Motor 原生 `user_config.json` 和 `env.json`；
- `scaffold/profiles/a2-dev.yaml` 中遗留的 namespace、queue、scheduler 字段
  直接删除，不保留兼容层；
- 真实集群验收在 P0 收口并经人工 review 后进行，由仓库所有者提供环境；
- 远端固定源码目录覆盖以及 Kubernetes apply、restart、stop 必须逐次取得明确
  授权。

## P0：打通 machine-ready 证据生产和消费

### TD-P0-01：`machine_verify` 不生产 `machine-ready` run

现状：

- `machine_verify.py` 成功后只更新 inventory 的 `last_verified_at` 和
  `last_verify_errors`；
- stdout 仍是 legacy `{status: ok|error, ready, checks}`；
- 没有生产路径调用 `write_run("machine-ready", ...)`；
- parity、environment preflight、deploy configure 和 deploy apply 要求
  `machine-ready` 证据，因此真实 `verify → parity` 链路中断。

目标：

- 每次 verify 创建新的 `machine_run_id` 和 `workflow_run_id`；
- 使用 `mws.result.v1` envelope；
- 成功时原子写入不可变
  `.motor-workspace-local/machine-runs/{machine_run_id}/run.json`，状态为
  `ready`，stdout 返回同一个 `machine_run_id`；
- 失败时可以保留 `failed` run 和已完成检查的证据，但不得发布或被消费为
  ready；
- inventory 中的 `last_verified_at` 继续只作为诊断元数据，不得作为下游证据。

### TD-P0-02：machine check 状态仍使用 legacy 枚举

现状：

- `mws_machine_target.py` 使用 `pass`、`fail`、`not_applicable`；
- 公共结果契约只接受 `ok`、`warning`、`error`、`unavailable`；
- machine-management 脚本仍通过 legacy `emit()` 输出。

目标：

- 将已执行检查统一为 `ok|warning|error|unavailable`；
- `warning` 保存证据后允许继续；
- `error|unavailable` 立即停止后续检查并返回非零；
- 不适用的检查不执行，不输出 `not_applicable`；
- machine-management 的 verify/repair 出口迁移到 `mws.result.v1`；
- 删除为 machine 主链保留双状态语义的代码和测试。

### TD-P0-03：machine-ready consumer 仍识别 legacy 格式

现状：

- `load_machine_ready_evidence()` 接受 `ready=true` 或 `status=ok`；
- 它没有按 `mws.result.v1` 的 `kind=machine-ready`、`status=ready` 和 schema
  完整校验；
- 当前 parity fixture 通过手工构造 legacy machine run，不能证明 producer
  和 consumer 兼容。

目标：

- consumer 使用公共 run loader 或等价的严格校验；
- 要求 schema、kind、run ID、ready 状态、machine identity、固定 endpoint 和
  必需 checks 一致；
- 下游内部调用使用显式 `machine_run_id`，不得静默选择模糊的最新成功记录；
- 删除或隔离 legacy 兼容路径，避免新旧格式继续双轨运行。

P0 验收必须包含：

- `machine_verify` 成功后产生可由 consumer 读取的不可变 ready run；
- verify 失败时不会产生可消费的 ready run；
- 不手工构造上游 JSON 的
  `machine_verify → load_machine_ready_evidence → parity` 纵向 fixture；
- machine identity、run kind、状态、endpoint 或 checks 被篡改时 fail closed；
- `scaffold/tests` 全量通过。

## P1：完成本地工作流收口

### TD-P1-01：repo-init 不生产 `workspace-ready`

现状：

- repo-init 已能报告工具、认证、submodule、remote topology、HEAD 和 dirty 状态；
- probe/apply 仍只输出 legacy JSON，不写 workspace run。

目标：

- repo-init 成功时写入不可变 `workspace-ready` run 并返回
  `workspace_run_id`；
- 结果使用 `mws.result.v1`，包含 workspace root、三个源码仓状态、工具和认证
  观察结果；
- dirty/untracked workspace 是合法状态，不作为失败门禁；
- `workspace-ready` 不得成为 machine verify、parity 或部署的强制上游；
- 增加 submodule 初始化的幂等 fixture，测试策略不得依赖 Git 默认禁止的
  `file://` clone 行为。

### TD-P1-02：旧 deploy profile 字段与原生配置边界冲突

现状：

- `scaffold/profiles/a2-dev.yaml` 仍包含 namespace、`volcano_queue` 和
  `scheduler_name`；
- 这些字段与“Motor 原生配置是唯一部署配置源”的约束冲突。

目标：

- 直接删除上述字段；
- 删除或修改依赖这些字段的测试和 legacy 读取路径；
- profile 只保留 machine/environment 观察所需字段，例如 hardware、
  mount root、kube context、MindCluster 组件和资源类型；
- namespace、queue 和 scheduler 只从本次 Motor 原生配置及其生成 manifest
  获取。

### TD-P1-03：部署后 diagnosis 仍依赖旧 plan 布局

现状：

- `diagnosis_collect.py` 从 deploy run 读取 `plan_dir`；
- 新部署主契约使用 config run、不可变 `bundle_dir` 和 deploy run；
- 新 `deploy-complete` 或失败 run 无法可靠进入 diagnosis。

目标：

- diagnosis 消费明确的 deploy run 和其 config/bundle 引用；
- 从 run/bundle 获取 machine、kube context、namespace 和资源引用；
- 收集 pods、events、关键 workload 日志及部署失败证据；
- 证据写入独立 run-scoped diagnosis/validation 目录；
- 缺 run、run 未完成、machine 不匹配、bundle 被篡改时 fail closed；
- 增加不依赖旧 `plan_dir` 的 fixture。

### TD-P1-04：Agent 路由文档和生成 shim 与实现漂移

现状：

- 根 `AGENTS.md` 仍将 preflight/configure 标为未实现，并将 k8s deploy 描述为
  legacy Plan wrapper；
- `motor-k8s-deploy` Claude skill shim 过期；
- `implementation-plan.md` 和 `agent-work-orders.md` 仍以大量已经完成的工作单
  作为当前执行入口，可能误导 Agent 重复实现；
- `.remote-dev` 文档仍有 `/vllm-workspace` 等 VAWS 默认值残留。

目标：

- 根 `AGENTS.md`、README、skill、CLI help 和目录责任文档描述当前 3+3
  实现；
- 重新生成并检查 Claude skill shim；
- 将旧实施计划标记为历史材料，或改写成只包含尚未完成的工作；
- 清理 `.remote-dev` 文档中的 VAWS 路径和 session 语义残留；
- `scaffold/.remote-dev/tests` 全量通过。

### TD-P1-05：缺少真实集群纵向验收

前置条件：

- P0 全部通过；
- P0 diff 经人工 review；
- 仓库所有者提供真实 K8s/MindCluster/Ascend 环境。

验收顺序：

```text
machine-management verify
→ remote-code-parity
→ motor-deploy-preflight
→ motor-deploy-configure
→ motor-k8s-deploy
```

验收要求：

- 先完成只读 probe/preflight；
- 覆盖固定远端源码目录前单独请求授权；
- apply、restart、stop 分别单独请求授权；
- 保存 environment/config/deploy run 和 bundle 证据；
- 只有关键资源 Ready、最小服务可访问、Pod 内 `motor`、`vllm`、
  `vllm_ascend` 加载路径与当前 parity 固定路径一致，才能声明
  `deploy-complete`；
- fixture 通过不得替代真实环境结论。

## P2：不阻塞第一版的维护性和后续能力

### TD-P2-01：两套 SSH/传输实现

现状：

- `scaffold/.remote-dev/core` 与 `scaffold/.agents/lib/mws_transport.py` 分别维护
  endpoint、SSH、错误处理和部分传输语义。

目标：

- 以 `.remote-dev` 作为通用远端操作实现；
- `mws_transport.py` 如保留，只作为 machine/parity 领域适配层，不重复实现
  SSH endpoint 和错误语义；
- 迁移前补等价契约测试，禁止创建第三套 transport；
- 该项在真实链路稳定后实施，不与 P0 并行重构。

### TD-P2-02：`mws_deploy.py` 责任过多

现状：

- 文件约 1224 行，混合 profile、render、bundle、kubectl dry-run、apply、
  Ready、smoke 和 runtime source proof。

目标：

- 按 config/bundle、Kubernetes apply/readiness、runtime proof 拆分模块；
- 保持现有公开入口和 fixture 行为；
- 先增加边界测试，再做机械拆分；
- 不与真实集群验收或 P0/P1 功能修改同时进行。

### TD-P2-03：motor-benchmark 仍是占位实现

现状：

- `bench_plan.py` 只返回 warning，没有执行请求、指标或 benchmark run。

目标：

- 只接受成功且 machine 匹配的 deploy run；
- 执行明确版本的 benchmark workload；
- 保存参数、环境、原始结果、聚合指标和失败证据；
- 缺 deploy run、非 ready、machine 不匹配或 endpoint 不可用时 fail closed；
- benchmark 不属于第一版真实部署验收门禁。

### TD-P2-04：残留 legacy 状态和 session 命名

目标：

- 全仓移除不再参与主链的 `SESSIONS_DIR`、旧 phase/plan 命名和模糊
  `last_*` 下游依赖；
- 内部 `workspace_id` 如保留，只用于本地诊断，不进入远端路径或下游必需
  输入；
- 清理完成后运行全仓检索和契约测试，证明没有重新引入 VAWS
  session/container 生命周期。

## 完成定义

单项技术债只有同时满足以下条件才可关闭：

- 生产路径已修改，不是只修改 fixture；
- 失败路径 fail closed，且不会发布错误的 ready/complete；
- 有覆盖 producer 与 consumer 的测试；
- 相关 skill、CLI help、Agent shim 和当前状态文档同步；
- `git diff --check`、`scaffold/tests` 和 `.remote-dev/tests` 通过；
- 涉及真实环境的项目明确区分“本地 fixture 完成”和“真实环境验收完成”。
