# 当前技术债

本文只记录截至 2026-07-31 仍未解决、能够落实到代码或验收动作的技术债。
已完成事项、历史实施过程、冻结设计全文和旧 Agent 工作包不在本文保留。

## 基线与范围

当前本地基线（2026-07-31 修复后实际执行结果）：

- `scaffold/tests`：**141 passed**，0 warning；
- `scaffold/.remote-dev/tests`：**73 passed / 44 subtests passed**；
- 第二部分 preflight、configure、apply、Ready 与 runtime source proof 均有实现与
  fixture，但尚未完成真实集群验收（见 TD-P1-05）。

上述数字早于提交 `3db99cd`（`修复部分技术债`）。该提交新增或迁入 VAWS
remote toolbox、managed session、container bootstrap 和 container 版 parity
等大量代码；本文没有把旧测试数字当作这批迁移后的新基线。完成 TD-P0-03
边界收口后必须重新执行本地契约测试和真实 direct-host 验收。

以下约束已经确认，不再作为待讨论项：

- `workspace-ready` 是审计和编排证据，不是 `machine-ready`、parity 或部署的硬门禁；
- workspace 不提供 namespace、queue、scheduler、image、model、NPU 等第二套部署字段，
  部署配置只来自 Motor 原生 `user_config.json` 和 `env.json`；
- 真实集群验收在本地 fixture 全绿并经人工 review 后进行，由仓库所有者提供环境；
- 远端固定源码目录覆盖以及 Kubernetes apply、restart、stop 必须逐次取得明确授权。

## 2026-07-31 修复记录（本轮已关闭）

| 条目 | 修复内容 |
|------|----------|
| 旧 P0-01~03、旧 P1-01~03 | 代码早已修复；文档过时，已从待办移除 |
| TD-P0-01 | `deploy_restart` 从 deploy run 取 `machine_run_id` 并传给 parity 子进程 |
| TD-P0-02 | `machine_verify` 用 `inventory_lock()` 原子更新 inventory |
| TD-P1-01 | 重新生成 Claude skill shim（含 `motor-diagnosis`） |
| TD-P1-02（部分） | 关键链路脚本迁到 `mws.result.v1`：`parity_sync`、`deploy_restart`、`deploy_status`、`deploy_stop` |
| TD-P1-03 | 删除 `PARITY_RUNS_DIR`/`DEPLOY_RUNS_DIR` 别名、`SESSIONS_DIR*` 死代码；`load_deploy_run` 显式语义；JSON 写收敛到 `mws_state` |
| TD-P1-04 | `validate_remote_posix_path` 拒绝 shell 元字符 |
| TD-P2-04 | `tarfile.extractall(..., filter="data")`，warning 归零 |
| TD-P2-06 | `.gitmodules` vllm/vllm-ascend 改 HTTPS 并 `git submodule sync` |
| `.remote-dev` 测试 | 补全 Endpoint `root`/`cwd`，修复默认 `/mnt` 与旧 `/vllm-workspace` 路径不一致导致的 10 项失败 |
| TD-P2-01（部分） | `mws_transport.SshScpTransport` 增加 `ConnectTimeout=10` |
| TD-P2-03（部分） | `bench_plan.py` 迁 envelope，校验 ready deploy run；workload 执行仍为 scaffold |

---

## P0：VAWS remote 迁移边界必须先收口

### TD-P0-03：VAWS direct-host remote 能力与 Docker session 专属能力混入同一实现

现状：

- Motor 的目标执行面是 K8s login/master Host、共享 `/mnt` 和 Motor Pods；
  默认开发链路不是 VAWS 的“每个任务创建一个 Docker container”。
- 当前代码同时存在两套互相冲突的语义：
  - `AGENTS.md`、`architecture.md` 和现有 Motor workflow 采用
    direct endpoint + 固定 `/mnt/motor-workspace`；
  - `session-management`、`mws_session_*`、`manage_machine.py`、
    `mws_remote_toolbox.py` 及部分 `.remote-dev` selector 又恢复了
    local worktree + remote container + SSH/service port/NPU lease。
- `remote-code-parity/SKILL.md` 定义的是同步到固定共享目录的 Motor parity；
  同包新增的 VAWS 能力中，git 对象增量（synthetic snapshot/bundle/mirror/
  materialize）已按本轮决策复用到 Motor parity，container cache、
  install/editable install 和 first-install consent 仍属 Docker session 专属，
  不进入默认链路。
- `.remote-dev/README.md` 声明 direct endpoint 默认 `root=/`，而
  `.mcp.json` 与 `core/endpoint.py` 默认仍为 `root=/mnt`；Host 文件访问范围
  和安全边界没有统一。
- `session-management` 已生成 Claude shim，但根 `AGENTS.md` 不把它列为
  Motor 默认 skill；Agent 可能因为发现到该 skill 而进入错误的 Docker 路径。

VAWS remote 迁移处理矩阵：

