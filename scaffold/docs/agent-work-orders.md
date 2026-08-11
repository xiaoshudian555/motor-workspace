# 3+3 技术债实施工作单（历史材料）

> **状态：历史参考。** 当前待办以 [`technical-debt.md`](technical-debt.md) 为准。
> 已完成工作单仍保留在文中供追溯，**不要**按本文重复实现。

本文是交给实现 Agent 的执行入口。责任边界以 `motor-deploy.md` 为准，问题清单
以 `technical-debt.md` 为准。Agent 不得重新讨论或改写本文中的六项冻结决定。

## 开始前

1. 先读根 `AGENTS.md`、`implementation-plan.md`、`motor-deploy.md` 和
   `technical-debt.md`。
2. 检查 `git status` 和现有 diff。当前工作树可能已有 repo-init、
   machine-management、parity、`.remote-dev` 和 preflight 的并行修改；这些
   是其他 Agent 的工作，不得覆盖或回退。
3. 所有实现必须基于包含本文的同一个 commit。不同 worktree/分支完成后按下述
   依赖顺序集成。
4. VAWS 只按 `technical-debt.md` 的复用矩阵迁移；较大复制保留 MIT
   attribution。
5. 没有真实机器/集群时只能声明 fixture 测试通过，不能声明环境或部署验收
   完成。

## 不得改变的业务决定

1. 检查为 `warning` 时记录并继续；`error` 或 `unavailable` 立即中断、非零
   退出，等待人修复后重新运行。
2. 部署字段只来自 Motor 原生 `user_config.json` 和 `env.json`。Workspace
   不增加 deploy profile 或字段级 CLI override。
3. namespace 取 `motor_deploy_config.job_id`，必须已经存在，任何步骤都不创建。
4. environment-ready 第一版不跨工作流复用。
5. config fingerprint 只描述结构配置；parity 代码内容不进入 fingerprint。
6. apply 前不创建诊断资源，不验证实际 image pull、模型容器内可读或候选节点
   hostPath。拉起失败后再进入 diagnosis。

## 依赖顺序

```text
现有第一部分并行修改完成
  → 工作单 0：集成和边界复核
  → 工作单 1：公共 result/run/bundle 契约
  → ┌─ 工作单 2：environment preflight
    └─ 工作单 3：deploy configure
  → 工作单 4：motor-k8s-deploy 收敛
  → 工作单 5：3+3 集成验收
```

工作单 2 和 3 可以在工作单 1 合入后并行。工作单 4 必须等工作单 3 完成旧
`deploy_plan.py` 的迁移，避免两个 Agent 同时编辑 `motor-k8s-deploy`。

## 工作单 0：集成当前第一部分并行修改

### 目标

集成并验证当前 repo-init、machine-management、remote-code-parity 和
`.remote-dev` 修改，不进入第二部分实现。

### 必须完成

- 按 `implementation-plan.md` 的 A/B/C/D 文件所有权检查越界修改。
- 运行 `.remote-dev` 和 `scaffold/tests` 全量测试。
- 确认 `machine_verify` 已不包含 Kubernetes、MindCluster、namespace 或 Pod
  readiness。
- 确认 parity 只依赖 machine-ready，不会自动伪造覆盖 consent。
- 确认 `.remote-dev` 没有恢复 VAWS session/container selector。
- 汇总需要公共 result/run schema 的字段，不自行创建多套格式。

### 交付

- 干净的集成 commit；
- 测试命令和结果；
- 尚未解决的问题清单；
- 若没有真实远端，明确标注“仅 fixture 验收”。

## 工作单 1：公共 result、run 和 bundle 契约

### 文件范围

```text
scaffold/.agents/lib/mws_result.py
scaffold/.agents/lib/mws_run_state.py
scaffold/.agents/lib/mws_state.py
scaffold/tests/test_result*.py
scaffold/tests/test_run_state*.py
```

根文档只由最终集成 Agent 更新。

### Result envelope

六类完成结果统一包含：

```text
schema_version
kind
run_id
workflow_run_id
status                  # ready | failed
started_at
finished_at
upstream_refs
checks[]
warnings[]
errors[]
artifacts[]
```

每个实际执行的 check：

```text
name
status                  # ok | warning | error | unavailable
message
evidence/artifact refs
```

规则：

- `warning` 继续执行，最终仍可 `status=ready`；
- 第一个 `error/unavailable` 立即中断并写入失败结果；
- 失败结果保留此前成功和 warning 的检查证据及准确停止位置；
- 不输出 `skipped/not_applicable`；
- 成功进程退出 0；`error/unavailable` 非零退出；
- 人重新执行产生新 run，不修改旧 run。

