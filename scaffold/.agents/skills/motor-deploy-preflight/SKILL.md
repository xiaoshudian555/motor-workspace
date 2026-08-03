---
name: motor-deploy-preflight
description: Validate K8s API and MindCluster base environment before deploy configure. Use for environment preflight, K8s/MindCluster checks, deploy-environment-ready.
---

# motor-deploy-preflight

3+3 **第二部分第一步**：只验证 K8s / MindCluster **基础环境**，产出
`deploy-environment-ready` 证据。

## 边界

**消费**：

- 同一 workflow 内成功的 `machine-ready` run（`--machine-run-id` 可选）
- machine inventory 中的 `kube_context`
- workspace 版本化的 environment contract（默认
  `references/environment-contract.yaml`）

**不消费**：parity-complete、Motor `user_config.json` / `env.json`、namespace、
镜像/模型、render 后的 manifest。

**检查项**：

- 机器侧（SSH 远端）的 `kubectl` 可用；不读取或回退到开发机 kubeconfig
- kube context 来自 machine inventory 且可用于 API 访问
- Kubernetes API 可达并具备读取基础集群环境所需权限
- environment contract 要求的 CRD/API resource、controller pattern、NPU
  resource type

可选版本信息读取失败记 `warning` 并继续；API 不可达、权限不足、必需组件缺失
为 `error`/`unavailable` 并立即中断。

**不做**：namespace RBAC、业务 Pod readiness、apply、创建 namespace、诊断 Pod、
配置 dry-run manifest、跨 workflow 复用历史 environment-ready。

## Entry point

```bash
python3 scaffold/.agents/skills/motor-deploy-preflight/scripts/environment_preflight.py \
  --alias dev1 \
  --machine-run-id <machine-run-id> \
  --workflow-run-id <workflow-run-id>
```

Progress 在 stderr，JSON 结果在 stdout（`mws.result.v1` envelope）。
