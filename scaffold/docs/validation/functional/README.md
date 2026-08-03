# Functional 证明目标接口和业务功能按设计工作

## 负责

- 围绕本次改动选择功能 case，而不是无目标地发送请求。
- 验证特性开关打开后的业务行为：API key、TLS、metrics、tracing、参数透传和
  典型请求组合是否按设计生效。
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

当前 catalog 先为 API key、TLS、metrics、tracing、参数透传和 overload-control
预留 case。dispatcher 只是显式的 `adapter -> handler` 字典，不引入插件注册中心或
第二套状态模型。尚未实现的 adapter 必须返回 `unavailable`，不能把“成功生成 spec”
当成“功能验证通过”。

入口：

```bash
python3 scaffold/.agents/skills/motor-functional/scripts/compile_spec.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '<用户原始描述>' \
  --feature api-key
```

## 不负责

- 给出 OpenAI 响应字段、SSE 分片、结束条件或错误语义的协议合规结论；该责任
  属于 [`../correctness/`](../correctness/)。
- 判断请求最终应该路由到哪个 Prefill、Decode 或 hybrid 实例；该责任属于
  [`../routing-topology/`](../routing-topology/)。
- 做模型级 accuracy evaluation；该责任属于
  [`../correctness/`](../correctness/)。
- 在升压曲线上寻找 overload 触发点、饱和区或压力解除后的恢复；该责任属于
  [`../stress-capacity/`](../stress-capacity/)。
- 给出性能是否退化的结论。

## 交付

`functional` validation run，包括功能 case、配置、输入输出、服务端行为证据、
通过标准和 [`../../diagnosis/`](../../diagnosis/) 引用。
