# Benchmark 证明相同条件下的性能水平和相对基线变化

## 负责

- 执行版本明确、参数固定、可重复的 latency、throughput 和 online serving
  workload。
- 覆盖目标输入/输出长度、stream 模式、请求到达方式、拓扑和实例配置。
- 采集吞吐、E2E latency、TTFT、TPOT/ITL、成功率和尾延迟等指标。
- 保存 raw result，并与同硬件、模型、配置和 workload 的合法基线比较。
- 明确区分绝对性能结果与性能回归结论。

## 完成标准

完成 warmup 和正式测量，环境及 workload 可复现，原始结果与聚合指标完整；
需要比较时，基线兼容且回归阈值明确。

## 不负责

- 通过持续升压寻找系统崩溃点；该责任属于
  [`stress-capacity/`](../stress-capacity/)。
- 解释性能时间具体耗在哪里；该责任属于
  [`profiling/`](../profiling/)。
- 用 dummy weight 结果声明真实模型端到端性能。

## 交付

`benchmark` 证据目录包括环境指纹、workload 参数、warmup、raw result、
聚合指标、基线比较和结论。失败时引用
[`../../diagnosis/`](../../diagnosis/)。

## 设计记录

- [`aisbench-wrapper-review.md`](aisbench-wrapper-review.md)：2026-08-20 对
  `motor-benchmark`、AISBench 官方仓库和 `aisbench_auto_tools_prefix` wrapper 的
  审查过程与建议修改顺序。
