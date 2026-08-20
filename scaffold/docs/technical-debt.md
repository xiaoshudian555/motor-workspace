# 当前技术债：030b430 删稿后按代码现状重建

旧全文在 `030b430`（删除第一波 script）被整篇删除。2026-08-13 用
`git show 030b430^:scaffold/docs/technical-debt.md` 对照当前仓库重建。

判定规则：代码载体已删、且现行 Skill/上游 deployer 已覆盖的，标关闭或过时；
问题本身还在的，保留原 ID 并按**当前架构**改写（Skill + `remote.*` +
`deploy.py`，不再假设 `mws_deploy.py` / `smoke_run.py` 存在）。

## 目录

- [判定总表](#判定总表)
- [仍打开](#仍打开)
- [已关闭 / 已过时](#已关闭--已过时)
- [完成定义](#完成定义)

## 判定总表

| ID | 判定 | 依据 |
|---|---|---|
| TD-P0-03 | 关闭 | `session-management` / `mws_session_*` / `manage_machine.py` 已不存在；`endpoint.py` 拒绝 `session_id`/`session_file`/`machine` |
| TD-P1-02 | 关闭 | `machine_add.py` 等管理脚本与 `mws_result.emit` 已删 |
| TD-P1-05 | **打开** | 真实集群纵向验收仍未作为完成门槛 |
| TD-P1-06 | 关闭 | `architecture.md` + `motorws parity` 已定义 local-control / remote-native；identity 有测试。剩余真实验收并入 TD-P1-05 |
| TD-P1-07 | 并入 TD-P1-08 | Host 入口已是 `remote.*`；缺的是 MCP 真能调用 + live smoke |
| TD-P1-08 | **打开** | `.mcp.json` 存在 ≠ Agent 能调到 `remote.*` |
| TD-P1-09 | 并入 TD-P2-01 | Skill 已写走 `remote.bash`；剩余是两套 SSH 实现 |
| TD-P1-10 | 关闭 | 旧文已关；`smoke_run.py` 已删 |
| TD-P1-11 | **打开** | Cursor 不加载根目录 `.mcp.json`；本轮只补了 `.cursor/mcp.json` |
| TD-R1-01 | **打开（首版已落）** | `motor-reliability` 已定义三类 RAS 实验并接入部署 dispatcher；仍缺真实集群验收和失败场景专项诊断 |
| TD-R1-02 | **打开** | GPQA 等 correctness 尚无独立 Skill、结果契约和可复现证据链 |
| TD-R1-03 | **打开** | 通用 diagnosis 能取证，但 readiness、P/D 注册、推理路径和 RAS 恢复的专项定位覆盖不足 |
| TD-R1-04 | **打开** | 冒烟/可靠性/精度的模型、拓扑、阈值、超时和基线仍缺版本化测试 profile |
| TD-P2-01 | **打开** | `mws_transport.SshScpTransport` 与 `.remote-dev/core/ssh_transport.py` 仍各拼一套 ssh argv |
| TD-P2-02 | 关闭 | `mws_deploy.py` 已删 |
| TD-P2-03 | **打开（改写）** | `bench_plan.py` 已删；Skill 改为 agent 跑 aisbench，仍无结构化证据落盘 |
| TD-P2-05 | **打开** | README 默认 `root=/`，`endpoint.py` / `.mcp.json` 默认 `root=/mnt` |
| TD-P2-07 | **打开（遗留）** | wheel 主路径已在 `motor-build-wheel` + `boot.sh`；Rust toolchain 固化未做 |
| TD-P2-08 | **打开** | 旧文第二节误标成第二个 TD-P2-07。镜像检查 skill 只报不补 |
| TD-A3-01 | 关闭 | workspace 私有 `namespace` 随 `mws_deploy.py` 删除；现行 `job_id` 即 namespace |
| TD-A3-02 | 关闭 | 旧文已关 |
| TD-A3-03 | 关闭 | `environment-contract.yaml` 已恢复到 skill `references/`；one-of 仍在 yaml。死配置 a2-dev.yaml 见 TD-A3-07 |
| TD-A3-04 | **打开（改写）** | yaml 已恢复；Python 仍不恢复。Agent 读 yaml 再 kubectl。Apply 前镜像/容量仍无脚本级 fail-closed |
| TD-A3-05 | **打开（回退）** | 自动避让代码已随 preflight 脚本删除；现行 Skill 只报告、等授权再改 |
| TD-A3-06 | 关闭 | `motor-k8s-deploy` 主路径已是 upstream `delete.sh` |
| TD-A3-07 | **打开** | 周边项部分仍在 |
| TD-A3-08 | 关闭 | `scaffold/tests` 现仅 parity 相关 3 个文件，过重问题已消失 |
| TD-A3-09 | 关闭 | 旧文已完成；`mws_build.py` 已删，Skill 直接改 `boot.sh` |
| TD-A3-10 | 关闭 | scaffold 无 `PYTHONPATH` 注入；configure/deploy Skill 明确禁止源码 PYTHONPATH |
| TD-A3-11 | 关闭 | `deploy_apply.py` 已删；checklist 规定 Coordinator `/readiness` 才算过，Pod Ready 不能替代 |
| TD-A3-12 | **打开** | local-control 下 SSH + `kubectl port-forward` 仍未验收通过 |

旧 2026-07-31 关闭表（TD-P0-01/02、TD-P1-01/03/04、TD-P2-04/06 等）不再展开，仍视为已关闭。

---

## 仍打开

### TD-P1-05：缺少真实集群纵向验收

当前架构已改成：inventory → `remote.*` / `kubectl` 只读检查 → `deploy.py --dry-run`
→ 授权 → `deploy.py` → Ready / Service / Coordinator `/readiness`。`cluster-acceptance-checklist.md`
写了通过标准，但没有一次按该清单走完、可引用的真实集群记录。

验收：在真实 K8s/MindCluster/Ascend 上按 checklist 跑通只读 → 授权覆盖 → apply
→ smoke；fixture 不能替代。apply / parity 覆盖 / stop 仍须逐次授权。

### TD-P1-08：MCP 工具在 Codex / Claude / Windows/WSL 上尚未形成可验证入口

仓库有 `.mcp.json` 和 `scaffold/.remote-dev/mcp/server.py`。配置文件存在不等于
当前 Agent 会话能调用 `remote.read` / `remote.bash` / `remote.probe`。

目标：Codex、Claude Code 启动后能 `tools/list` 看到 `remote.*`；WSL → Linux Host
有最小 live smoke。`remote.*` 未注册时 fail-closed，给出修复步骤，不静默改手搓 SSH。

Cursor 特例见 TD-P1-11。

### TD-R1-01：motor-reliability 首版已落，尚未完成真实集群闭环

2026-08-20 已新增 `motor-reliability` Skill，覆盖三个显式授权场景：

- Coordinator active/standby 主备切换；
- Decode engine-server 进程强杀后的重拉和重新注册；
- Prefill NPU 参数面 link 故障后的实例隔离、冗余补齐和物理链路恢复。

Skill 已要求精确目标、注入前基线、一次性授权、轮询代替固定 sleep、失败先取证、
以及 linkdown 的强制物理恢复。当前仍缺：

- 在真实 Ascend/MindCluster 集群逐场景验收，确认各版本的进程名、日志、状态转换和
  `hccn_tool` 查询/恢复语法；
- `motor-deploy` dispatcher 已补 reliability 路由；仍需保持其 live authoring source、
  tracked mirror 和 Claude shims 在后续修改中同步；
- Coordinator failover、Decode 重拉和 P link 隔离失败后的专项 diagnosis Skill；
- 连续请求探针的低负载、错误预算和最长中断统计契约。

验收：三个场景各至少一次真实 PASS 和一次受控 FAIL；FAIL 时证据足以区分症状、
近因和根因；任何超时/失败路径都完成目标恢复，或明确报告 `RESTORATION FAILED` 并
转人工处理。

### TD-R1-02：correctness / GPQA 缺独立执行与归因契约

旧 Pymotor 用例会就地修改 AISBench 接口文件，运行 GPQA 后用写死的 0.807 基线
判断 accuracy。workspace 目前没有 `motor-correctness`，不能保证数据集版本、prompt、
chat template、thinking 参数、模型/tokenizer、评测器和原始产物一致，也无法可靠区分
模型回归与评测环境问题。

目标：使用 run-scoped 可变目录，保存版本指纹、精确生成/评测参数、数据集校验和、
逐题原始结果及聚合指标；只有可比条件一致时才应用版本化基线。精度失败先证明评测
有效，再进入专门归因；不能路由成性能问题或 generic smoke 通过。

### TD-R1-03：冒烟与 RAS 的专项诊断覆盖不足

`motor-diagnosis` 已能收集 Pod、Event、describe、current/previous logs 和 deployer
auto-log；现有专项只覆盖 precision auto-recovery terminate。仍缺以下决策树：

- workload 启动失败和 Coordinator `/readiness` 长期不收敛；
- P/D 实例注册、拓扑计数和 Controller/Coordinator 状态不同步；
- inference connect、timeout、5xx 和流式响应未完成；
- Coordinator 主备未切换、Decode engine 未重拉、P link 故障未隔离或冗余未补齐。

目标：每个专项消费通用 diagnosis 的只读证据，按时间、Pod UID、instance/job ID 和
请求 ID 关联，输出阶段、证据链、根因分类、可信度与最小下一步。不因单条错误日志
直接建议修复或代码修改。

### TD-R1-04：缺少版本化测试 profile

旧用例把端口、模型参数、拓扑、等待时间、GPQA 基线、GSM8K 并发/速率和吞吐阈值
写死在用例中。Agent 当前可以要求用户确认，但没有统一、可审查、可版本化的 profile，
容易把不兼容的模型、硬件、软件 revision 或 workload 结果直接比较。

目标：定义只包含测试输入和判据的 profile schema，不复制 Motor 原生部署配置；至少
覆盖模型/tokenizer、硬件和 P/D/TP/DP/EP 拓扑、case 列表、deadline/error budget、
数据集和生成参数、benchmark workload 与版本化基线。profile 不能隐含部署、故障注入
或配置修改授权，也不能变成新的 deploy bundle/run gate。

### TD-P1-11：Cursor 不加载根目录 `.mcp.json`，agent 连远程会改走 paramiko / sshpass

**现状**

- 标准通道：inventory 的 `host`/`port` + 系统 ssh（公钥、`BatchMode`），或 `remote.*`。
  仓库不使用、也不该安装 `paramiko` / `sshpass`。
- Claude Code 认 `.mcp.json`。Cursor 当前会话只看到 `cursor-ide-browser`。
- Agent 没有 `remote.*` 时手搓 SSH；免密失败后找 `sshpass`，再 `pip install paramiko`。

**本轮已做（2026-08-13）**

- 新增 `.cursor/mcp.json`，与根目录 `.mcp.json` 对齐。
- 本地验证：MCP server `initialize` + `tools/list` 成功，含 `remote.probe` /
  `remote.bash` / `remote.read`。

**未做**

- Cursor Settings → MCP 批准/启用 `remote-dev`（文件落地 ≠ 本会话已加载）。
- `AGENTS.md` / `remote-toolbox` fail-closed：禁止 paramiko / sshpass / expect /
  pexpect，禁止为连机器 `pip install`；`BatchMode` 失败只报公钥不通。
- 密码机（如 `10.218.4.2`）人工写公钥；agent 不自建密码通道。
- inventory alias 同步到 `.remote-dev/endpoints.json`。
- MCP 未挂上时不要禁止本机 `ssh`/`scp`，否则会更快逼去 paramiko。

验收：Cursor MCP 列表出现可用的 `remote-dev`；新会话能调 `remote.probe`，不必装
SSH Python 库。

### TD-P2-01：两套 SSH/传输实现未完全收敛

- `.remote-dev/core/ssh_transport.py`：MCP `remote.*` 使用，含 `identity_file`、
  `timeout_ms`、`run_remote_python`。
- `scaffold/.agents/lib/mws_transport.py` 的 `SshScpTransport`：`motorws parity`
  使用，另拼 ssh/scp argv，带重试。

目标：`mws_transport` 薄适配 `.remote-dev` Endpoint，不再维持第二套 ssh argv。
迁移前补等价契约测试。不与功能改动并行。

### TD-P2-03：motor-benchmark 无结构化证据落盘

`bench_plan.py` 已删。现行 `motor-benchmark` Skill 让 agent 用 `remote.bash` 跑
aisbench，不自动安装。仍没有参数/原始结果/聚合指标的统一落盘格式。

目标：一次正式 workload 留下命令、配置、原始输出、成功/失败请求数、QPS/
TTFT/TPOT。不属于第一版部署门禁。

### TD-P2-05：`.remote-dev` 默认 `root` 文档与代码不一致

- `scaffold/.remote-dev/README.md`：默认 `root=/`
- `core/endpoint.py`、`.mcp.json`、`.cursor/mcp.json`：默认 `root=/mnt`

目标：DESIGN / README / VALIDATION / MCP schema / `.mcp.json` 对 direct endpoint
的默认 root/cwd 一致。`session_id` 已在代码层拒绝，不必再清 Docker session 实现。

### TD-P2-07：Motor wheel 构建路径的 Rust toolchain 未固化

主路径已在：`motor-build-wheel` Skill 在运行时镜像里跑上游 `build.sh`，产出
wheel 后改远端 `boot.sh`。`mws_build.py` 已不存在。

遗留：kv-conductor cargo 若需特定 Rust 版本，应在 build 容器内固化 toolchain
并写入产物元数据。日常 Python 迭代禁止源码 `PYTHONPATH`。

### TD-P2-08：跨节点镜像分发缺少补 load / 自动分发

旧文第二节误标为第二个「TD-P2-07」。`motor-image-distribution-check` Skill 仍
写 TD-P2-07，应以本条为准。

现状：Skill 只做只读检查（临时 DaemonSet 扫节点本地 runtime）。缺失后仍靠人工
`docker save` / `docker load`。`motor-deploy-preflight` 也只要求 agent 报告覆盖风险。

目标（按优先级）：内部 registry + 带前缀镜像引用，让 K8s 自拉；过渡期经授权做
save/load 分发。全程不依赖节点间 SSH 互信。

### TD-A3-04：部署前验证由 agent 读契约后 kubectl，apply 前无脚本级 fail-closed

2026-08-13：已从 `030b430^` 恢复
`scaffold/.agents/skills/motor-deploy-preflight/references/environment-contract.yaml`。
`environment_preflight.py` 仍不恢复。现行 Skill：先读 yaml，按 `deploy_mode`
查 CRD / controller / 默认 NodePort，再由 agent 跑只读 kubectl；契约硬性项
缺失 fail-closed，禁止猜 CRD 名。不写 run 记录。

仍缺：镜像缺失且不可拉时 apply 前的硬门禁、NPU 容量未验证的显式阻断——仍靠
agent 报告，没有脚本/run-gate。

目标：镜像缺失且不可拉时 apply 前 fail-closed；关键组件非 Ready 要标 error；
NPU 容量未验证须在报告里显式标记，不得静默当通过。

### TD-A3-05：NodePort 自动避让已随脚本删除

曾落地的范围校验 / 集群占用探测 / 写回 `user_config.json` 已随
`environment_preflight.py` 删除。现行 Skill：冲突只报告并给空闲候选，等授权再改。

目标：探测占用、范围内唯一、无空闲才 fail-closed；改配置必须用户同意。不恢复
未授权自动写回。

### TD-A3-07：环境与依赖治理残留

仍有效：

- 顶层 `.gitmodules` 的 motor 跟 `branch = master`，不是旧债里的 pinned commit；
  发布前要有可获取的版本映射，不能把 master 静默当 pin。
- `scaffold/profiles/a2-dev.yaml` 的 `mindcluster.required_api_resources` 仍写
  `ascendjobs`，preflight 已不读该段，属死配置。

过时（不再作为打开项）：本机缺 PyYAML——configure 已改为远端跑 `deploy.py --dry-run`。

`log_monitor.py` 的 Python 3.9 `X | None` 语法是否仍炸远端，未在本轮复验，保持待确认。

### TD-A3-12：local-control smoke 的 port-forward 通道仍不可靠

`motor-smoke` 仍允许「远端宿主机直连」或临时 `kubectl port-forward`。
remote-native 直连 ClusterIP 在旧债里已验证；WSL/Windows 控制机 + SSH master
的 port-forward 在 B132 上出现过 `WinError 10061` / `RemoteDisconnected`，之后
没有新的通过记录。

目标：local-control 下标准 smoke 拿到 Coordinator `GET /readiness` 且
`ready=true`；失败时要有 forward 进程日志。可选：在 master 上 curl ClusterIP，
不走隧道。

---

## 已关闭 / 已过时

| ID | 关闭原因 |
|---|---|
| TD-P0-03 | VAWS session/container 代码与 skill 已不在默认树；selector 被拒绝 |
| TD-P1-02 | legacy `emit` 脚本已删 |
| TD-P1-06 | 两种拓扑已写入 architecture 与 parity identity |
| TD-P1-10 | 旧文关闭；smoke 脚本已删 |
| TD-P2-02 | `mws_deploy.py` 已删 |
| TD-A3-01 / 02 | workspace `namespace` 字段已不存在 |
| TD-A3-03 | 契约 yaml 已删；preflight 改为 kubectl 目视 |
| TD-A3-06 | stop 走 `delete.sh` |
| TD-A3-08 | 测试集已随脚本大幅缩小 |
| TD-A3-09 / 10 / 11 | wheel/`boot.sh`、禁止 PYTHONPATH、readiness 归属 smoke，均已在 Skill/checklist 落地 |

`job-summary-*` ConfigMap 是否被 `delete.sh` 清掉，旧债标过未复验；有真实残留再单开，不预置为打开项。

---

## 完成定义

单项技术债只有同时满足才可关闭：

- 生产路径已改，不是只改文档或 fixture
- 失败路径 fail-closed，不会把未就绪标成成功
- 相关 Skill、文档与当前实现一致
- 涉及真实环境的条目须区分「本地验证」和「真实环境验收」
