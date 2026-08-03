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

验收：

- 文档记录哪些产物必须走 build 路径（pb2、kv-connector wheel）及原因；
- build 路径存在可执行脚本或明确命令，产物有固定落盘位置；
- 快路径执行后能检测 pb2 / Rust 扩展缺失并提示走 build 路径（而非静默失败）；
- 该条目不阻塞第一版真实部署验收，但须在发布级替换前完成。

---

## 完成定义

单项技术债只有同时满足以下条件才可关闭：

- 生产路径已修改，不是只修改 fixture；
- 失败路径 fail closed，且不会发布错误的 ready/complete；
- 有覆盖 producer 与 consumer 的测试；
- 相关 skill、CLI help、Agent shim 和当前状态文档同步；
- `git diff --check`、`scaffold/tests` 和 `.remote-dev/tests` 通过且无新增 warning；
- 涉及真实环境的项目明确区分「本地 fixture 完成」和「真实环境验收完成」。
