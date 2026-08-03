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

当前已能执行真实 inference-request、metrics 和 tracing：

```bash
python3 scaffold/.agents/skills/motor-functional/scripts/functional_run.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '验证真实推理请求' \
  --feature inference-request
```

```bash
python3 scaffold/.agents/skills/motor-functional/scripts/functional_run.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '验证 metrics 和 tracing' \
  --feature metrics \
  --feature tracing
```

metrics adapter 通过远程 `kubectl port-forward` 访问 Coordinator observability
Service 的 `1027/metrics`：先验证 Prometheus family/sample，再以一次真实 non-stream
请求验证 `vllm:request_success_total` 正增量。由于 Motor metrics 默认有短时复用缓存，
请求后会在限定时间内轮询，而不是要求下一次 scrape 立即变化。

tracing adapter 为受控请求注入唯一且 sampled 的 W3C `traceparent`，通过 SSH tunnel
访问 `tracer_config.endpoint` 中 OTLP export host 上仓库既有 Tempo 栈（默认
`3200`；Tempo 与 collector 不同机时由 Agent 传 `--tempo-host`），轮询
`/api/traces/{trace_id}`，同时验证 trace ID 命中及 Motor span 的 `requestId` 以该
trace ID 为前缀。`tracer_config.endpoint` 为空、remote-parent sampling 为 `0`，或
Tempo 未部署/不可查询时，case 结果为 `unavailable`，不会被记为通过。它不会自动
修改 deploy 配置或拉起 observability stack。

## Tracing 可视化 Roadmap：优先复用 Grafana + Tempo，其他前端只做兼容备选

### 已有基础

本仓不需要从零建设 tracing 前端。Motor 已有的
[`observability stack`](../../../../sources/motor/examples/features/observability/stack/)
已经包含 Grafana、Tempo、OTel Collector、Prometheus 和 Loki，并预置了：

- Grafana Tempo datasource 与 TraceQL / Search 查询入口；
- Trace → Log、Trace → Metrics 和 Log → Trace 跳转；
- Node Graph、service map 和按 `x_request_id` 关联日志；
- Docker Compose 与 native runtime 两种拉起方式。

因此下一阶段的目标不是新写一个 UI，而是让 Agent 在用户要求“搞 tracing 并提供
可视化”时，复用这套 stack 完成前端准备、健康检查和 trace deep link 交付。

### 前端选型

| 前端 | Roadmap 定位 | 结论 |
|---|---|---|
| Grafana + Tempo | 默认实现 | 已有代码和 datasource provisioning；直接复用，作为第一优先级 |
| Jaeger UI | 可选兼容入口 | 仅在用户明确需要 Jaeger 操作习惯或外部系统兼容时评估；不得为它默认复制一套采集和存储 |
| Zipkin UI / 其他 OTLP 前端 | 更后期兼容项 | 只有出现明确交付环境或协议需求时才接入，不进入第一阶段 |
| CCAE 等生产运维平台 | 交付环境集成 | 复用相同 trace ID / request ID 关联语义，具体接入由目标环境决定 |

### 分阶段实施

| 阶段 | 建设内容 | 验收标准 |
|---|---|---|
| R1：Grafana 前端准备 | 基于现有 observability stack 增加 Agent 可调用的 detect / plan / start / health 流程；默认采用 minimal stack | Agent 能判断 Grafana、Tempo、OTel 是否已存在；缺失时先展示变更计划并取得部署授权，不静默拉起容器或进程 |
| R2：Functional 联动 | 保留 `tracing.request-correlated` 作为 backend 真值；新增独立的 frontend readiness / deep-link 检查，不改变现有状态模型 | 一次受控请求后，结果中同时保存 `trace_id`、Tempo 原始证据和不含凭据的 Grafana trace URL；打开 URL 能看到对应 Motor spans 与 `requestId` |
| R3：跨信号导航 | 固化 Trace → Log、Trace → Metrics、Log → Trace 和 Node Graph 的检查与使用入口 | 从目标 trace 可跳到同一请求日志和相邻时间窗口 metrics；关联失败能明确指出缺的是 label、datasource 还是数据 |
| R4：可插拔前端 | 在出现真实需求后接入 Jaeger、Zipkin 或生产运维平台 | 新前端只实现 detect / health / trace-link 映射；不得新增第二套 validation run 或 check status |

### 建议的工作流边界

```text
用户：“验证 tracing，并把可视化前端准备好”
  → Agent 生成 tracing validation spec
  → 检测已有 Grafana / Tempo / OTel
  → 缺失时输出 observability stack 变更计划并请求授权
  → 使用已有 stack 拉起或复用服务
  → 执行 tracing.request-correlated
  → 生成 Tempo evidence + Grafana deep link
  → 可选验证 Trace / Log / Metrics 跳转
```

- observability stack 的安装和拉起会改变远端状态，应由独立的 setup 操作负责并遵守
  consent；`trace-query` adapter 本身继续保持只验证、不部署。
- 默认 tracing case 仍以 Tempo API 证据作为通过依据。Grafana 不可用不能把已经通过
  的 backend correlation 改写成失败；只有用户明确要求可视化时，frontend check 才
  是本次 pass policy 的一部分。
- 第一阶段只需要显式的 `frontend kind -> detect / health / trace URL` 映射，不建设
  frontend 插件注册中心。
- Grafana 地址、账号和 token 不写入 tracked spec；运行结果只保存脱敏后的 endpoint
  和 deep link，认证材料继续使用环境变量或本地 secret path。
- 可视化用于请求级功能诊断；持续 workload 下的 trace 聚合、延迟归因和资源关联仍
  属于 Profiling。

已有 Grafana 操作和 datasource 说明见
[`GRAFANA_GUIDE.md`](../../../../sources/motor/examples/features/observability/stack/GRAFANA_GUIDE.md)，
stack 拉起方式见
[`SERVICE_GUIDE.md`](../../../../sources/motor/examples/features/observability/stack/SERVICE_GUIDE.md)。

## Metrics、Tracing 与负载监控的边界

Functional 只证明功能语义：metrics 端点和目标 series 存在、单次受控请求引起预期
变化；请求能够通过 trace ID 与 Motor `requestId` 找到对应 trace。这类检查负载极小，不用于分析
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