| 能力/文件组 | 结论 | Motor 处理方向 |
|---|---|---|
| `.remote-dev/core/ssh_transport.py` | 保留 | 作为唯一 SSH transport；支持 `identity_file`、timeout、stdin 脚本和 remote Python |
| `.remote-dev/core/file_ops.py`、`search_ops.py`、`patch_ops.py` | 保留 | 复用远端 read/write/edit/ls/glob/grep/apply_patch、原子写、SHA read ledger 和 path policy |
| `.remote-dev/core/shell_ops.py`、`job_ops.py`、`monitor_ops.py` | 保留并适配 | 直接在 K8s Host 执行；runtime env 改为可选的 Motor Host 环境，不依赖 VAWS container |
| `.remote-dev/core/artifact_ops.py`、context/result/state | 保留 | 用于 Host/Pod 日志、部署证据、benchmark/profiling 产物和可恢复长任务 |
| `.remote-dev/mcp/`、CLI fallback、Codex/Claude 接入与 direct-endpoint tests | 保留 | 让 Agent 获得一等 `remote.*` 工具；补 native Windows/WSL 和真实 Host 验收 |
| machine inventory 的原子写、锁、SSH endpoint、kube context、`mount_root` | 保留 | 服务 Motor direct Host，不记录或创建 managed container |
| `mws_transport`、machine → Endpoint adapter | 保留但收薄 | 只做 Motor machine record → direct endpoint；不得形成第二套 SSH 实现 |
| remote probe/exec/job/artifact wrapper | 选择性保留 | 仅在 skill 需要稳定 JSON CLI 时保留；内部必须复用 `.remote-dev`，不重复 transport |
| Motor 固定目录 parity 的 dirty tree 采集、摘要、锁、fast path、post-sync proof | 保留 | 继续由 `mws_parity.py` + `parity_sync.py` 负责 |
| `scaffold/.agents/skills/session-management/**` 及 Claude shim | 删除或隔离 | 纯 VAWS managed Docker session；不进入 Motor 默认发现、路由和验收 |
| `mws_session_id.py`、`mws_session_state.py`、session index/current-session/lease | 删除或隔离 | Motor 当前没有 session contract；parity/deploy run 不能借用 VAWS session 语义 |
| `manage_machine.py`、`_workflow_common.py` 中的 container create/reuse、container sshd、prepared image cache | 删除 | Motor 登记现有 K8s Host，不负责创建 Docker container 或提交 prepared image |
| container SSH port、service port、NPU device lease | 删除 | K8s 资源由 namespace/job、scheduler、device plugin 和 Motor 原生配置管理 |
| `remote_service_start/status/logs/stop` 的 vLLM container service adapter | 删除或重写 | Motor 服务生命周期只由 `motor-k8s-deploy`/upstream deployer 管理 |
| `remote_cleanup` 中的 container/session/lease cleanup | 删除或重写 | 仅保留明确的 Host temp/job/artifact cleanup；K8s 删除/停止必须走 Motor workflow 和 consent |
| VAWS `remote_code_parity.py` 的 synthetic snapshot / bundle / mirror / materialize | 保留并适配 | 复用为 git 对象增量 parity（`mws_parity` 已落地：temp-index synthetic snapshot → `git bundle` → bare mirror → worktree `checkout -f -B` + `reset --hard` + `clean -ffd`），输出仍是固定 shared-hostPath 目录 |
| VAWS 的 container mirror/cache（Docker image 层）、runtime install/editable install、image package replacement | 删除 | 属 Docker session 专属能力，与固定 hostPath + `PYTHONPATH` 运行模型冲突 |
| `install_consent.py` 的 image package replacement、editable install、container marker | 删除 | Motor daily Python loop 使用 hostPath + `PYTHONPATH`；ABI/install 走明确 bootstrap Job 或 image bypass |
| managed `session_id`/`session_file` endpoint selector | 从通用 `.remote-dev` 移除 | direct `host+port`/alias 是通用层；Motor machine 解析在 `.agents/lib` adapter 完成 |
| `machine` endpoint selector | 不进入通用 core | 如需保留便利入口，只能作为 Motor adapter，解析后仍传 direct endpoint |

不在本条技术债范围：

- `repo-init` 中确实通用的 Git、`gh`、fork、submodule、原子状态能力；
- Motor 原生 deploy、benchmark、diagnosis 逻辑；
- 未来是否需要多 Agent 并行隔离。若以后需要，应基于 K8s namespace/job、
  shared mount 与源码隔离重新设计，不能默认恢复 VAWS Docker session。

目标：

1. 按上表给当前迁移文件逐项标记 `keep`、`adapt`、`remove`，处理者执行删除或
   收口；本文只记录边界，不代替代码处理。
2. 根 `AGENTS.md`、skill catalog、Claude shim、`.remote-dev` schema、CLI help
   和测试只暴露 Motor 实际支持的 direct-host 路径。
3. direct remote 原子操作只有一套 transport、path policy、result 和 job/artifact
   状态实现。
4. 固定目录 Motor parity 采用 git 对象增量（synthetic snapshot → bundle →
   bare mirror → worktree materialize），但只输出固定 shared-hostPath 目录；
   默认链路不得再出现 session、container cache、editable install 或 image
   package replacement。
5. 清理完成后重新建立测试基线；不得引用 VAWS 自身的 container/session
   validation 证明 Motor direct-host 已完成。

验收：

- 从全仓搜索 `session_id`、`session_file`、`current-session`、`container_ssh_port`、
  `prepared image`、`npu lease`，保留项都有明确的非 Docker
  理由，否则从 Motor 默认实现移除。
- `remote.*` direct endpoint 的 read/edit/bash/search/patch/job/artifact
  local contract tests 全部通过。
- 使用一台真实 K8s login/master Host 完成 direct endpoint live smoke；
  全程不创建 Docker container，不分配 container/service port/NPU lease。
