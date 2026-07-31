# 当前技术债

本文只记录截至 2026-07-31 仍未解决、能够落实到代码或验收动作的技术债。
已完成事项、历史实施过程、冻结设计全文和旧 Agent 工作包不在本文保留。

## 基线与范围

当前本地基线（2026-07-31 修复后实际执行结果）：

- `scaffold/tests`：**141 passed**，0 warning；
- `scaffold/.remote-dev/tests`：**73 passed / 44 subtests passed**；
- 第二部分 preflight、configure、apply、Ready 与 runtime source proof 均有实现与
  fixture，但尚未完成真实集群验收（见 TD-P1-05）。

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

### TD-P2-05：`.remote-dev` 文档残留 VAWS session 语义

现状：

- 代码层已无 session/container selector；
- `DESIGN.md`、`VALIDATION.md` 仍描述 VAWS managed session、NPU lease 等历史行为；
- `MIGRATION-NOTES.md` 属历史记录可保留。

目标：

- `DESIGN.md`/`VALIDATION.md` 改为 direct-endpoint + `/mnt/motor-workspace` 当前模型；
- 测试已用显式 `root`/`cwd`，但部分仍引用 `/vllm-workspace` 作为隔离测试 root
  （可接受，须在文档说明）。

---

## 完成定义

单项技术债只有同时满足以下条件才可关闭：

- 生产路径已修改，不是只修改 fixture；
- 失败路径 fail closed，且不会发布错误的 ready/complete；
- 有覆盖 producer 与 consumer 的测试；
- 相关 skill、CLI help、Agent shim 和当前状态文档同步；
- `git diff --check`、`scaffold/tests` 和 `.remote-dev/tests` 通过且无新增 warning；
- 涉及真实环境的项目明确区分「本地 fixture 完成」和「真实环境验收完成」。
