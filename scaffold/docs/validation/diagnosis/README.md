# Diagnosis 为所有验证失败提供可关联、可追溯的证据出口

## 负责

- 接收失败或异常的 validation run，继承其 deploy、machine、workload 和时间
  范围。
- 收集客户端请求响应、Motor/Engine 日志、K8s Event、Pod 状态、metrics、
  tracing 和相关 Host/NPU 证据。
- 按客户端、Motor、Engine、K8s、Host/NPU 层次标记失败位置。
- 保存原始 artifact、采集失败信息、时间线和初步错误摘要。
- 让后续诊断能够复现上下文，而不依赖用户重新手工拼接命令和日志。

## 完成标准

成功 artifact 和采集失败项都被记录；每份证据带来源、目标、时间范围和完整性
信息，并能回链原 validation run 与 deploy run。

## 不负责

- 把“收集到日志”直接当成根因结论。
- 修改业务代码、重启服务、删除资源或自动执行恢复动作。
- 用 diagnosis 成功掩盖原 validation 失败。

## 交付

run-scoped diagnosis artifacts、证据索引、跨层时间线、失败位置判断和仍待确认
的问题。该结果被原 validation run 引用，但不会改变原测试结论。