- Motor parity 远端目标只出现固定
  `/mnt/motor-workspace/{motor,vllm,vllm-ascend,python-overlay}`。
- Agent discovery 中不再出现无法完成 Motor workflow 的 session/container skill。

---

## P1：本地工作流收口

### TD-P1-02（剩余）：管理类脚本仍用 legacy `emit`

现状：

- 已迁 envelope：verify/repair/preflight/configure/apply/diagnosis/repo-init/parity/restart/status/stop；
- 仍用 legacy `emit({status:…})`：`inventory`、`machine_add`、`machine_remove`、
  `repo_topology`、`deploy_plan`（占位重定向）、`_repo_init_common`；
- `mws_result.emit()` 仍保留 `legacy=True` 分支。

目标：

- 管理类脚本可保留 legacy，但须在 `mws_result.py` docstring 写清边界；
- 删除 `legacy=True` 分支前，确认无脚本再输出 `{status: ok|error}` 非 envelope JSON；
- 每个迁移脚本补 stdout schema fixture。

### TD-P1-05：缺少真实集群纵向验收

前置条件：

- 本地 fixture 全绿；
- diff 经人工 review；
- 仓库所有者提供真实 K8s/MindCluster/Ascend 环境。

验收顺序：

```text
machine-management verify
→ remote-code-parity
→ motor-deploy-preflight
→ motor-deploy-configure
→ motor-k8s-deploy (apply)
→ (代码热更) motor-k8s-deploy restart
```

验收要求：

- 先完成只读 probe/preflight；
- 覆盖固定远端源码目录前单独请求授权；
- apply、restart、stop 分别单独请求授权；
- 保存 environment/config/deploy run 和 bundle 证据；
- 只有关键资源 Ready、最小服务可访问、Pod 内三个包加载路径与 parity 固定路径一致，
  才能声明 `deploy-complete`；
- fixture 通过不得替代真实环境结论。

### TD-P1-06：未定义 remote-native 与 local-control 两种 Agent 拓扑的统一契约

现状：

- 当前文档只定义“本地 workspace → parity → 远端固定目录”，没有把 Agent
  已经运行在远端 Linux Host、源码已经位于 `/mnt/motor-workspace` 的场景作为
  一等路径。
- 对 native Windows/WSL 上的本地 Agent，也没有明确规定哪些操作必须走
  `remote.*`、何时自动 parity、何时允许使用本地工具。
- source of truth、execution Host 和 Pod runtime 三者仍容易被混为一谈。

目标：

Motor 支持两种拓扑，但共享同一套下游 deploy/validation workflow：

```text
remote-native
  Agent + workspace 位于 Linux Host 的 /mnt/motor-workspace
  → parity 产出 no-op/identity proof
  → configure/deploy/validation

local-control
  Agent + workspace 位于本地 Windows/WSL/Linux
  → remote-code-parity 同步到 /mnt/motor-workspace
  → configure/deploy/validation
```

- 不新增第二套 Motor deploy 配置；
- 不要求用户为 Python 日常修改 commit/push；
- downstream 只消费统一的 source/parity evidence，不关心 Agent 物理位置；
- remote-native 的 no-op 也必须证明 source root、machine 和固定共享路径一致，
  不能无证据跳过。

验收：

- 两种拓扑各有一条端到端 contract fixture；
- 同一个 configure/deploy consumer 能消费 sync parity 或 remote-native identity
  proof；
- 模式切换不会把本地代码误覆盖到错误机器，也不会把远端代码误判为本地代码；
- 文档明确哪些工具在 Agent 所在 Host 原生执行，哪些通过 direct endpoint 执行。

### TD-P1-07：K8s login/master Host 尚未成为一等 remote execution target

现状：

- machine inventory 能记录 SSH endpoint、kube context、`mount_root`，但
  Host read/edit/bash/search/job/artifact 能力尚未作为 Motor workflow 的稳定
  公共入口验收。
- `motor-deploy-*` 关注 K8s API 与 Pod 生命周期，但 Host 侧的共享盘、日志、
  网络、CANN/NPU、进程、systemd、container runtime 和长任务诊断没有统一路由。
- 当前 `.remote-dev` 的 `root` 语义不统一；同时要注意 `root` 只限制文件工具和
  `remote.bash` 的 cwd，不是 arbitrary shell command 的安全沙箱。

目标：

- 将 K8s login/master Host 定义为 direct endpoint；至少记录
  `host/port/user/identity_ref/root/cwd/kube_context_ref/mount_root`。
- 区分三个目标：
  - Host：SSH、共享盘、系统日志、网络、NPU/CANN、`kubectl`、长任务；
  - K8s API：preflight/configure/apply/status；
  - Pod：runtime `__file__`、服务日志、smoke、benchmark、profiling。
- 提供最少两类权限配置：
  - workspace endpoint：非 root，文件范围限制在 `/mnt/motor-workspace`；
  - host-admin endpoint：最小 sudo/受控 kubeconfig，仅在明确授权的 Host/K8s
    mutation 中使用。
- Host mutation（安装、覆盖系统文件、systemctl、清理、K8s apply/restart/stop）
  与只读 probe 分开 consent。

验收：

- direct-host live smoke 覆盖 read/edit/bash/grep/job/artifact；
- `kubectl`、Host 日志和 `/mnt` 操作均能通过 Agent 工具完成，不依赖 raw SSH；
- 非特权 endpoint 无法执行未授权的 Host/K8s mutation；
- Host、K8s API、Pod 三层失败分别保存证据，不互相冒充完成结果。

