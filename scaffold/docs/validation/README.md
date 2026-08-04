# 第三层以部署后场景验证为核心，不等同于 benchmark

本目录定义 motor-workspace 第三层“部署后验证与测试”的目标场景。这里先回答
每类场景负责证明什么、消费什么、交付什么，不表示仓库已经具备对应实现。

后续现状盘点应把已有 skill、脚本、source submodule 能力映射到这些场景，再决定
复用、补齐或新建。不得根据当前已有目录反向删减目标场景。

失败后的证据收集与分层诊断不属于本目录，见
[`../diagnosis/`](../diagnosis/)。

## 统一边界

第三层通常消费一个成功的 `deploy-complete`：

```text
deploy-complete
  + 服务访问地址
  + Ready 与运行代码证据
  + 目标代码、配置、模型、拓扑和环境引用
                         |
                         v
                   validation run
                         |
                    （失败时）
                         v
                   diagnosis（独立目录 / skill 族）
```

第三层负责用正式 workload 判断功能、正确性、性能、容量、稳定性和可靠性，并在
失败时引用 diagnosis 产物。

所有 Kubernetes API 操作统一使用目标 machine 上的远端 `kubectl` 和该机器记录的
`kube_context`，不读取开发机的 `kubectl`/kubeconfig。需要让本地 workload client
访问 ClusterIP/Service 时，应在目标机器执行远端 `kubectl port-forward`，再用 SSH
隧道接回本地临时端口。

第三层不负责：

- 同步本地代码或维护远端固定源码目录。
- 生成、修改或 apply Motor 部署配置。
- 修复 Pod Ready、hostPath、`PYTHONPATH` 或运行代码加载问题。
- 用一次 HTTP 连通性探测代替正式场景验证。
- 实现诊断 skill 族本身；见 [`../diagnosis/`](../diagnosis/)。

“拉起服务”属于第二层；“服务拉起后执行指定 workload 并给出可判断结果”属于
第三层。

## NodePort 冲突默认策略（决策已确认，preflight 已落地自动避让，默认 override 打开）

部署配置生成前（preflight 阶段）检测到 NodePort 被集群现有服务占用时，
自动 fallback 调整端口，不再中断询问用户：

- **preflight 阶段**：读 `motor_deploy_config.node_port_overrides` 目标端口；
  未声明时用当前 `deploy_mode` 的模板默认端口（如 `infer_service_set` 为
  31015/31017/31027）→ `kubectl get services -A` 探测集群级占用 → 冲突自动
  分配空闲端口 → 更新映射写回 `user_config.json`。范围内无空闲端口才
  fail closed。
- **configure 阶段**：消费写回后的 `node_port_overrides` 注入 manifest，生效
  映射进入配置包/bundle。
- **验证请求端口跟随**：任何直接访问 NodePort 的验证请求（包括确认拉起成功
  的探测）必须使用映射后的新端口，从 bundle 的端口映射读取，不得用旧端口
  请求——否则验证会拿到失败/误判结果。
- **当前状态**：已落地。未冲突的默认端口不写回配置，保持配置最小改动。

## 场景目录

| 目录 | 核心问题 |
|---|---|
| [`smoke/`](smoke/) | Coordinator management `/readiness` 是否报告 `ready=true` |
| [`functional/`](functional/) | 目标接口和功能行为是否符合预期 |
| [`routing-topology/`](routing-topology/) | 请求是否在指定拓扑中走到正确实例和角色 |
| [`correctness/`](correctness/) | 协议、token、输出和模型精度是否正确 |
| [`benchmark/`](benchmark/) | 相同条件下性能是多少、是否相对基线退化 |
| [`stress-capacity/`](stress-capacity/) | 饱和点、最大稳定负载和过载行为是什么 |
| [`stability/`](stability/) | 服务长时间运行是否出现泄漏、漂移或状态腐化 |
| [`reliability/`](reliability/) | 故障期间是否正确隔离、降级、恢复并继续服务 |
| [`profiling/`](profiling/) | 已发现的性能问题具体耗在哪个阶段和组件 |

## 易混淆职责对照

同一操作或同一特性名可能出现在多个场景中；判定归属时看**验证目的**，不看
workload 形态。

