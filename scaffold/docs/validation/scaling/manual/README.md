# Manual Scaling 证明人工显式扩缩容链路

## 负责

- 验证用户或套组明确要求的实例数变更能够通过 Motor 原生扩缩容入口生效。
- 记录配置字段变化、deployer 命令、InstanceSet/Pod 变化、Coordinator 注册变化和推理结果。
- 验证扩缩容后服务仍可推理，并在需要时确认路由收敛。

## 当前执行入口

交互式或套组内的人工在线扩容由 `motor-scale` 执行。当前已支持的 profile 以
`sources/motor/.agents/skills/motor-scale/` 为准。

## 完成标准

起始拓扑、目标拓扑和变更命令明确；目标副本数在限定时间内收敛；扩缩容后
Coordinator readiness、实例注册和控制请求均符合预期。

## 不负责

- 指标驱动的 HPA 自动扩缩容；该责任属于 [`../auto/`](../auto/)。
- 用 HPA 或 Operator 自主调节替代人工扩缩容 profile。
- 未授权修改配置、apply、restart、stop 或执行第二次扩缩容。

## 交付

`manual` 证据目录包括授权记录、配置 diff、命令、扩缩容前后拓扑、Pod/InstanceSet
状态、Coordinator 证据、推理结果和失败诊断引用。