### TD-P1-08：MCP 工具在 Codex/Claude 与 Windows/WSL 上尚未形成可验证入口

现状：

- 仓库已有 `.mcp.json` 和 remote-dev server，但当前 Codex 会话未实际暴露
  `remote.read/edit/bash/...` 工具，说明“配置文件存在”不等于 Agent 可调用。
- VAWS 有 direct-endpoint/Linux 侧验证和 Windows `py -3` launcher 说明，但没有
  足够证据证明 Motor 在 native Windows → Linux Host 的 MCP、OpenSSH、UTF-8、
  路径、identity file 和长任务链路已经端到端稳定。

目标：

- 给 Codex、Claude Code 分别提供可执行、可检查的 MCP 配置与安装说明；
- 明确 native Windows 使用 `py -3` 还是强制推荐 WSL，不能让 `python3`
  配置在 Windows 上静默失败；
- Agent 启动后执行 tools/list 自检，并用 direct Host 跑最小 live smoke；
- remote tool 未注册时 fail closed，给出修复命令，不静默退化为 Agent 手拼 SSH；
- 统一 UTF-8、CRLF、Windows identity path、OpenSSH 发现和 timeout 行为。

验收：

- Codex 与 Claude Code 都能看到并调用预期 remote tools；
- WSL → Linux Host 全链路通过；
- 若宣称支持 native Windows，必须有真实 native Windows → Linux Host 验收记录；
- 覆盖含空格路径、中文、CRLF、大输出、timeout、断连、并发 edit 和 artifact
  hash 校验。

### TD-P1-09：Motor workflow 尚未统一消费 `.remote-dev` direct-host 原子能力

现状：

- `.remote-dev`、`mws_transport`、`mws_remote_toolbox` 和各 skill wrapper
  存在职责重叠；
- 部分 workflow 仍可能自行构造 SSH/命令或依赖兼容 wrapper；
- remote toolbox 中混有 target、exec、job、sync、service、cleanup 多种业务语义。

目标：

- `.remote-dev` 只负责通用 direct-host 原子操作；
- `.agents/lib` 负责 Motor machine → Endpoint、consent、run/result 和业务编排；
- skill wrapper 只做参数解析、进度输出和最终 JSON，不再实现 transport；
- deploy、diagnosis、benchmark 优先复用统一 job/artifact/log 能力；
- raw SSH 只保留为底层 transport 实现和明确的 emergency diagnosis，不成为
  Agent-facing 主路径。

验收：

- transport contract test 证明所有上层走同一 SSH 实现；
- 代码搜索不存在第二套未说明的 ssh argv、scp/sftp/rsync 或后台 job registry；
- 任一远端失败都能返回统一 target、outcome、status、logs/refs 和建议动作；
- TD-P2-01 在本条完成后关闭。

---

### TD-P1-10：smoke 在 remote-native 拓扑下需按 executor 分叉直接访问 ClusterIP

现状：

- `smoke_run.py` 无条件走 `PortForward`（`RemoteKubectlPortForward`）。在
  local-control（SSH）拓扑下这是必须的：Agent 不在集群网络，ClusterIP 不可达，
  必须「SSH 到 master + 远端 kubectl port-forward + 隧道拉回本地」。
- 在 remote-native 拓扑（Agent 就在 master 宿主机上）下，kube-proxy 的 iptables
  规则就在本机，直接 `curl ClusterIP:port` 即可（实测
  `curl http://10.107.213.17:1026/readiness` → HTTP 200）。port-forward 依赖
  kubelet 侧 `socat`，宿主机缺该二进制时这条路径就是断的。
- 远端 agent 验证已确认：native transport 路由、identity parity、native dry-run、
  native port-forward 代码路径均正确，唯一没适配的是 smoke 后置链路。

本轮的修复（已落地）：

- `mws_smoke.py::request_json` 增加 `host` 参数（默认 `127.0.0.1`，local-control
  行为不变），TLS 场景天然支持（连接地址与 SNI server name 分离）。
- `mws_smoke.py::discover_coordinator_services` 返回每个 role 的 `cluster_ip`
  （从 service `spec.clusterIP` 取；headless/空则 native 下 fail-closed）。
- `mws_smoke.py::resolve_validation_context` 增加 `executor` 字段（从 machine
  record 读，缺省 `ssh`）。
- `smoke_run.py` 按 `executor == "native"` 分叉：native 直接
  `request_json(host=cluster_ip, port=mgmt_port, path="/readiness")`，不进
  `with PortForward`；ssh 走原逻辑。
- 测试：`test_smoke.py` 新增 native 直连分支（断言 host=ClusterIP、不进入
  port-forward），ssh 分支回归通过。

仍保留的技术债（本轮不封闭，属 R1 范畴）：

- 该分叉是「在消费点分叉」，不是统一的 `ServiceAccess` adapter。R1 做
  `ExecutionAdapter` 时，native 直连逻辑应收敛为 `DirectServiceAccess`（或
  `NativeExecutionAdapter.port_forward` 的直连实现），消除消费点拓扑分支。
- `discover_coordinator_services` 只暴露 `clusterIP`，未暴露 NodePort/ExternalIP
  等其它访问形态；若未来需要多形态访问需扩展。

