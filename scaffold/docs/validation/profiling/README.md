# Profiling 解释性能时间具体耗在哪个阶段和组件

## 负责

- 由 benchmark 回归、容量瓶颈或明确的性能优化目标触发。
- 在可控 workload 下采集 CPU、NPU、通信、scheduler、KV transfer 和请求级
  tracing 等 profiling 数据。
- 承接“负载下监控”：记录 workload 期间的资源曲线、Motor metrics、请求 tracing
  和 profiler 时间线，并将观察结果用于性能归因。
- 将 TTFT、TPOT/ITL 或吞吐变化拆解到 Prefill、Decode、排队、调度、通信和
  kernel 等阶段。
- 对齐客户端指标、Motor metrics、请求 tracing 和底层 profiler 时间线。
- 给出瓶颈、影响 workload、证据和下一步优化方向。

## 完成标准

profiling workload 能复现目标性能现象；采集范围、开销和环境明确；结论能够
从指标逐层追溯到具体阶段或组件。

## 不负责

- 在没有性能问题或优化目标时默认执行高开销采集。
- 只交付 profiler 原始目录而不给出分析结论。
- 用 profiling 结果替代可重复的 benchmark 比较。
- 只验证 metrics 端点是否存在或单次请求是否产生预期 series/trace；这类功能语义
  属于 [`../functional/`](../functional/)。

## 交付

`profiling` validation run，包括触发原因、复现 workload、采集配置、raw
artifact、跨层时间线、瓶颈结论和基线关联。产物不足或无法归因时引用
[`../../diagnosis/`](../../diagnosis/)。
