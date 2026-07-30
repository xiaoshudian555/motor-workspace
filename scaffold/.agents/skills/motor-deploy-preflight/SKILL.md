---
name: motor-deploy-preflight
description: Validate K8s API and MindCluster base environment before deploy configure. Use for environment preflight, K8s/MindCluster checks, deploy-environment-ready.
---

# motor-deploy-preflight

3+3 **第二部分第一步**：只验证 K8s / MindCluster **基础环境**，产出
`deploy-environment-ready` 证据。

## 边界

**消费**：machine-ready（machine alias）、deploy profile、machine inventory 中的
kube context 元数据。

**不消费**：parity-complete、user config、render 后的 manifest、镜像/模型选择。

**检查项**（从 legacy `machine_verify.py` 平移）：

- kubectl 可用
- kube context 一致性（inventory vs profile）
- namespace RBAC（`auth can-i get pods`）
- MindCluster / Volcano CRD（`required_api_resources`）
- namespace 内 Pod readiness（可选，`--skip-pod-readiness` 关闭）

**不做**：apply、创建 namespace、诊断 Pod、配置调度、dry-run manifest。

## Entry point

```bash
python3 .agents/skills/motor-deploy-preflight/scripts/environment_preflight.py \
  --alias dev1 --profile profiles/a2-dev.yaml
```

Progress 在 stderr，JSON 结果在 stdout。
