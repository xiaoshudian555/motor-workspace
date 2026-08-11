# Coordinator 访问端点（Smoke / Functional / 手工 curl）

Motor Coordinator 在 Kubernetes 里通常拆成 **三个 Service**，端口固定、用途不同。
混用 ClusterIP 与 NodePort 是最常见的 curl 失败原因。

## 端口与角色

| 角色 | Service 名（典型） | Service port | 用途 |
|------|-------------------|--------------|------|
| **infer** | `mindie-motor-coordinator-infer` | **1025** | 推理：`/v1/completions`、`/v1/chat/completions` 等 |
| **mgmt** | `mindie-motor-coordinator-mgmt` | **1026** | 管理：`/readiness`、`/startup`、`/liveness` |
| **obs** | `mindie-motor-coordinator-obs` | **1027** | 可观测：`/metrics`、`/health` |

实际端口以当前 Kubernetes Service 为准；上表是 Motor 的默认约定。

## 常见错误（禁止）

| 错误做法 | 后果 |
|----------|------|
| 用 **mgmt ClusterIP**（如 `10.111.243.83`）打 **infer 端口**（1025 或 NodePort 31015） | TCP 连不上或一直挂起直到超时 |
| 把 **mgmt 的 1026** 当成推理端口 | 只能做 readiness，不能推理 |
| NodePort **31015** 打到错误的 IP（非节点 InternalIP） | `connect timeout` |
| 从 `user_config.json` **顶层**读 `served_model_name` | `KeyError` / model 为空 |

**规则：infer 走 infer Service；mgmt 走 mgmt Service。不要交叉。**

## 推荐访问方式（workspace 标准）

### 1. motor-smoke — 只验 readiness

- 发现 **mgmt** Service（port 1026）
- `kubectl port-forward` 到本地 loopback
- `GET /readiness`，解析 JSON **`ready=true`**
- **不发推理请求**

### 2. motor-functional — 验推理 / metrics / tracing

- 发现 **infer**（1025）和/或 **obs**（1027）Service
- **`port-forward` 到 infer Service**，不手工拼 NodePort
- 从本次部署使用的原生 `user_config.json` 解析 served model name
- 推理 case 默认：`POST /v1/completions`（见下文「与部署文档差异」）

### 3. 集群内手工 curl（仅当无法 port-forward 时）

在 **master/worker 节点**上：

```bash
# 推理 — ClusterIP（同 namespace 或集群内）
curl -sS -X POST "http://<infer-cluster-ip>:1025/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"<served_model_name>","messages":[{"role":"user","content":"hi"}],"max_tokens":8,"stream":false}'

# 就绪 — mgmt ClusterIP
curl -sS "http://<mgmt-cluster-ip>:1026/readiness"

# 推理 — NodePort（必须用节点 InternalIP，不是 mgmt ClusterIP）
curl -sS -X POST "http://<node-internal-ip>:31015/v1/chat/completions" ...
```

查 Service / IP：

```bash
kubectl get svc -n <namespace> | grep coordinator
kubectl get nodes -o wide
```

## model 名解析

`served_model_name` 在 engine 配置段内，不在顶层：

- `motor_engine_prefill_config.engine_config.served_model_name`
- 或 `motor_engine_decode_config.engine_config.served_model_name`

直接从本次部署使用的原生 `user_config.json` 读取上述字段，不要从顶层猜测。

## 与部署文档的差异

Motor 部署指南（`sources/motor/docs/.../pd_disaggregation_deployment.md`）示例使用：

- `POST /v1/chat/completions` + `messages`

**motor-functional** 默认使用：

- `POST /v1/completions` + `prompt`

选择与当前模型和验证目标匹配的接口；不要由 wrapper 固定接口或返回字段。

## Skill 边界

| 需求 | Skill |
|------|-------|
| 部署后 Coordinator 是否 ready | **motor-smoke** |
| 推理能否打通、metrics/tracing | **motor-functional** |
| 长 prompt / cache / 性能 | 压测或专项诊断，**勿**在 smoke 里 curl 推理 |