目标：

- remote-native 下 smoke 直接访问 ClusterIP，不依赖 `socat` / port-forward；
- local-control 行为零变化；
- R1 时把消费点分叉收敛为统一访问 adapter。

验收：

- native 机器上 smoke 走直连分支并拿到 `/readiness` ready=true（真实环境）；
- local-control 机器 smoke 仍走 port-forward（现有 fixture 回归）。

R1 关闭记录（2026-08-03）：

- `mws_execution.py` 引入 `PortForwardHandle`（`target_host`/`local_port`/`log`/
  `close`）与 `ExecutionAdapter.port_forward`/`host_port_forward` 抽象；
  `NativeExecutionAdapter` 对带 `cluster_ip` 的 `ServiceTarget` 返回直连 handle
  （`_NativeClusterIPAccess`），SSH 返回远端 listener + tunnel handle。
- `smoke_run.py` / `functional_run.py` 消费点统一改为
  `adapter.port_forward(ServiceTarget(...))` + `request_json(host=handle.target_host,
  port=handle.local_port, ...)`，删除 `executor == "native"` 消费点分叉。
- 本技术债条目关闭；遗留项仅剩「多形态访问（NodePort/ExternalIP）暂未暴露」。

---

## P2：不阻塞第一版的维护性和后续能力

### TD-P2-01（剩余）：两套 SSH/传输实现未完全收敛

现状：

- 已加 `ConnectTimeout=10` 到 `mws_transport`；
- `.remote-dev/core/ssh_transport.py` 仍更完整（`identity_file`、`timeout_ms`、
  `run_remote_python`）；
- 两套各自构造 ssh argv，错误语义未统一。

目标：

- `mws_transport` 薄适配 `.remote-dev/core/ssh_transport`（machine dict → Endpoint）；
- 迁移前补等价契约测试；不与 P1 功能修改并行。
- 最终由 TD-P1-09 统一关闭本项。

### TD-P2-02：`mws_deploy.py` 责任过多（1224 行）

现状：

- 混合 profile、YAML 注入、upstream dry-run、legacy plan、config bundle、kubectl
  apply/readiness、runtime proof；
- legacy `render_plan`/`load_plan_from_dir`/`apply_from_plan` 仍存在，新链经
  `configure_deploy_bundle` + `apply_config_bundle`。

目标：

- 按 config/bundle、kubectl 操作、runtime proof、legacy plan 拆分模块；
- 明确 legacy plan 函数存留边界（`bundle_to_plan` 复用的留下，纯旧 plan 删除）；
- 先加边界测试再机械拆分。

### TD-P2-03（剩余）：motor-benchmark workload 未实现

现状：

- `bench_plan.py` 已迁 envelope，校验 ready deploy run + machine 匹配；
- 无真实 benchmark 请求、指标采集或 benchmark run 落盘。

目标：

- 执行明确版本的 benchmark workload；
- 保存参数、环境、原始结果、聚合指标和失败证据；
- 不属于第一版真实部署验收门禁。

### TD-P2-05：`.remote-dev` 文档、默认值与当前 VAWS compatibility 实现不一致

现状：

- `architecture.md` 明确 Motor 不采用默认 session-management；
- `.remote-dev` 代码和 schema 已重新加入 `session_id`、`session_file`、`machine`
  selector，`VALIDATION.md` 也重新描述 managed session/NPU lease；
- `.remote-dev/README.md` 写默认 `root=/`，但 `.mcp.json` 和
  `core/endpoint.py` 默认 `root=/mnt`；
- direct Host、managed machine、managed session 三种语义混在同一通用层。

目标：

- 按 TD-P0-03 删除或隔离 Docker session compatibility；
- `DESIGN.md`、`README.md`、`VALIDATION.md`、MCP schema、CLI help、tests 和
  `.mcp.json` 对 direct endpoint、默认 root/cwd、权限边界保持一致；
- 测试中使用 `/vllm-workspace` 只能作为明确的隔离 fixture，不能出现在 Motor
  产品默认值或 live validation 结论中。

### TD-P2-07：发布级代码替换需支持 Motor 完整构建（protobuf + Rust），而非仅 hostPath/PYTHONPATH

现状：

- 快路径（固定共享目录 hostPath + `PYTHONPATH`）只覆盖纯 Python 修改：远端
  `motor/vllm/vllm-ascend` 源码树可被 Pod 直接加载，无需重新打包镜像。
- Motor 运行时还依赖两类产物，快路径无法提供：
  - protobuf 生成文件（`*_pb2.py`）：由 `.proto` 编译生成，若源码树缺少
    `pb2`，`import` 阶段即失败，hostPath 同步成功并不能代表代码已可用；
  - Rust 扩展（如 kv-connector）：需要 `cargo build` 产出动态库并打成 wheel
    安装进 Python 环境。

目标：

- 发布级替换提供 build 路径：编译 protobuf、构建 kv-connector wheel、重新
  打包基础镜像（image rebuild），作为 release/delivery 的显式旁路。
- 明确两条路径分工：日常 Python 迭代走快路径；涉及 pb2 / Rust 扩展 / 打包
  产物时必须走 build 路径。
- 快路径对"覆盖不到什么"有显式声明，避免同步成功后误认为代码已完整生效；
  build 路径有可执行的构建命令、产物落盘位置和镜像引用记录。

2026-08-03 落地记录（本条目已实现）：

