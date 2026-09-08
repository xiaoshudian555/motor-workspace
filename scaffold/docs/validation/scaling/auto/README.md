# Auto Scaling 证明指标驱动自动扩缩容链路

## 负责

- 验证 Motor metrics、Prometheus、External Metrics Adapter、HPA 和 Infer Operator
  之间的数据链路完整。
- 验证 `scalingPolicy` 生成后引用的 external metric、selector 和阈值与实际任务匹配。
- 通过受控 workload 触发扩容，并在负载回落后验证缩容或稳定回落策略。
- 记录 HPA status、events、InstanceSet/Pod 变化、Coordinator 感知和推理可用性。

## 当前用例

| 用例 | 说明 |
|---|---|
| [`hpa-composite-metrics.md`](hpa-composite-metrics.md) | 使用 Prometheus Adapter `externalRules.metricsQuery` 暴露 A+B 等复合指标，并验证 HPA 自动扩缩容 |

## 完成标准

原始指标、复合 external metric、HPA selector、生成 YAML、HPA 状态、扩容行为和回落行为
均有证据；多任务隔离必须由 `kubernetes_namespace` 和
`infer_huawei_com_inferservice_name` 两个标签共同证明。

## 不负责

- 人工 `deploy.py --update_instance_num` 扩缩容；该责任属于 [`../manual/`](../manual/)。
- 把打流过程当作容量上限分析；容量边界属于
  [`../../stress-capacity/`](../../stress-capacity/)。
- 深入解释性能瓶颈；性能归因属于 [`../../profiling/`](../../profiling/)。

## 交付

`auto` 证据目录包括 adapter 配置摘要、external metric 查询结果、生成 YAML 检查、
HPA status/events、负载时间线、Pod/InstanceSet 变化、推理结果、路由收敛证据和失败倒查。