### Run 类型

```text
workspace-ready
machine-ready
parity-complete
deploy-environment-ready
deploy-config-ready
deploy-complete
```

所有下游只接受显式上游 run ID，不读取模糊 `last_*` 指针。

### Config bundle

使用两个不同摘要，禁止混为一个：

- `config_fingerprint`：规范化的 Motor 原生配置、machine 固定路径映射、
  upstream deployer commit/version、workspace manifest injector version。
- `bundle_digest`：不可变 bundle 中全部原生配置副本、最终 manifest 和验证
  证据的内容摘要。

建议目录：

```text
.motor-workspace-local/config-runs/{config_run_id}/
.motor-workspace-local/config-bundles/{config_fingerprint}/
```

同 fingerprint 的目录若已存在，必须先验证 `bundle_digest`；内容不匹配立即
报错，不得覆盖。第一版不做自动 GC。

### 验收

- warning 后继续并可 ready；
- error/unavailable 立即中断；
- 失败 run 保留部分证据；
- 旧 run 不可被新运行改写；
- bundle 被篡改、上游引用不存在或类型错误时 fail closed；
- 并发创建同 fingerprint 不产生半写或互相覆盖。

## 工作单 2：修正并完成 motor-deploy-preflight

### 文件范围

```text
scaffold/.agents/skills/motor-deploy-preflight/**
scaffold/.agents/lib/mws_environment.py
scaffold/tests/test_environment_preflight.py
.claude/skills/motor-deploy-preflight/**
```

### 当前已知越界，必须先修

当前并行实现已经出现以下错误方向：

- `SKILL.md` 和脚本消费包含 namespace 的 deploy profile；
- 检查 namespace RBAC；
- 检查 namespace Pod readiness；
- 提供 `--skip-pod-readiness`；
- `mws_environment.py` 依赖 deploy 层的 `mws_deploy` 和
  `pod_readiness_probe`；
- 使用 `pass/fail/not_applicable`，未遵守冻结状态和立即中断规则。

这些都必须删除或迁出 preflight，不能把测试改成认可越界行为。

### 正确输入

- 同一 workspace 的成功 `machine-ready` run；
- machine inventory 中的 kube context 引用；
- workspace 版本化的 environment contract，只描述必需
  K8s/MindCluster/Volcano/Ascend CRD、controller、scheduler、device plugin
  和 NPU resource 类型，不包含 namespace 或任何 Motor deploy 字段。

### 正确检查

- `kubectl` 可用；
- kube context 与 machine-ready 引用一致；
- Kubernetes API 可达并能识别 cluster identity；
- 读取基础集群环境所需的权限；
- 必需 CRD/API resource、controller、scheduler、device plugin 和 NPU
  resource type 存在且可用。

可选版本信息拿不到可以 warning。API 不可达、上下文不一致、必需组件缺失或
必需状态不可读是 error/unavailable，立即中断。

### 明确禁止

- 不读取 parity、Motor config、namespace、模型、镜像或最终 manifest；
- 不检查 namespace RBAC、候选节点、现有业务 Pod readiness；
- 不执行 dry-run，不创建任何 Kubernetes 资源；
- 不跨 workflow 复用历史 environment-ready。

### 验收

- 每个 workflow 生成新 environment run；
- warning 继续，error/unavailable 短路；
- 没有 namespace/profile/pod-readiness 参数或结果字段；
- fixture 覆盖 API 不可达、context 错误、CRD 缺失、controller 不可用、
  warning 和成功路径。

## 工作单 3：实现 motor-deploy-configure

### 文件范围

```text
scaffold/.agents/skills/motor-deploy-configure/**
scaffold/.agents/lib/mws_deploy.py
scaffold/.agents/skills/motor-k8s-deploy/scripts/deploy_plan.py  # 迁出后删除/收口
scaffold/tests/test_deploy_configure*.py
```

不要编辑 `deploy_apply.py/restart.py/status.py/stop.py`；这些属于工作单 4。

### 输入

- 当前 workflow 的成功 environment run；
- 成功 machine-ready；
- 成功 parity run，只使用其 machine 和固定路径映射；
- Motor 原生 config directory，或 upstream 原生支持的
  `user_config.json + env.json` 路径；
- 可选历史 config run，用于尝试复用。

Workspace 命令可以选择上述路径/run，但不得提供 namespace、image、model、
NPU 或调度字段 override。

### 新配置路径