- 新增 `scaffold/.agents/lib/mws_build.py`：
  - `detect_build_gaps`：扫描 `*.proto` 对应 `*_pb2.py` 与 `kv_conductor/bin`
    二进制，缺任一即返回 `build_required`，供快路径执行后显式提示走 build 路径；
  - `build_motor_wheel_in_docker`：在远端机器上 `docker run`（容器基础镜像 =
    运行时 `base_image_ref`）挂载共享盘固定 motor 源码 + build 输出目录，容器内
    执行上游 `bash build.sh`（含 `generate_proto.sh` 与 cargo build），产出
    wheel 到 `<remote_workspace_root>/motor-wheel-builds/<source_sha>/dist/`，
    以 `wheel.sha256` marker 幂等复用；
  - `render_wheel_replace_manifest`：生成 namespaced Job（hostPath 挂载 wheel
    目录 + `pip install --force-reinstall`），作为替换执行体；
  - `build_wheel_run_envelope`：产出 `motor-wheel-build` run 证据。
- 新增 skill `motor-build-wheel`（`SKILL.md` + `scripts/build_wheel.py`），入口
  `build_wheel.py --machine <alias> --source-sha <sha> [--base-image-ref <img>]`。
- 关键约束：wheel 构建**必须**在 Docker 内进行——本地 WSL 缺 CANN/grpcio-tools/
  Rust 工具链，直接构建产物与 Pods ABI 不一致；容器镜像即运行时镜像保证一致。
- 测试：`scaffold/tests/test_build_wheel.py`（8 passed）覆盖 gaps 检测、docker
  命令构造、幂等复用、替换 manifest、run envelope。

遗留（真实环境验收待办，不阻塞本地 fixture）：

- 在真实 K8s Host 上完成一次 docker 内 wheel 构建 + 替换 Job 端到端验收；
- kv-connector cargo 构建若需特定 Rust 版本，在 build 容器内固化 toolchain
  版本并记录到产物元数据。

---

## 2026-08-04 A3 真实部署暴露的技术债（90.90.97.30，`kubernetes-admin@kubernetes`）

以下条目来自 2026-08-04 在 A3 集群的首次真实 configure/apply/stop 全流程运行
（configure run `config-20260804T035253Z-f4bcd295`、deploy run
`deploy-20260804T065907Z-16a52dae`）。这不是 fixture 结论，是真实环境证据；
每条均可落到代码与验收动作。本节编号接 P2 之后，优先级独立评估。

### TD-A3-01（P0）：workspace 私加 `namespace` 字段，与 upstream `job_id` 即 namespace 的语义分叉

现状：

- Motor 原生 `user_config.json` 没有独立 `namespace` 字段；upstream deployer
  恒以 `job_id` 作为 namespace（generator 中
  `metadata.namespace = deploy_config.job_id`，`k8s_utils.py` 的
  `kubectl apply -n job_id`）。配置参考仅有的 `configmap_namespace` 是
  kube-system 下另一用途，与部署目标 namespace 无关。
- workspace `load_motor_deploy_config`（`mws_deploy.py:392`）曾单方面支持
  `deploy.get("namespace")`，缺省回退 `job_id`——这是 workspace 私加的第二套
  字段，违反仓库约束"部署配置只来自 Motor 原生配置"。
- 实际后果（已发生）：运行时配置 `namespace: mindie-motor-hxy` 时，workspace
  bundle 按显式 namespace 处理并 dry-run 通过；apply 阶段
  `_run_deploy_full_remote` 委托 upstream `deploy.py`，upstream 仍按
  `job_id: mindie-pd-precision-fi` 在旧 namespace 又创建一套同名 Controller/
  Coordinator/P/D vLLM 资源，一次 apply 落两个 namespace。

已定修复方向（2026-08-04 与用户确认）：

- 删除 workspace 对显式 `namespace` 字段的语义支持：`namespace = job_id` 恒等；
- 配置中若残留显式 `namespace` 且与 `job_id` 不一致，fail closed 报错，不再
  静默忽略；显式字段与 `job_id` 相同则兼容放行（旧运行时副本冗余字段）；
- 不再考虑"workspace 编排接管 apply / 给 upstream 打 patch 消费 namespace"
  两条路线，避免维护 Motor 分叉。

已落地：

- `mws_deploy.py` `load_motor_deploy_config`：namespace 恒等于 job_id，不一致
  时抛 `WorkspaceStateError`；
- 测试：`test_deploy_configure.py` 新增"显式 namespace 与 job_id 不一致
  fail closed"与"相等时兼容放行"两条用例。

验收：

- 显式 namespace 与 job_id 不一致的配置在 configure 阶段即失败，不可能到达
  apply；
- 一次 apply 的资源只落在一个 namespace（job_id），run 证据包含目标 namespace
  的全量资源清单（与 TD-A3-06 的 run-scoped 登记联动）；
- 真实环境复验：apply 后 `kubectl get deploy -A | grep <job_id>` 只出现在目标
  namespace。

遗留（本轮不封闭，仅剩 run-scoped 资源登记/清理一项，归 TD-A3-06）：

- 主路径的 `motor-config` 错位问题已随 A3-01（job_id == namespace 恒等）消失；
  upstream 在拉起时自建 ConfigMap 到 job_id namespace，Deployment 可正常挂载。
