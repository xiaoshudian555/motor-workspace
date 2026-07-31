# 第三层以部署后场景验证为核心，不等同于 benchmark

本目录定义 motor-workspace 第三层“部署后验证与测试”的目标场景。这里先回答
每类场景负责证明什么、消费什么、交付什么，不表示仓库已经具备对应实现。

后续现状盘点应把已有 skill、脚本、source submodule 能力映射到这些场景，再决定
复用、补齐或新建。不得根据当前已有目录反向删减目标场景。

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
```

第三层负责用正式 workload 判断功能、正确性、性能、容量、稳定性和可靠性，并在
失败时保存诊断证据。

第三层不负责：

- 同步本地代码或维护远端固定源码目录。
- 生成、修改或 apply Motor 部署配置。
- 修复 Pod Ready、hostPath、`PYTHONPATH` 或运行代码加载问题。
- 用一次 HTTP 连通性探测代替正式场景验证。

“拉起服务”属于第二层；“服务拉起后执行指定 workload 并给出可判断结果”属于
第三层。

## 场景目录

| 目录 | 核心问题 |
|---|---|
| [`smoke/`](smoke/) | 服务能否完成最小但正式的推理闭环 |
| [`functional/`](functional/) | 目标接口和功能行为是否符合预期 |
| [`routing-topology/`](routing-topology/) | 请求是否在指定拓扑中走到正确实例和角色 |
| [`correctness/`](correctness/) | 协议、token、输出和模型精度是否正确 |
| [`benchmark/`](benchmark/) | 相同条件下性能是多少、是否相对基线退化 |
| [`stress-capacity/`](stress-capacity/) | 饱和点、最大稳定负载和过载行为是什么 |
| [`stability/`](stability/) | 服务长时间运行是否出现泄漏、漂移或状态腐化 |
| [`reliability/`](reliability/) | 故障期间是否正确隔离、降级、恢复并继续服务 |
| [`profiling/`](profiling/) | 已发现的性能问题具体耗在哪个阶段和组件 |
| [`diagnosis/`](diagnosis/) | 验证失败时是否留下足够、可关联、可追溯的证据 |

## “打流”不是独立场景

打流是多个场景共用的 workload 执行方式：

| 打流方式 | 主要服务的场景 |
|---|---|
| 少量固定请求 | smoke、functional、correctness |
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
- 失败阶段、错误摘要以及 diagnosis artifact 引用。

只有结果可判断且可追溯，场景才算完成。仅保存一段终端输出或一个
`success=true` 不构成完整验证证据。

## 场景选择原则

日常开发按改动风险选择场景，不要求每次执行全部目录：

```text
所有远端代码改动
  → smoke
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

`diagnosis` 是所有场景的失败出口，不应要求用户重新手工拼接上下文。
