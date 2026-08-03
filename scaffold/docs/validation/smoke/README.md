# Smoke 只证明 Coordinator 报告 Ready

## 负责

- 消费成功的 `deploy-complete`，校验 config run 和不可变 bundle 引用。
- 从 live Kubernetes Service 定位 Coordinator management 端口。
- 在目标 machine 上执行远端 `kubectl port-forward`，通过 SSH 隧道接回本地。
- 调用 Coordinator management `GET /readiness`，要求 HTTP 200 且响应体
  `ready=true`。

## 为什么不能只看 Pod Ready

Motor 的 Coordinator `/readiness` 在实例尚不可运行时仍可能返回 HTTP 200，但
响应体为 `ready=false`。当前 K8s `probe.py` 只判断 HTTP 状态码，所以 Smoke 必须
解析 readiness body。

## 完成标准

1. Coordinator management Service 存在 ready endpoint。
2. `GET /readiness` 返回 HTTP 200 且 JSON `ready=true`。

Smoke 到此结束，不再发送 inference 请求。

## 不负责

- non-stream/stream 真实推理请求；移到 [`../functional/`](../functional/) 的
  `inference-request` cases。
- metrics、tracing、API key、TLS、参数透传等功能行为。
- OpenAI/SSE 协议合规、模型精度、性能或容量结论。
- 自动重启、扩缩容、重新部署或修改 Motor 配置。

## 已有入口

```bash
python3 scaffold/.agents/skills/motor-smoke/scripts/smoke_run.py \
  --machine <alias> \
  --deploy-run-id <id>
```

TLS 场景通过 `--ca-file` 以及可选 client cert/key 访问 management readiness；
不提供跳过证书校验的参数。

## 交付

`motor-smoke` validation run，包括 deploy/config/bundle 引用、management
Service/endpoint 证据、readiness 原始响应和判定。结果位于
`.motor-workspace-local/validation-runs/{smoke_run_id}/`。
