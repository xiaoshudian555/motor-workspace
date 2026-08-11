# Smoke 只验证 Coordinator management readiness

从当前 namespace 发现 management Service，使用远端 `kubectl port-forward` 或
目标 Host 可达地址请求 1026 端口的 `GET /readiness`。

通过标准同时满足：

- Service 有 ready endpoint；
- HTTP 200；
- JSON body 中 `ready=true`；
- 临时 port-forward 已清理。

Pod Ready、TCP connect、`/health` 或 `/v1/models` 不能替代这个标准。推理请求属于
functional，并使用独立的 infer Service 1025。
