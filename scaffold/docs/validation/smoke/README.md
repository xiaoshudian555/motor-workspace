# Smoke 证明 Motor 已真正拉起并完成最小正式推理闭环

## 负责

- 消费成功的 `deploy-complete`，校验其 config run 和不可变 bundle 引用。
- 从 live Kubernetes Service 定位 Coordinator inference/mgmt 端口，不依赖固定
  NodePort。
- 在目标 machine 上执行远端 `kubectl port-forward`，通过 SSH 隧道把远端 loopback
  临时端口接回本地请求客户端；不使用开发机 kubeconfig。
- 解析 Motor `/readiness` 的 JSON 响应体，要求 `ready=true`。
- 对 OpenAI-compatible 业务接口发送少量、固定、可重复的请求。
- 使用 bundle 中真实的 `served_model_name`，覆盖 streaming 与 non-streaming
  `/v1/completions` 请求。
- 验证 HTTP/SSE、响应结构、结束标志和基本推理输出。

## 为什么不能只看 Pod Ready

Motor 的 Coordinator `/readiness` 在实例尚不可运行时会返回 HTTP 200，并在
响应体中给出 `ready=false`。当前 K8s `probe.py` 只判断 HTTP 状态码，所以 Pod
Ready 只能证明 probe 请求成功，不能证明 scheduler 已看到可运行实例。

`/startup`、`/liveness` 和 observability `/health` 也只证明对应进程存活；
`/v1/models` 又依赖可选的 AIGW 配置。因此本场景以
`/readiness ready=true + 真实推理完成` 作为“真的拉起”的最小证据。

## 完成标准

以下四项必须全部通过：

1. Coordinator inference/mgmt Service 均存在 ready endpoint。
2. `GET /readiness` 返回 HTTP 200 且 JSON `ready=true`。
3. non-streaming inference 返回非空生成结果。
4. streaming inference 返回合法 SSE choice event、非空生成结果和 `[DONE]`。

## 不负责

- 证明 deploy 阶段的 Pod Ready 或运行代码路径正确；这些是 Deploy 的责任。
  smoke 会复验 live `/readiness ready=true`，但这是推理前的 gate，不替代
  deploy proof。
- 验证完整功能矩阵、模型精度或完整协议合规；本场景对 HTTP/SSE/结构的检查
  只是 gate 级最小断言，不替代 [`../correctness/`](../correctness/)。
- 自动重启、扩缩容、重新部署或修改 Motor 配置。
- 用端口可连接、Pod Ready 或 `GET /health` 成功代替正式推理请求。

## 已有入口

```bash
python3 scaffold/.agents/skills/motor-smoke/scripts/smoke_run.py \
  --machine <alias> \
  --deploy-run-id <id>
```

API Key 通过 `MOTOR_SMOKE_API_KEY` 环境变量传入且不会落盘。TLS 场景通过
`--ca-file` 以及可选的 client cert/key 指定本地可读证书；不提供跳过证书校验
的参数。

## 交付

`motor-smoke` validation run，包括 deploy/config/bundle 引用、Service/endpoint
证据、请求参数（鉴权信息脱敏）、readiness 响应、两类原始推理响应和逐项判定。
结果位于 `.motor-workspace-local/validation-runs/{smoke_run_id}/`。失败时引用
[`../../diagnosis/`](../../diagnosis/)。
