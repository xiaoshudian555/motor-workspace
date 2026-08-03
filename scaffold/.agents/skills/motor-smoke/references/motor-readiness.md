# Motor Coordinator 就绪语义

本 Smoke 只判断 Coordinator readiness，不判断推理请求功能：

- `Coordinator /startup` 只说明管理进程开始启动。
- `/liveness` 只检查 Daemon/心跳存活。
- `/readiness` 在实例不足时仍可能返回 HTTP 200，但响应体为 `ready=false`；
  Motor 的 `probe.py` 只检查 HTTP 状态码，因此必须解析响应体并要求
  `ready=true`。
- `/health` 是 observability 进程存活信号，不替代 Coordinator readiness。
- `/v1/models` 依赖可选 AIGW 配置，不作为通用 readiness 条件。

真实 non-stream/stream inference 已移到 `motor-functional` 的
`inference-request` cases。Smoke 成功只表示 Coordinator 自己报告 ready，不表示
任意业务接口、协议、metrics 或 tracing 已验证通过。
