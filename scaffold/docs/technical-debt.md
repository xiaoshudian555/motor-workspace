# 已确认技术债

本文记录已经由责任边界讨论确认、但当前实现尚未完成的工作。它不是所有未来
功能的愿望清单，也不按旧 P0/P1/P2 或 VAWS skill 名称机械展开。

## machine-management 与 Deploy preflight 拆分

已确定的目标边界：

```text
machine-management
  → machine-ready
  → 只证明远程开发和 parity 可以进行

motor-k8s-deploy preflight
  → deploy-preflight-ready
  → 证明本次 Motor 部署具备尝试 apply 的条件
```

### 执行顺序链与证据文件路径约定

下游只通过**显式 run id 引用**消费上游结果，不用 inventory 里的
`last_verified_at` 或模糊的 `last_*` 字段代替本次证据。

```text
workspace-ready
  → machine-ready
  → parity-complete
  → deploy-preflight-ready
  → plan
  → apply
```

| 阶段 | 责任 skill | 证据文件（相对 repo 根） | 主要引用字段 |
|---|---|---|---|
| `machine-ready` | `machine-management` | `.motor-workspace-local/validation-runs/{validation_run_id}/machine-ready.json` | `machine_ref`、`status`、`checks[]` |
| `parity-complete` | `remote-code-parity` | `.motor-workspace-local/parity-runs/{parity_run_id}/manifest.json` | `machine_ref`、固定远端路径、内容摘要 |
| `deploy-preflight-ready` | `motor-k8s-deploy` | `.motor-workspace-local/validation-runs/{validation_run_id}/deploy-preflight-ready.json` | `machine_ref`、`parity_run_id`、`profile`、`config_dir`、`base_image_ref`、`status`、`checks[]` |
| `plan` | `motor-k8s-deploy` | `.motor-workspace-local/deploy-runs/{deploy_run_id}/plan/plan-body.json` | `preflight_run_id`、`parity_run_id` |

约定：

- `validation_run_id` 由 `mws_run_state.new_run_id()` 生成；machine 与 deploy
  preflight 各自独立目录，**不得共用文件名或混写进** `machine-inventory.json`。
- `parity` 只要求 `machine-ready`；**不得**要求 `deploy-preflight-ready`。
- `deploy-preflight-ready` 必须引用**本次**成功的 `parity_run_id`；缺 manifest
  或 run 过期时 fail closed，不得隐式再跑 parity。
- `plan` 必须引用**本次** `preflight_run_id`；无 preflight 证据时不得 render
  或 apply。
- inventory 最多保存**最近一次** run id 作为 UI 便利指针；任何 skill 不得
  仅凭指针跳过本次检查。
- `.motor-workspace-local/deploy-runs/{deploy_run_id}/run.json` 必须记录
  `machine_ready_run_id`、`parity_run_id`、`preflight_run_id`，便于追溯。

### 与 `render_plan` 的 refactor 关系

当前 `deploy_plan.py` 在 plan 阶段内联调用 `render_plan()`；后者已包含
upstream deployer dry-run 和 Kubernetes server-side dry-run（见
`scaffold/.agents/lib/mws_deploy.py`）。这与「独立 preflight」目标重叠。

refactor 原则：**迁移，不复制**。

| 现状位置 | 目标归属 | 说明 |
|---|---|---|
| `mws_deploy.run_deploy_dry_run()` | deploy preflight | 保留函数；由 preflight 调用，plan 不再独立触发 |
| `mws_deploy.kubectl_dry_run_and_diff()` | deploy preflight | 同上 |
| `mws_deploy.render_plan()` 中的 manifest 生成与 staging | plan | 保留；只负责产出 `plan-body.json` 和 manifests |
| `deploy_plan.py` 内联 parity | 不变 | parity 仍在 plan 前；preflight 消费其 manifest |

目标调用链：

