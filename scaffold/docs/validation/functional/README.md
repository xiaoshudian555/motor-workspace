# Functional 证明目标接口和业务功能按设计工作

## 负责

- 围绕本次改动选择功能 case，而不是无目标地发送请求。
- 验证 OpenAI-compatible 接口的正常、边界和错误路径。
- 验证 streaming、non-streaming、参数透传和典型请求组合。
- 验证 overload control、API key、TLS、metrics、tracing 等启用特性的行为。
- 保存客户端结果，并用日志、metrics 或 tracing 证明功能确实生效。

## 完成标准

每个 case 都有明确前置配置、输入、预期行为和实际结果；目标功能的正向与关键
失败路径均可判断。

## 不负责

- 判断请求最终应该路由到哪个 Prefill、Decode 或 hybrid 实例；该责任属于
  [`routing-topology/`](../routing-topology/)。
- 做模型级 accuracy evaluation；该责任属于
  [`correctness/`](../correctness/)。
- 给出性能是否退化的结论。

## 交付

`functional` validation run，包括功能 case、配置、输入输出、服务端行为证据、
通过标准和 diagnosis 引用。
