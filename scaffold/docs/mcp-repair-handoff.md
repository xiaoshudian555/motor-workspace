# remote-dev MCP 修复交接

## 目标与首个动作

在 motor-workspace 修复 MCP 长任务中断、查询阻塞和结果误判，随后用实测定位
额外延迟。先读 AGENTS.md、相关 Skill、当前 dirty diff 和下面的真实代码，再执行
本地基线测试。本文是修复任务说明，不代表已实现或已授权操作集群。

保留用户未提交修改；不 commit、不 merge。不修改 Motor 部署架构，不新增通用
campaign engine，不把 SSH 脱管运行作为长期解决方案。远端验证需明确目标和授权。
本地 mock/子进程测试先行，不需要为了验证协议而启动真实模型服务。

## 已确认的问题（2026-09-07，接手时重读）

路径均相对仓库根目录：

| 优先级 | 代码位置 | 现状与修复目标 |
|---|---|---|
| P0 | scaffold/.remote-dev/mcp/tools.py `call_tool`；core/job_ops.py `start_remote_job` | 默认 120000ms 传给后台任务并转换成远端 `timeout`，把请求等待和任务寿命混在一起。分离请求等待预算与执行 deadline；明确旧参数兼容、零值、负值和默认策略，不简单改成无限运行。 |
| P0 | mcp/server.py `handle` / 消息读取循环 | 同步调用阻塞后续消息。让长任务快速返回句柄；若实现并发，保护 stdout 消息边界、ledger 和共享文件，限制并发，保证短查询可响应。 |
| P0 | core/job_ops.py；mcp/schemas.py；mcp/tools.py | 底层支持 job_id，MCP 未完整暴露；本地记录在远端启动后才落地。稳定请求身份、启动前意图记录、远端去重与重连核对必须一起设计，避免响应丢失后重复构建/部署。相同 ID 不同参数应冲突，同参数应恢复同一任务。 |
| P1 | core/shell_ops.py `_format_bash_text`；mcp/server.py | 文本统一写 completed，超时易误判。文本与结构化结果一致表达请求状态、任务状态、是否终态、下一步和日志引用。查询成功但任务失败是两个维度，不应简单把查询也判成失败。 |
| P1 | core/monitor_ops.py `remote_monitor` | pattern 只写进 description，没有条件检测。实现有界条件等待并区分匹配、截止、任务退出；若不实现，明确拒绝不支持参数，不得装作满足条件。 |
| P1 | core/job_ops.py `remote_job_tail`、endpoint reconstruction | 日志重复 tail 无游标；恢复 endpoint 时检查 identity_file 等连接信息是否保留。支持增量读取并处理截断/轮转。 |
| P2 | core/ssh_transport.py、core/context_snapshot.py | 每次操作创建 SSH 子进程；probe 导入多个重型推理包。先分段测量，再决定连接复用、轻量探测或缓存，不把业务命令耗时全算 MCP 开销。 |
| P2 | mcp/server.py、mcp/tools.py、.codex/config.example.toml | 核验协议版本协商、取消语义、资源分页和新鲜度、工具 schema；示例仍指向根目录 .remote-dev 而真实目录在 scaffold。实际 Desktop 配置曾是正确绝对路径，不能据示例断言当前客户端配置错误。 |

另：hooks/guard_common.py 当前相关检查返回 allow。它不是有效权限执法层；
不要在并发化后假设它能保护远端写入。修复权限模型如超出本次范围，单列风险和授权。

## 最小验收集

1. 长任务：短请求预算到期后，后台任务仍按独立执行 deadline 运行；显式执行
   deadline 到期可收敛并查询到终态。覆盖默认值和旧客户端参数。
2. 并发：阻塞任务进行时，另一 job_status/ping 类短请求能在测试规定的有界时间
   返回；多条响应可完整解析，无 ledger 串会话、stdout 交错或无限线程。
3. 响应丢失：模拟远端已启动、本地没收到确认；同一 ID 重试只启动一次，重启
   MCP 后仍能核对状态；同 ID 不同参数不复用。取消请求不隐式杀死独立 job。
4. 三种结果：请求超时但任务存活、请求成功但任务失败、任务成功，文本和
   structuredContent 在 Codex/Cursor 的实际消费结果均不歧义。
5. monitor：匹配命中、始终不命中、进程提前失败、日志轮转；不把超时说成匹配。
6. 增量日志：重复读取不重复输出旧内容，游标过期有明确信号，大输出内存有界。
7. 重连：保留 endpoint 身份信息但不把密钥材料写入日志；恢复后能读旧 job。

先执行 `python3 -m unittest discover -s scaffold/.remote-dev/tests` 并记录基线。
此前 72 项中有 1 项 session scope 测试失败，可能受 CODEX_SESSION_ID 优先级影响；
这是历史观察，不是当前结果。重现后区分既有失败与新增失败，不删测试绕过。

## 性能和多 Agent 验证

分别记录客户端排队、服务端排队、SSH 建连、命令执行、编码/返回耗时；先跑
空命令/小文件/状态查询，再跑有界长命令。记录样本量、环境、p50/p95、超时数。
历史操作日志包含真实业务耗时，不是 MCP 微基准。

此前样本确认 Codex Desktop 高频使用 remote.bash，长等待多、后台任务使用少；
Cursor 历史对话报告构建受 120 秒任务 deadline 影响并绕到脱管运行。历史对话是
线索，不替代原始日志。Claude/Hermes/CLI 没有足够已确认调用样本，不做快慢排名。
对可用客户端分别验证发现、启动、长任务、重连、文本/结构化消费；不可用写未覆盖。

## 接入 Skill 学习验收

沿用 AGENTS.md 的学习入口，不另造学习系统。以“请求超时误判任务失败”为
候选，保留固定工具观察，比较旧/新指引的实际决策，再加入“已明确任务失败”的
反例。另覆盖长构建和条件未命中。工具单测通过不等于 Agent 回放通过。
回放不可用就保留 pending 和下一步，不填写虚构成功。交付变更清单、测试实录、
兼容策略、性能对比和未覆盖客户端；学习记录由 learning_check.py 检查。

## HyperLoom 借鉴范围

参考 [AMD-AGI/Hyperloom](https://github.com/AMD-AGI/Hyperloom) 及其
[writeback 实现](https://github.com/AMD-AGI/Hyperloom/blob/main/src/hyperloom/orchestrator/loop/writeback.py)：
借鉴证据驱动的保留/拒绝、可恢复任务身份和验证后复用；不要声称它已经实现通用
SKILL.md 自改，也不要照搬其推理优化 campaign 架构。接手时核验最新实现。
