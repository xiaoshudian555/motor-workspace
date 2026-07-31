# Smoke 证明服务能够完成最小正式推理闭环

## 负责

- 消费成功的 `deploy-complete` 和服务地址。
- 对 OpenAI-compatible 业务接口发送少量、固定、可重复的请求。
- 覆盖基线要求的 streaming 与 non-streaming 请求。
- 验证 HTTP/SSE、响应结构、结束标志和基本推理输出。
- 在请求前后读取必要的 health 和 metrics，确认服务没有立即异常。

## 完成标准

指定 smoke case 全部执行，客户端拿到符合协议的完整响应，服务端没有出现会
使结果不可接受的错误，并保存请求、响应、日志摘要和判定结果。

## 不负责

- 证明 Pod Ready 或运行代码路径正确；这些是 Deploy 的责任。
- 验证完整功能矩阵、模型精度或性能指标。
- 用端口可连接、`GET /health` 成功代替正式推理请求。

## 交付

`smoke` validation run，包括 case 清单、原始请求响应、通过标准、结果和失败时
的 diagnosis 引用。
