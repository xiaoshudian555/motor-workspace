# Functional 使用直接请求证明目标功能

从原生配置和实时 K8s 状态解析 namespace、served model、infer/metrics Service
和 tracing backend。按 Skill case catalog 选择最小用例，然后用 `remote.*`、
`kubectl port-forward`、HTTP 和后端查询工具直接执行。

当前重点：

- non-stream/stream inference：infer Service 1025，`POST /v1/completions`；
- metrics：控制请求前后查询预期 series；
- tracing：注入 W3C `traceparent` 并在 Tempo 查询同一 trace。

管理面 1026 只做 readiness。缺失 backend、关闭的功能或未实现 case 必须报告为
unavailable。Functional 不证明性能、正确性、稳定性或 Reliability。
