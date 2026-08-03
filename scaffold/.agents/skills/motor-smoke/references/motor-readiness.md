# Motor 就绪语义

本 Smoke 的判定来自当前 Motor 实现，而不是通用 Kubernetes 经验：

- `Coordinator /startup` 只说明管理进程开始启动。
- `/liveness` 只检查 Daemon/心跳存活。
- `/readiness` 在实例不足时仍可能返回 HTTP 200，但响应体为 `ready=false`；
  Motor 的 `probe.py` 只检查 HTTP 状态码，因此 Pod `Ready` 不能单独证明推理可用。
- `/health` 是 observability 进程存活信号，不检查 scheduler 和推理实例。
- `/v1/models` 依赖 AIGW model 配置，未配置时会返回 503，不能作为通用前置条件。
- `/v1/completions` 会先检查 scheduler 的 `InstanceReadiness.is_run()`，再进入真实
  request handler 和 engine 链路。因此必须至少完成一条正式推理请求。

默认同时执行 non-streaming 和 streaming，是为了分别证明完整 JSON 返回路径和
SSE 增量返回/结束路径；它们仍属于最小拉起验证，不替代功能、正确性或性能测试。