```text
deploy_plan.py
  → parity_sync（若无有效 parity_run_id）
  → deploy_preflight.py（新建；产出 deploy-preflight-ready.json）
  → render_plan（只 render；读取 preflight 证据，不重复 dry-run）
  → write deploy run
```

禁止：

- preflight 与 plan 各跑一遍 dry-run / server-side dry-run；
- 在 `machine_verify.py` 保留任何已从 preflight 迁出的 Kubernetes 检查；
- 把 preflight 结果写进 `plan-body.json` 而不保留独立
  `deploy-preflight-ready.json`；
- preflight 未通过或缺失时，plan 降级为 `warning` 后继续 render 或 apply。

`render_plan()` 里现有的 `run_deploy_dry_run()` 与
`kubectl_dry_run_and_diff()` 应抽到 preflight 公共模块（如
`run_deploy_preflight()`）；plan 只读取 preflight 证据并在
`plan-body.json` 中引用 `preflight_run_id`。

### `skipped` / fail closed 策略

关键依赖无法验证时 **fail closed**：整体 `status` 不得为 `ok` / ready，
也不得把 `skipped` 包装成「可以 apply」。

| 检查项 | 所属层 | 无法验证时 | 检查失败时 | 备注 |
|---|---|---|---|---|
| SSH / 远端命令 | `machine-ready` | `failed` | `failed` | |
| `mount_root` 读写与 cleanup | `machine-ready` | `failed` | `failed` | |
| `remote_workspace_root` 路径边界 | `machine-ready` | `failed` | `failed` | 必须在 `mount_root` 内 |
| parity 基础工具（rsync/scp 等） | `machine-ready` | `failed` | `failed` | 工具清单在实现时枚举 |
| kube context / Kubernetes API | `deploy-preflight` | `failed` | `failed` | 不属于 `machine-ready` |
| namespace RBAC | `deploy-preflight` | `failed` | `failed` | |
| MindCluster / Volcano / CRD | `deploy-preflight` | `failed` | `failed` | |
| NPU resource / affinity / 可调度 | `deploy-preflight` | `failed` | `failed` | 不用登录机 NPU 数代替 |
| 候选节点 hostPath / parity 内容可见 | `deploy-preflight` | `failed` | `failed` | |
| 模型路径可读 | `deploy-preflight` | `failed`（若配置要求） | `failed` | 未配置则可 `skipped` 且不计 ready |
| 镜像 / registry / imagePullSecret | `deploy-preflight` | `failed` | `failed` | |
| upstream deployer dry-run | `deploy-preflight` | `failed` | `failed` | 从 `render_plan` 迁入 |
| server-side dry-run | `deploy-preflight` | `failed` | `failed` | 从 `render_plan` 迁入 |
| 本地无 kubectl | `deploy-preflight` | `failed` | `failed` | 环境不具备，不是 `skipped` |
| OpenAI smoke / 服务探活 | deploy 后 status | `skipped` 允许 | `failed` | 不属于 preflight |
| 历史 Pod readiness | — | — | — | 从 machine verify 移除；不属于 preflight |

整体 ready 判定：

- `machine-ready`：任一上表 machine 行 `failed` → 整体 `failed`。
- `deploy-preflight-ready`：任一上表 preflight 行 `failed` → 整体 `failed`；
  仅「模型路径未配置」等明确列出的项可 `skipped`，且 `skipped` 不得计为 ready。

当前实现与上表不符，拆分时应一并修正：`machine_verify.py` 在缺 kubectl 时记
`skipped`；`deploy_plan`/`render_plan` 在 k8s 检查失败时 overall 可能为
`warning`。

### machine-management 尚需完成

- [ ] 将 `machine-ready` 结果契约固定为：SSH/远端命令可用、固定目录映射
  合法、目录可创建/读写/清理、parity 所需基础工具可用。
- [ ] 补全 inventory 的 add/list/update/remove/repair 工作流；破坏性或
  bootstrap 操作继续要求用户 consent。