| 易混点 | 归谁 | 不归谁 |
|---|---|---|
| OpenAI 响应字段、SSE 分片、结束条件、错误语义 | [`correctness/`](correctness/)（协议子类） | [`functional/`](functional/) 不给协议合规结论 |
| 特性开关打开后的业务行为（API key、TLS、metrics 暴露、参数透传生效） | [`functional/`](functional/) | [`correctness/`](correctness/) |
| overload control：拒识码 / 限流响应形态是否正确 | [`functional/`](functional/) | [`stress-capacity/`](stress-capacity/) |
| overload control：升压曲线上的触发点、饱和与压力解除后恢复 | [`stress-capacity/`](stress-capacity/) | [`functional/`](functional/) |
| 有意扩缩容 / 受控摘除后的流量迁移与路由收敛 | [`routing-topology/`](routing-topology/)（主动） | [`reliability/`](reliability/) |
| 故障注入触发的隔离、降级、重拉起与恢复 | [`reliability/`](reliability/)（被动） | [`routing-topology/`](routing-topology/) |
| 过载解除后的业务/资源回到稳定态 | [`stress-capacity/`](stress-capacity/) | [`reliability/`](reliability/)（那是故障恢复） |
| 真实 non-stream/stream 请求能否走通并产生输出 | [`functional/`](functional/) 的 `inference-request` | [`smoke/`](smoke/) 只判断 Coordinator readiness；完整协议合规仍归 [`correctness/`](correctness/) |
| 长稳中观察到的重启/扩缩容后脏状态 | [`stability/`](stability/)（副作用观察） | 不是 routing/reliability 的验收主责 |
| 验证失败后的证据收集与分层定位 | [`../diagnosis/`](../diagnosis/) | 任一 validation 场景本身 |

## “打流”不是独立场景

打流是多个场景共用的 workload 执行方式：

| 打流方式 | 主要服务的场景 |
|---|---|
| 少量固定请求 | functional、correctness |
| 特征化请求集 | routing-topology、functional、correctness |
| 固定 QPS 或固定并发 | benchmark |
| 阶梯升压、突发流量 | stress-capacity |
| 长时间稳定负载 | stability |
| 持续流量叠加故障 | reliability |
| 可控负载叠加采集 | profiling |

同一个 workload 可以被多个场景复用，但每个 validation run 必须声明自己的
验证目的和通过标准。

## 公共结果契约

每类场景最终都应保存：

- 上游 `deploy_run_id` 和目标 machine。
- Motor、vLLM、vLLM Ascend 代码版本或 parity 引用。
- 模型、硬件、拓扑、实例数和关键配置。
- workload 名称、版本、输入数据、随机种子和执行参数。
- 开始/结束时间、客户端结果和服务端观测。
- 原始结果、聚合指标、通过标准和最终结论。
- 失败阶段、错误摘要以及 diagnosis artifact 引用（指向
  [`../diagnosis/`](../diagnosis/) 产物，不由本目录实现采集逻辑）。

只有结果可判断且可追溯，场景才算完成。仅保存一段终端输出或一个
`success=true` 不构成完整验证证据。

## 场景选择原则

日常开发按改动风险选择场景，不要求每次执行全部目录：

```text
所有远端代码改动
  → smoke
  → functional / inference-request
  → 与改动直接相关的 functional / routing-topology / correctness

性能热路径改动
  → benchmark
  → 发现退化或需要归因时 profiling

控制面、生命周期或故障恢复改动
  → reliability
  → 必要时 stability

发布或系统级交付
  → 功能回归 + benchmark + stress-capacity
  → 按风险增加 stability 和 reliability
```

任一场景失败时，按 [`../diagnosis/`](../diagnosis/) 的场景→skill 对应表调用诊断，
不应要求用户重新手工拼接上下文。