1. 复制原生配置到 run-scoped staging。
2. 使用 upstream Motor `deploy.py --config_dir ... --dry-run` 或等价原生入口。
3. 只收集本次生成的 manifest。
4. 根据当前 machine/parity 固定路径注入共享 hostPath 与 volumeMount。
5. 从 `motor_deploy_config.job_id` 取得 namespace；不存在立即报错。
6. 校验 manifest 结构和本次资源所需 RBAC。
7. 执行 Kubernetes server-side dry-run，不持久化资源。
8. 创建不可变 bundle、bundle digest 和 deploy-config-ready。

### 配置复用路径

1. 计算不含代码摘要的 `config_fingerprint`。
2. 找到相同 fingerprint 的 bundle。
3. 验证 bundle digest 和 upstream/injector 版本。
4. 验证 bundle 中固定路径与当前 machine/parity 路径映射相同。
5. 创建新的 config run，引用旧 bundle，并记录当前 parity 路径引用。

配置变化时重新 render；代码内容变化本身不重新 render。

### 明确禁止

- 不增加 deploy profile 或字段级 CLI override；
- 不自动执行 parity；
- 不创建 namespace；
- 不 apply；
- 不创建诊断 Pod/Job；
- 不验证 image pull、模型容器内可读、候选节点 hostPath 实际可见；
- 不把这些运行期条件登记为 deferred/warning 门禁。

### 验收

- 原生配置缺失/非法、namespace 不存在、upstream dry-run、注入、RBAC、
  server-side dry-run 任一 error/unavailable 都立即停止；
- warning 保存后继续；
- 最终 bundle 可被第三步原样 apply；
- 同结构配置命中复用，代码摘要变化不破坏复用；
- bundle 被修改或固定路径不一致时拒绝复用；
- 未经授权不发生任何 Kubernetes 持久写入。

## 工作单 4：收敛 motor-k8s-deploy

### 文件范围

```text
scaffold/.agents/skills/motor-k8s-deploy/**
scaffold/tests/test_deploy_apply*.py
scaffold/tests/test_deploy_runtime*.py
```

### 目标

- 删除自动 parity、render、替换和 dry-run；
- 只消费成功 deploy-config-ready 和其不可变 bundle；
- apply 前验证 config run、bundle digest、machine 和 parity 固定路径绑定；
- 用户明确 consent 后原样 apply；
- 保存逐资源 apply 结果；
- 等待 Motor 关键资源和 Pod Ready；
- 验证最小服务可访问；
- Ready 后采集 `motor`、`vllm`、`vllm_ascend` 的实际 `__file__` 路径，并与
  固定共享路径对应；
- 修复 `deploy_apply.py` 未定义 `args.plan_dir`；
- status/restart/stop 明确关联 deploy run。

### 失败行为

- bundle 或引用错误：apply 前停止；
- apply、调度、拉取、挂载或模型问题导致无法 Ready：结果 failed，保留事件、
  日志和资源引用，不创建额外诊断 workload；
- 深入排查由 `motor-diagnosis` 显式执行；
- warning 可继续；error/unavailable 立即中断；
- 未达到 Ready、最小可访问和运行代码路径证明，不得发布 deploy-complete。

### 验收

- apply 的字节内容与 bundle 一致；
- 不调用 render/parity/dry-run；
- apply/Ready/最小访问/代码路径各类失败均有 fixture；
- restart 重新等待 Ready 并重新采集代码路径；
- 真实 apply 必须单独取得 consent。

## 工作单 5：3+3 集成验收

### 必须完成

- 更新六个 skill、Agent shim、README、架构、边界和目录责任文档；
- 确认全仓不再把第二部分写成 2A/2B；
- 确认没有 deploy profile/字段级 CLI override 作为 Motor 配置源；
- 确认 preflight 不读取 namespace 或检查 Pod readiness；
- 确认 namespace 始终 require-existing；
- 确认没有 environment-ready 跨 workflow 复用；
- 确认 config fingerprint 不含 parity 内容摘要；
- 确认 apply 前不创建诊断资源或执行运行期可用性探测；
- 运行所有本地测试和 `git diff --check`。

### 纵向 fixture

至少覆盖：

- warning 继续、error/unavailable 短路；
- 六类上游 run 缺失或类型错误；
- environment run 来自另一 workflow；
- namespace 不存在；
- upstream/server-side dry-run 失败；
- config 相同复用、代码变化仍复用、固定路径变化拒绝复用；
- bundle 被篡改；
- apply 失败、Ready 失败、代码路径不匹配；
- restart 快速路径。

### 真实环境

按顺序执行：

```text
repo-init
→ machine-management
→ remote-code-parity
→ motor-deploy-preflight
→ motor-deploy-configure
→ motor-k8s-deploy
```

没有真实 K8s/MindCluster/Ascend 环境时不得声称纵向部署完成。真实 apply、
restart、stop 必须分别取得用户明确 consent。
