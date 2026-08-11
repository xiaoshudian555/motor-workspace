# 三个闭环直接消费当前事实，不再通过本地 run 状态机交接

## 1. 远端开发准备

```text
repo-init（Git/gh 直接命令）
→ machine inventory + remote.probe
→ remote-code-parity（确需同步时）
→ motor-build-wheel（确需替换 Motor 包时）
```

交付物是可用 endpoint、固定远端源码目录、可选 parity state，以及 wheel 模式下
直接返回的构建结果和远端 `wheel.sha256` marker。

## 2. Motor 部署

```text
原生 user_config.json + env.json
→ read-only preflight
→ upstream deploy.py --dry-run
→ 用户授权
→ upstream deploy.py
→ kubectl 验证当前资源
```

不生成 workspace profile、plan、immutable bundle、fingerprint 或 deploy run。

## 3. 部署后验证

- smoke：Coordinator management `/readiness` 必须 HTTP 200 且 `ready=true`；
- functional：真实 inference、metrics、tracing 等目标行为；
- benchmark：明确 workload 下的性能数据；
- diagnosis：失败时收集当前 Pod/Event/log 和 upstream auto-log 证据。

各验证 Skill 直接读取原生配置与实时集群状态。它们不能互相冒充，也不能把
functional 结果解释成性能、稳定性或 Reliability 证明。