## 现状
| 场景 | motor-workspace 状态 | Active skill | 现有脚本/可复用资产 | 主要缺口 |
|---|---|---|---|---|
| Smoke | 实现完成；B132 镜像基线 Pod 内 `/readiness ready=true` 已验证；标准 smoke 仍 blocked | `motor-smoke` | [`smoke_run.py`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/skills/motor-smoke/scripts/smoke_run.py) 消费成功的 `deploy-complete`，发现 management Service、解析 `/readiness` 的 `ready=true` 并落盘 validation run | deploy run 因 TD-A3-11 误标 failed 导致无法消费；local-control port-forward 出现 WinError 10061（TD-A3-12）；不再承担 inference 请求 |
| Functional | inference-request 等在 B132 镜像基线 Pod 内 curl 已打通；标准 functional 仍 blocked | `motor-functional` | [`case-catalog.json`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/skills/motor-functional/references/case-catalog.json) 提供 feature/case 映射；[`functional_run.py`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/skills/motor-functional/scripts/functional_run.py) 执行 non-stream/stream 请求、`1027/metrics` 快照与 request counter 增量、W3C traceparent→Tempo trace/requestId 关联；[`mws_functional.py`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/lib/mws_functional.py) 负责 spec compiler、dispatcher 和响应判定 | 无 `status=ready` deploy-complete run 时入口校验拒绝；待 TD-A3-11 修复后接回 evidence chain；observability/TLS/overload 等仍待扩展 |
| Routing Topology | 仅有 source 测试资产 | 无 | [`test_unified_pd_router.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/motor/tests/coordinator/router/test_unified_pd_router.py)、[`test_router_pd_hybrid.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/motor/tests/coordinator/router/test_router_pd_hybrid.py)、[`test_kv_cache_affinity.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/motor/tests/coordinator/scheduler/test_kv_cache_affinity.py) | 缺真实多实例/PD 拓扑打流、实例选择证据和扩缩容前后验证 |
| Correctness | 仅有 source 测试资产 | 无 | vLLM Ascend 有 [`test_lm_eval_correctness.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm-ascend/tests/e2e/models/test_lm_eval_correctness.py)；Motor 有 [`test_precision_e2e_chain.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/motor/tests/coordinator/sampling/test_precision_e2e_chain.py) | 缺连接 Motor deploy endpoint 的 correctness adapter、基线、容差和 run 结果 |
| Benchmark | 部分实现 | `motor-benchmark` | [`SKILL.md`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/skills/motor-benchmark/SKILL.md) 和 [`bench_plan.py`](/home/h00906152/projects/pymotor/motor-workspace/scaffold/.agents/skills/motor-benchmark/scripts/bench_plan.py:17) 只校验 deploy run/machine；vLLM Ascend 有 [`run-performance-benchmarks.sh`](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm-ascend/benchmarks/scripts/run-performance-benchmarks.sh)，vLLM 有 [`benchmark_serving.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm/benchmarks/benchmark_serving.py) | 没有真实请求、指标采集、基线比较和 benchmark run 落盘；仓库技术债也明确记录了这个缺口 |
| Stress Capacity | 仅有压测原子能力 | 无 | vLLM `bench serve` 可复用；vLLM Ascend serving 配置已有固定 QPS 和 `inf`，[配置见此](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm-ascend/benchmarks/tests/serving-tests.json:1) | 缺阶梯升压、饱和点判定、瓶颈识别、过载解除后的恢复验证 |
| Stability | 未发现可信实现 | 无 | 只发现少量名为 `steady_state` 的单元测试和长期日志监听，不构成长稳测试 | 需要新建长时间 workload、资源时间序列、泄漏/漂移阈值和稳定性结果 |
| Reliability | 仅有产品测试和参考脚本 | 无 | Motor 有 fault manager、Scale P2D 等单测；[`ras_monitor.py`](/home/h00906152/projects/pymotor/motor-workspace/sources/motor/examples/features/fault_tolerance/ras_monitor/ras_monitor.py) 能探活和自动重拉 | `ras_monitor` 是健康伴侣，不是故障 validation；缺 consent、故障注入、持续打流、业务影响和恢复时间线 |
| Profiling | 仅有底层采集/分析资产 | 无 | vLLM Ascend 有 [`TorchNPUProfilerWrapper`](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm-ascend/vllm_ascend/profiler/torch_npu_profiler.py:30)；vLLM 有 [`tools/profiler/`](/home/h00906152/projects/pymotor/motor-workspace/sources/vllm/tools/profiler)；Motor 有 profiling dashboard | 缺 motor-workspace 的采集入口、workload 联动、artifact 回收和跨层分析结果 |

诊断能力现状见 [`../diagnosis/`](../diagnosis/)，不在本表展开。
