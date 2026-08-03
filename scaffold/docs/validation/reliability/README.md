# Reliability 证明故障期间能够正确隔离、降级、恢复并继续服务

## 负责

- 在持续打流背景下执行受控故障注入（被动触发，区别于主动扩缩容验证）。
- 验证实例隔离、自动重拉起注册、缩 P 保 D、模式回退和 token 级重推等目标
  可靠性行为。
- 覆盖 Engine/Pod、NodeManager、Coordinator、Controller、KV Cache 链路和
  明确支持的 Host/NPU/网络故障场景。
- 记录故障发生、检测、隔离、降级、恢复、重新接流的完整时间线。
- 量化故障期间的请求失败、超时、中断、恢复时间和恢复后性能。

## 完成标准

故障注入对象和预期策略明确；业务影响、状态转换和恢复结果均有证据，恢复后
服务重新达到目标状态且持续请求能够正常完成。

## 不负责

- 未经明确授权执行破坏性故障注入。
- 仅检查 Pod 最终重新 Ready，而忽略故障期间业务表现。
- 在故障恢复 validation 中顺便修复 Deploy、K8s 或 Host 环境。
- 验证有意扩缩容或受控摘除后的路由收敛；该责任属于
  [`../routing-topology/`](../routing-topology/)（主动）。
- 验证过载解除后的容量恢复；该责任属于
  [`../stress-capacity/`](../stress-capacity/)。故障恢复 ≠ 过载恢复。

## 交付

`reliability` validation run，包括 consent、注入动作、持续 workload、故障与
恢复时间线、业务影响、状态证据和完整
[`../../diagnosis/`](../../diagnosis/) artifacts。
