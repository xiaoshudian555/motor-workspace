# Functional 证明目标接口和业务功能按设计工作

## 负责

- 围绕本次改动选择功能 case，而不是无目标地发送请求。
- 承接原 Smoke 中的真实 non-stream/stream inference 请求，证明 Coordinator
  Ready 后业务请求确实能走通。
- 第一优先级验证 metrics 和 tracing：接口/数据是否暴露、单次受控请求能否关联到
  预期 metric 变化和 trace 证据。
- 后续再扩 TLS、参数透传、overload control 等功能行为；API key 当前无人使用，
  明确延后，不作为近期建设重点。
- 验证 streaming / non-streaming 等模式下，目标功能的正向与关键失败路径。
- 对 overload control：只验证启用后的拒识码、限流响应形态等行为是否正确。
- 保存客户端结果，并用日志、metrics 或 tracing 证明功能确实生效。

## 完成标准

每个 case 都有明确前置配置、输入、预期行为和实际结果；目标功能的正向与关键
失败路径均可判断。

## 最小编排模型

用户只描述验证目标，不手写验证配置。Agent 读取 `motor-functional` 的 case
catalog，把口头目标解析成 feature/case ID，再生成一次运行对应的不可变
`mws.functional.spec.v1`：

```text
用户口头目标
  → feature / case 映射
  → validation spec（目标 deploy、cases、预期、证据、pass policy）
  → adapter dispatcher
  → mws.result.v1 checks + artifacts
```

当前 catalog 以 metrics、tracing 和 `inference-request` 为前排能力；TLS、参数透传、
overload-control 和 API key 作为后续项。dispatcher 只是显式的
`adapter -> handler` 字典，不引入插件注册中心或第二套状态模型。尚未实现的
adapter 必须返回 `unavailable`，不能把“成功生成 spec”当成“功能验证通过”。

入口：

```bash
python3 scaffold/.agents/skills/motor-functional/scripts/compile_spec.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '<用户原始描述>' \
  --feature metrics
```

当前已能执行从 Smoke 移入的真实推理请求：

```bash
python3 scaffold/.agents/skills/motor-functional/scripts/functional_run.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '验证真实推理请求' \
  --feature inference-request
```

## Metrics、Tracing 与负载监控的边界

Functional 只证明功能语义：metrics 端点和目标 series 存在、单次受控请求引起预期
变化；请求能够通过 correlation ID 找到对应 trace。这类检查负载极小，不用于分析
CPU/NPU、内存、通信、吞吐或延迟瓶颈。

在持续或可控 workload 下采集资源曲线、Motor metrics、请求 tracing、NPU/CPU
profiler 并做性能归因，属于 [`../profiling/`](../profiling/)。如果目的是寻找饱和点
或最大稳定负载，则属于 [`../stress-capacity/`](../stress-capacity/)；如果目的是性能
基线比较，则属于 [`../benchmark/`](../benchmark/)。

## 不负责

- 给出 OpenAI 响应字段、SSE 分片、结束条件或错误语义的协议合规结论；该责任
  属于 [`../correctness/`](../correctness/)。
- 判断请求最终应该路由到哪个 Prefill、Decode 或 hybrid 实例；该责任属于
  [`../routing-topology/`](../routing-topology/)。
- 做模型级 accuracy evaluation；该责任属于
  [`../correctness/`](../correctness/)。
- 在升压曲线上寻找 overload 触发点、饱和区或压力解除后的恢复；该责任属于
  [`../stress-capacity/`](../stress-capacity/)。
- 在负载下持续监控 CPU/NPU、内存、通信和请求阶段并做瓶颈归因；该责任属于
  [`../profiling/`](../profiling/)。
- 给出性能是否退化的结论。

## 交付

`functional` validation run，包括功能 case、配置、输入输出、服务端行为证据、
通过标准和 [`../../diagnosis/`](../../diagnosis/) 引用。