- [ ] 验证 `remote_workspace_root` 必须位于允许的 `mount_root` 内，避免只
  检查绝对路径而允许越出共享根。
- [ ] 明确凭据只保存引用，不将 SSH key、token 或 kubeconfig 内容写入
  tracked 文件。
- [ ] kube context、hardware profile 和基础 NPU 观察结果可以登记，但不得
  被解释为 `machine-ready` 的集群部署证明。
- [ ] 从 machine verify 中移出 Kubernetes、namespace RBAC、Pod readiness、
  MindCluster/Volcano/CRD 和部署级 NPU 调度检查。
- [ ] 为 machine-ready 增加 fixture 测试，覆盖连接失败、目录越界、目录
  不可写、基础工具缺失和 cleanup 失败。

当前
`scaffold/.agents/skills/machine-management/scripts/machine_verify.py`
仍把 kube context、namespace RBAC、CRD 和 Pod readiness 混在 machine
verify 中，需要按上述边界拆分。

### motor-k8s-deploy preflight 尚需完成

- [ ] 在 plan/apply 前增加独立的只读 preflight，并让 plan 明确引用其结果。
- [ ] 消费 machine-ref、成功 parity manifest、deploy profile、Motor
  `user_config.json`、模型路径和 `base_image_ref`。
- [ ] 检查 kube context、Kubernetes API、目标 namespace 和本次资源操作
  所需 RBAC。
- [ ] 检查 MindCluster、Volcano、AscendJob、PodGroup 等实际需要的 CRD、
  controller 和 scheduler 条件。
- [ ] 检查本次配置所需的 NPU resource、节点标签、affinity 和可调度条件；
  不用登录机上的 NPU 数量代替集群调度判断。
- [ ] 从 render 后的实际候选节点出发，验证这些节点在相同路径看到 parity
  manifest 对应的共享内容；检查不是多节点重复同步。
- [ ] 检查模型路径在目标运行环境可读。
- [ ] 检查运行镜像引用、registry 访问和 imagePullSecret 等拉取条件。
- [ ] 复用 upstream Motor deployer dry-run，并增加 manifest 校验及
  Kubernetes server-side dry-run。
- [ ] 定义 `ok`、`failed`、`unknown/skipped` 的清晰结果；关键依赖无法验证
  时 fail closed，不把 skipped 报成 ready。
- [ ] preflight 保持只读；任何 namespace 创建、apply、restart、scale 或
  delete 都留在取得 consent 之后。
- [ ] 为 preflight 增加 fixture/单元测试；真实 K8s + NPU 环境验收单独记录，
  无集群时不得宣称已经验收完成。

### 交接和文档债

- [ ] parity 只依赖 `machine-ready`，不依赖 Kubernetes/MindCluster
  preflight。
- [ ] Deploy 不得仅凭历史 machine verify 结果跳过本次 preflight。
- [ ] `machine-ready` 与 `deploy-preflight-ready` 使用不同的结果名称和
  证据文件，避免再次把两层状态混成一个 `ready`。
- [ ] Agent 路由应把“检查机器能否同步代码”交给 machine-management，把
  “检查这次 Motor 能否部署”交给 motor-k8s-deploy。
- [ ] 更新根目录 `AGENTS.md`、machine-management 与 motor-k8s-deploy 的
  `SKILL.md`、脚本帮助、测试和生成的 Agent shim，使其与本文一致（含
  machine-management 不再承担 kube/RBAC/CRD 部署证明的表述）。

## 当前验收口径

完成 machine-management 代码与 fixture 测试，只能声明：

> 远程开发目标和固定同步目录已经准备好。

完成 Deploy preflight 代码与 fixture 测试，但没有真实集群，只能声明：

> preflight 实现和本地测试完成，尚未完成环境验收。

只有在真实 K8s + MindCluster + Ascend NPU 环境执行成功后，才能声明：

> 本次 deploy-preflight-ready 环境验收完成。