- `_apply_bundle_direct`（upstream deployer 不可用时的 fallback）直接 apply
  bundle，无 upstream 创建 ConfigMap，bundle 里也没有——该 fallback 路径的
  bundle 自包含缺口仍存在，已标注，不作为主路径验收项。

### TD-A3-02（关闭）：bundle 不自包含导致 `motor-config` 缺失——已由 TD-A3-01 解决，非独立技术债

关闭理由（2026-08-04 复核，与 A3-01 修复联动）：

- 该条是 A3-01（namespace 语义双轨）的症状而非独立根因。A3-01 修复前，
  upstream 把 `motor-config` 创建在旧 namespace（job_id），而 workspace
  overlay apply 到新 namespace（显式 namespace），两处错位导致 Pod
  `ContainerCreating`。A3-01 修复 `job_id == namespace` 恒等后，upstream 在
  拉起时自建 `motor-config` 到正确 namespace，主路径问题消失。
- `motor-config` 是 upstream `create_motor_config_configmap` 在非 dry-run 阶段
  用 `kubectl create configmap --from-file=...` 运行时创建的，dry-run 既不产
  YAML 也不执行创建，configure 阶段不可见——因此"预生成 ConfigMap 进 bundle"
  方案不成立，也不再需要。
- 剩余边界：`_apply_bundle_direct`（upstream 不可用 fallback）缺 ConfigMap，
  stop 清理需知道 ConfigMap 存在（归 TD-A3-06 run-scoped 资源登记）。

处理：本条标记关闭；剩余边界分别由 fallback 注释说明和 TD-A3-06 承担。

### TD-A3-03（P1）：环境契约是单代硬编码，不支持 AscendJob / InferServiceSet 两代链路二选一

现状：

- `environment-contract.yaml` 把 `ascendjobs.mindxdl.gitee.com` 写为硬性必需；
  `mws_environment.py:155` 只支持 `required_api_resources` 平铺列表逐项检查，
  无 one-of 语义；`component_patterns` 同样逐项硬性匹配（含 `ascend-operator`）。
- 实际集群证据：A3 集群存在 `podgroups.scheduling.volcano.sh`、
  `inferservices[ets].mindcluster.huawei.com` 与 infer-operator-manager Pod，
  不存在 ascendjobs 与 ascend-operator。`deploy_mode: multi_deployment` 生成
  普通 Deployment，本就不需要 AscendJob。preflight 在
  `api_resource:ascendjobs` 误报停止。
- `scaffold/profiles/a2-dev.yaml` 的 `mindcluster` 段是第二份硬编码
  （ascendjobs/podgroups + ascend-operator），两处需同步改。

目标：

- 契约 schema 升级支持"组内任一满足"（one-of）：workload API 组
  （ascendjobs | inferservicesets）、operator 组件组
  （ascend-operator | infer-operator）；
- podgroups（volcano）与 noded/clusterd/ascend-device-plugin 保持硬性；
- `a2-dev.yaml` 与契约 YAML 同源或同步更新；
- preflight 结果明确记录实际命中的是哪一代链路。

验收：

- fixture：AscendJob-only 集群与 InferServiceSet-only 集群各一条 preflight
  通过用例；两者都缺时 fail closed；
- 真实环境：A3 集群 preflight 通过且证据记录命中 infer 链路。

### TD-A3-04（P1）：部署前验证断层——API server 接受 YAML 不代表节点能跑

现状（三个子项，均可独立落地）：

1. 逐节点镜像存在性/可拉取性无检查。实际后果（已发生）：apply 成功但新
   ReplicaSet 因 `registry-1.docker.io Gateway Time-out` 出现
   `ErrImagePull`/`ImagePullBackOff`，旧 ReplicaSet 保留，滚动更新卡死。
   `motor-deploy-configure` 只做 manifest/RBAC/server-side dry-run，
   `motor-k8s-deploy` 在 apply 后才观察 Pod Ready。
2. preflight 只验证 MindCluster 组件名称存在，不验证 Deployment/Pod 健康；
   实际集群 clusterd 有 Failed/Pending Pod、noded 有 Pending Pod 时 preflight
   仍报 ok。
3. NPU 容量校验被 `prefill_node_selector`/`decode_node_selector` 缺失阻断后，
   靠 `--skip-npu-check` 绕过继续，配置完整性问题被消解为"跑通流程"。

目标：

- configure（或独立 preflight 子检查）对目标镜像做逐节点存在性检查：记录
  节点、镜像引用、image ID/digest、检查时间；任一候选节点无镜像且无法证明
  可拉取时，apply 前 fail closed；
- preflight 组件检查从"名字存在"升级为"关键组件 Ready"（至少 clusterd、
  noded、device-plugin、operator、volcano 的 Pod 健康）；
- 缺 selector 的配置要么补全后校验容量，要么显式声明跳过且 run 证据中标记
  容量未验证，不得静默通过。

验收：

- fixture：镜像缺失节点被检出并阻断 apply；组件非 Ready 时 preflight 报
  error/warning；
- 真实环境：A3 上镜像检查输出逐节点证据表；容量未验证的 run 证据中带显式
  标记。

### TD-A3-05（P2）：NodePort 治理缺失——无占用探测、无范围校验、无自动分配

现状：

- workspace 只有 `inject_node_port_override`（`mws_deploy.py:325`）与
  `_load_node_port_overrides`（`mws_deploy.py:363`）：用户预先给映射，改写
  manifest。不查询集群 Service、不校验 NodePort 范围、不自动分配。
