# Smoke 只验证 Coordinator management readiness

从当前 namespace 发现 management Service，使用远端 `kubectl port-forward` 或
目标 Host 可达地址请求 1026 端口的 `GET /readiness`。

## 轮询与通过标准

| 参数 | 默认 |
|---|---|
| 间隔 | 15s |
| 上限 | 600s |

通过标准同时满足：

- Service 有 ready endpoint；
- 最终一次 poll：HTTP 200 且 JSON body 中 `ready=true`；
- 临时 port-forward 已清理。

**等待 vs 失败**

- HTTP 200 + `ready=false`：**等待中**，继续轮询并记录每次 timestamp / body；
- 超时仍为 `ready=false`：**FAIL**；
- Pod Ready、TCP connect、`/health` 或 `/v1/models` **不能**替代此标准。

推理请求属于 functional，并使用独立的 infer Service 1025。
