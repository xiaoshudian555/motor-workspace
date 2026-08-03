# Diagnosis 是跨闭环失败出口，不是第三层验证场景

本目录定义 motor-workspace 的诊断目标与 skill 族入口。Diagnosis 可被 Deploy
失败路径或任一 validation 场景失败后调用，负责留下可关联、可追溯的证据，并按
失败位置调度对应诊断能力。

Diagnosis **不属于** [`../validation/`](../validation/)：验证场景回答
“是否通过”；诊断回答“失败时证据是否够、应往哪一层查”。

当前 `motor-diagnosis` 是已有的 deploy-oriented 采集入口；后续应按下表扩展为
skill 族，而不是继续把所有排查逻辑堆进单一脚本。

## 统一边界

```text
deploy 失败  ──┐
               ├──> diagnosis skill 族
validation 失败 ─┘
                 │
                 v
        场景/层级 → skill 对应表
                 │
                 v
     run-scoped artifacts + 时间线 + 失败位置
```

## 负责

- 接收失败或异常的 deploy / validation run，继承其 machine、workload 和时间
  范围。
- 按客户端、Motor、Engine、K8s、Host/NPU 层次收集并标记证据。
- 保存原始 artifact、采集失败信息、跨层时间线和初步错误摘要。
- 通过场景→skill 对应表调度专项诊断，而不是要求用户手工拼接命令和日志。

## 不负责

- 把“收集到日志”直接当成根因结论。
- 修改业务代码、重启服务、删除资源或自动执行恢复动作。
- 用 diagnosis 成功掩盖原 deploy / validation 失败。
- 代替 smoke、functional、benchmark 等场景给出通过/不通过结论。

## 场景 / 失败面 → 诊断 skill 对应表

下表是目标路由骨架。未落地的 skill 表示“将来挂接”，不得假装已实现。

| 触发来源或失败面 | 目标诊断入口（规划） | 当前状态 |
|---|---|---|
| Deploy apply / Ready / runtime source proof 失败 | `motor-diagnosis`（deploy 采集） | 部分实现：Pod、Event、context、manifest |
| [`smoke`](../validation/smoke/) 失败 | Coordinator management Service + readiness 响应诊断 | 缺：readiness 响应与服务端日志时间范围联动 |
| [`functional`](../validation/functional/) 失败 | inference 客户端响应 + 特性行为证据（日志 / metrics / tracing） | 未落地 |
| [`routing-topology`](../validation/routing-topology/) 失败 | 实例选择 / 路由表 / 流量迁移时间线 | 未落地 |
| [`correctness`](../validation/correctness/) 失败 | 输出 diff、基线、容差与采样配置取证 | 未落地 |
| [`benchmark`](../validation/benchmark/) / [`stress-capacity`](../validation/stress-capacity/) 失败或异常 | 客户端指标 + Motor/Engine metrics + 资源曲线 | 未落地 |
| [`stability`](../validation/stability/) 失败 | 资源/业务时间序列与异常事件关联 | 未落地 |
| [`reliability`](../validation/reliability/) 失败 | 故障注入动作、隔离/恢复时间线、业务影响 | 未落地 |
| [`profiling`](../validation/profiling/) 产物不足或无法归因 | profiler artifact 完整性与跨层时间线对齐 | 未落地 |
| Host / NPU / 网络层可疑 | Host-NPU 专项诊断（规划） | 未落地 |

调用约定：validation 或 deploy 只声明失败阶段与引用；具体采集由本目录对应
skill 执行。新增诊断能力时先更新本表，再实现 skill。

## 完成标准

成功 artifact 和采集失败项都被记录；每份证据带来源、目标、时间范围和完整性
信息，并能回链原 deploy run 与（若有）validation run。

## 交付

run-scoped diagnosis artifacts、证据索引、跨层时间线、失败位置判断和仍待确认
的问题。该结果被原 run 引用，但不会改变原测试或部署结论。

## 现状

| 能力 | motor-workspace 状态 | Active skill | 现有资产 | 主要缺口 |
|---|---|---|---|---|
| Deploy 失败采集 | 部分实现 | `motor-diagnosis` | [`diagnosis_collect.py`](../../.agents/skills/motor-diagnosis/scripts/diagnosis_collect.py) 校验 deploy/config/bundle，收集 Pod、Event、context 和 manifest；契约测试见 [`test_diagnosis.py`](../../tests/test_diagnosis.py) | 缺 Pod 日志、客户端响应、metrics、tracing、Host/NPU 证据、validation 时间范围和跨层时间线 |
| Validation 失败联动 | 未实现 | 无 | 各 validation 场景 README 仅要求引用 diagnosis | 缺场景→skill 调度与 validation-scoped 采集 |
| 分层专项 skill 族 | 未实现 | 无 | 上表为规划骨架 | 按失败面拆分 skill，避免单脚本膨胀 |
