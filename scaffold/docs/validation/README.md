# 部署后验证直接读取原生配置和实时服务

| 场景 | 证明什么 |
|---|---|
| smoke | Coordinator management readiness body 为 `ready=true` |
| functional | inference、metrics、tracing 等具体功能行为 |
| benchmark | 给定 workload 下的性能数据 |
| routing-topology | 请求路由和拓扑行为 |
| correctness | 输出正确性 |
| stress-capacity | 饱和点和容量 |
| stability | 长时间稳定运行 |
| profiling | 性能瓶颈归因 |
| reliability | 由 `motor-reliability` 执行已支持的故障注入、隔离和恢复场景；其他场景显式报告 capability gap |

所有场景直接使用当前 native config 和 K8s/Service 状态，不消费
`deploy-complete` 或生成 validation run。原始证据按用户指定路径保存；未指定时
可放 untracked `.motor-workspace-local/`。
