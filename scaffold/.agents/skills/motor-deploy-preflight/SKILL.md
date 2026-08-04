---
name: motor-deploy-preflight
description: Validate K8s API and MindCluster base environment before deploy configure. Use for environment preflight, K8s/MindCluster checks, deploy-environment-ready.
---

# motor-deploy-preflight

3+3 **第二部分第二步**：只验证 K8s / MindCluster **基础环境**，产出
`deploy-environment-ready` 证据。

## 边界

**消费**：

- 同一 workflow 内成功的 `machine-ready` run（`--machine-run-id` 可选）
- machine inventory 中的 `kube_context`
- workspace 版本化的 environment contract（默认
  `references/environment-contract.yaml`）
- 可选：`--config-dir` 指向的 Motor 原生 `user_config.json`——preflight 只读
  `motor_deploy_config` 的三个字段，用它做配置校验：
  - `deploy_mode`：选择 workload 专用检查集（`infer_service_set` 要求 Motor
    workload API 与 operator；其余模式只查基础组件）
  - `image_name`：镜像引用合法性 + 每个可调度节点该镜像的覆盖度探测
    （缺失节点记 warning + 证据；逐节点可拉取性验证仍属 configure/deploy）
  - `node_port_overrides`：目标 NodePort 的范围校验（默认 30000-32767）、本批
    唯一性、`kubectl get services -A` 集群级占用探测。冲突时**自动避让**：分配
    空闲端口并把更新后的映射写回 `user_config.json`（configure 直接消费新端
    口）；范围内无空闲端口才 fail closed。未声明时记 warning（模板默认端口归
    configure 处理）
  配置在前是 3+3 真实顺序，preflight 需要这三个字段才能按配置自适应。

**不消费**：parity-complete、`user_config.json` 的其余字段（namespace、模型、
P/D 实例数等）、render 后的 manifest。

**检查项**：

- 机器侧（SSH 远端）的 `kubectl` 可用；不读取或回退到开发机 kubeconfig
- kube context 来自 machine inventory 且可用于 API 访问
- Kubernetes API 可达并具备读取基础集群环境所需权限
- environment contract 要求的 CRD/API resource（基础项 + 按 deploy_mode
  追加项）、controller pattern（含按 deploy_mode 的 one-of 组件组）、NPU
  resource type
- 配置驱动的镜像引用/节点覆盖、NodePort 范围/唯一性/集群占用
- 结果记录实际命中的 deploy_mode、one-of 组命中项、镜像覆盖证据与端口校验
  证据

可选版本信息读取失败记 `warning` 并继续；API 不可达、权限不足、必需组件缺失、
镜像引用非法、NodePort 越界/重复/无空闲端口为 `error`/`unavailable` 并立即
中断。NodePort 集群占用默认自动避让并写回配置，不中断。`--config-dir` 提供
了但 `user_config.json` 缺失或 `deploy_mode` 非法时 fail closed；未提供
`--config-dir` 时只跑基础检查集并在结果中标注。

**不做**：namespace RBAC、业务 Pod readiness、apply、创建 namespace、诊断 Pod、
配置 dry-run manifest、跨 workflow 复用历史 environment-ready。

> **副作用**：NodePort 冲突自动避让时，preflight 会把更新后的
> `node_port_overrides` 写回 `--config-dir/user_config.json`（用户配置的
> 唯一修改点）。其余检查保持只读。

## Entry point

```bash
python3 scaffold/.agents/skills/motor-deploy-preflight/scripts/environment_preflight.py \
  --alias dev1 \
  --machine-run-id <machine-run-id> \
  --workflow-run-id <workflow-run-id> \
  --config-dir <motor-native-config-dir>
```

`--config-dir` 可选：提供时读取 `user_config.json` 的 `motor_deploy_config` 节，
按其 `deploy_mode` 选择 workload 专用检查集，并做镜像引用/节点覆盖与 NodePort
范围/占用校验（完整自适应）；缺省时只跑基础环境检查并在结果中记录
`deploy_mode: null` 与 warning。

Progress 在 stderr，JSON 结果在 stdout（`mws.result.v1` envelope）。
