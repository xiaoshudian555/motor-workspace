# 真实集群纵向验收清单（P1-05）

> **状态：待环境。** 本地 fixture 已全部通过不代表本清单完成。
> 必须由仓库所有者提供真实 K8s/MindCluster/Ascend 环境后逐项执行。

## 前置条件

- P0/P1 代码 diff 经人工 review 并合入工作分支；
- 目标 machine 已通过 `machine-management verify` 且持有显式 `machine_run_id`；
- 已获得逐次授权：远端目录覆盖、apply、restart、stop（如需）。

## 验收顺序

```text
machine-management verify          → machine-ready run
remote-code-parity                 → parity-complete run（--approved-overwrite）
motor-deploy-preflight             → deploy-environment-ready run
motor-deploy-configure             → deploy-config-ready + immutable bundle
motor-k8s-deploy apply             → deploy-complete run（--approved-by-user）
（可选）deploy_restart             → 新 runtime source evidence
（失败时）motor-diagnosis          → deploy-diagnosis artifacts
```

## 每步必须保留的证据

| 步骤 | run kind | 本地目录 |
|------|----------|----------|
| verify | `machine-ready` | `.motor-workspace-local/machine-runs/{id}/` |
| parity | `parity-complete` | `.motor-workspace-local/parity-runs/{id}/` |
| preflight | `deploy-environment-ready` | `.motor-workspace-local/environment-runs/{id}/` |
| configure | `deploy-config-ready` | `.motor-workspace-local/config-runs/{id}/` + `config-bundles/{fingerprint}/` |
| deploy | `deploy-complete` | `.motor-workspace-local/deploy-runs/{id}/` |
| diagnosis | `deploy-diagnosis` | `.motor-workspace-local/validation-runs/{id}/` |

下游必须通过**显式 run ID** 引用上游，不得使用 inventory `last_*` 字段。

## 通过标准

1. **Preflight（只读）**：Kubernetes API、MindCluster/Volcano CRD、device plugin 等基础检查为 `ok` 或已记录 `warning`；不创建任何 K8s 资源。
2. **Configure**：Motor 原生 `user_config.json` / `env.json` 生成不可变 bundle；namespace 已存在；server-side dry-run 通过；manifest hostPath/`PYTHONPATH` 与 parity 固定路径一致。
3. **Apply**：bundle digest/fingerprint 与 config run 匹配；关键 Pod Ready；最小服务可访问。
4. **Runtime proof**：Pod 内 `motor`、`vllm`、`vllm_ascend` 加载路径与当前 parity 固定目录一致。
5. **Diagnosis（失败路径）**：能从 deploy run + config/bundle 收集 pods/events，**不依赖** legacy `plan_dir`。

## 明确不算验收通过的情况

- 仅本地 pytest fixture 全绿；
- 跳过 preflight 直接 apply；
- 未保存 run/bundle 证据；
- 使用 inventory 诊断字段代替 run 引用。

## 建议记录模板

验收完成后在 `~/projects/doc/业务用/` 或团队指定位置保存一份纪要，包含：

- 日期、machine alias、各步 run ID；
- 失败项与 diagnosis artifact 路径；
- 与 parity 固定路径对照的 runtime proof 摘要。