- 冲突实际靠 server-side dry-run 撞错发现（`31027`/`31015`/`31017` 分别被
  集群现有 Service 占用），人工避让到 `32027`/`32015`/`32017` 才通过；
  映射只存在于本地未跟踪运行时副本。
- `scaffold/docs/validation/README.md` 已写明自动 fallback "实现待落地"；
  upstream 单项配置 `coordinator_infer_node_port: "-"` 只覆盖 Coordinator
  infer 一个端口，不是全局方案。

目标：

- configure 阶段扫描 manifest 中全部 nodePort，`kubectl get services -A`
  探测集群级占用（NodePort 不按 namespace 隔离）；
- 冲突时给出避让建议（自动选空闲端口）或要求显式 overrides，fail closed；
- 校验目标端口在合法 NodePort 范围内、且本批 manifest 内不重复；
- 避让映射写入 bundle 证据，不停留在未跟踪本地副本。

验收：

- fixture：占用端口被探测并触发自动避让/报错；范围外端口被拒；
- 真实环境：A3 上 31015/31017/31027 场景重放，configure 直接产出可用映射，
  不需要人工试错。

### TD-A3-06（已落地）：stop 清理语义不对等——改为复用 upstream `delete.sh`

现状（修复前）：

- `stop_from_plan`（`mws_deploy.py:1049`）只对 bundle 内 manifest 反序
  `kubectl delete --ignore-not-found`；upstream 运行时生成的 `motor-config`、
  `job-summary-*` ConfigMap、日志采集进程等不在清单中，stop 成功后仍有残留。
  本次实际靠人工补删 ConfigMap 才把 namespace 清到只剩默认资源。
- 删除成功只看 kubectl 返回码，不轮询确认资源实际消失。
- 根因：workspace 只继承了 upstream 的生成能力（deploy.py），没继承配套的
  清理能力（`delete.sh`）。upstream `delete.sh` 本身是完整的：删
  `output_yamls/*.yaml` workload、等 Pod 终止（超时 force-delete）、删
  `motor-config` ConfigMap、sed 还原启动脚本 patch、`pkill` log_monitor。

已落地（2026-08-04）：

- 新增 `mws_deploy.stop_via_upstream_delete_sh`：在远端 deployer 目录执行
  `bash delete.sh <namespace>`（namespace == job_id，与 A3-01 对齐）；
  `deploy_stop.py` 主路径改用它，`stop_from_bundle` 降级为 fallback。
- 测试：`test_deploy_runtime.py` 新增两条用例（正常执行 delete.sh /
  delete.sh 缺失时 fail）。

目标（完成定义）：

- deploy 阶段登记 upstream 与 workspace 实际创建的全部 run-scoped 资源
  （kind/name/namespace），delete 使用统一资源清单；——由 delete.sh 替代，
  不再自建清单；
- `motor-config`、`job-summary-*` 等 ConfigMap 纳入清理范围；——delete.sh
  已覆盖 `motor-config`；
- 日志采集/监控辅助进程在 delete 前停止；——delete.sh `pkill log_monitor`；
- delete 后轮询确认 Pod/ReplicaSet/Service/ConfigMap 消失；——delete.sh
  等 Pod 终止 + force-delete 兜底；
- namespace 删除保持独立、明确授权的 destructive 操作，普通 stop 不得隐式
  执行。

遗留：

- `job-summary-*` ConfigMap 由 upstream log 采集运行时创建，delete.sh 只显式
  删 `motor-config`；若 real 环境确认 `job-summary-*` 残留，需在 delete.sh
  覆盖或 stop 后补一条清理。尚未在真实环境复验。
- 真实环境复验：A3 上 stop 后目标 namespace 仅剩 `kube-root-ca.crt` 与
  `default` ServiceAccount，无需人工补删。

### TD-A3-07（P3）：环境与依赖治理记录项

以下为真实运行暴露的周边问题，各自独立小改，不阻塞上述条目：

- Motor pinned commit `45901d9f...` 不在当前 GitCode remote refs（GitCode
  传输返回非 Git 内容，`remote-curl: bad line length character`）。当前以
  master 源码继续验证，未改顶层 gitlink；正式提交/发布前必须补齐可获取的
  Motor 版本映射或维护 fork mirror，不能静默把 master 当 pinned commit。
- 本机控制机缺 PyYAML（configure 处理 manifest 时
  `ModuleNotFoundError: No module named 'yaml'`）。需在本地依赖声明/skill
  启动自检中补齐，fail fast。
- upstream `log_monitor.py` 使用 `logging.Logger | None`，远端 Python 3.9
  运行时报 `TypeError`（被日志采集包装成 warning 未阻塞）。parity 或
  preflight 应校验远端 Python 版本与同步源码的语法兼容性；upstream 兼容性
  问题需向上游反馈。

---

## 完成定义

单项技术债只有同时满足以下条件才可关闭：

- 生产路径已修改，不是只修改 fixture；
- 失败路径 fail closed，且不会发布错误的 ready/complete；
- 有覆盖 producer 与 consumer 的测试；
- 相关 skill、CLI help、Agent shim 和当前状态文档同步；
- `git diff --check`、`scaffold/tests` 和 `.remote-dev/tests` 通过且无新增 warning；
- 涉及真实环境的项目明确区分「本地 fixture 完成」和「真实环境验收完成」。
