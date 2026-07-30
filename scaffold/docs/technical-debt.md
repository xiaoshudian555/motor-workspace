# 已确认技术债

并行实施的文件边界、工作包和验收顺序见
[`implementation-plan.md`](implementation-plan.md)。
六项定义冻结后的实现工作单见
[`agent-work-orders.md`](agent-work-orders.md)。

本文同时记录两类内容：

1. 已经由责任边界讨论确认、但当前实现尚未完成的工作；
2. 已定位到具体边界、但仍需讨论后才能固定的设计定义。

两类内容在各节中明确分开。本文不是所有未来功能的愿望清单，也不按旧
P0/P1/P2 或 VAWS skill 名称机械展开。

## 第一部分：远程开发准备与代码同步

第一部分的目标责任链已经确定：

```text
repo-init
  → workspace-ready

machine-management
  → machine-ready

remote-code-parity
  → parity-complete
```

下面记录的是实现和文档尚未达到该目标的内容，不代表要在第一次真实操作前
一次性实现所有体验优化。

### repo-init 尚需完成

- [ ] 将当前“子模块初始化 + lock 检查”扩展为明确的
  `workspace-ready` 结果。
- [ ] 检查 Git、Python、GitHub CLI 等本地必要工具；安装或修改系统环境前
  获取用户 consent。
- [ ] 检查 GitHub/GitCode 认证状态，但不把 token、密码或密钥写入 tracked
  文件。
- [ ] 支持确认或引导创建 fork，并检查三个源码仓的 `origin` / `upstream`
  拓扑。
- [ ] 报告三仓 HEAD、gitlink、dirty/untracked 状态；dirty workspace 是
  parity 的合法输入，不得作为失败门禁。
- [ ] 补充 Motor、vLLM、vLLM-Ascend 目标版本和兼容关系的检查或明确
  “无法自动判断”结果。
- [ ] 明确 `workspace.lock.yaml` 只提供诊断信息，不成为日常 dirty tree
  同步和部署的硬门禁。
- [ ] 更新 `repo-init/SKILL.md`、脚本帮助和测试，使其描述实际
  `workspace-ready` 输入、输出与不负责事项。

当前 repo-init 实现主要覆盖子模块和 lock，尚不能交付设计文档定义的完整
`workspace-ready`。

#### 第一阶段并行工作：工作包 B（repo-init，已冻结）

工作包 B 的 subagent 两次运行均中止或卡住，未完整交付
[`implementation-plan.md`](implementation-plan.md) 工作包 B 验收。工作区可能已有
部分 repo-init VAWS 迁移（未提交/untracked 的 `repo-init/**` 与
`tests/test_repo_init.py`）；**暂缓继续 B 实现**，待后续单独收口。

- [ ] **`tests/test_repo_init.py::test_apply_init_submodules_fixture`** — 冻结时全文件
  **20 passed / 1 failed**（21 项）。该 fixture 在 gitlink-only
  `workspace_with_submodule` 上调用 `repo_init_apply.init_submodules()`，期望
  首次/二次均为 `status == "ok"` 且 `sources/child` 目录存在；实际失败：

  ```text
  FAILED tests/test_repo_init.py::test_apply_init_submodules_fixture
  AssertionError: assert 'error' == 'ok'
    - ok
    + error
  (at assert first["status"] == "ok" after init_submodules())
  ```

  根因待修：`git submodule update --init` 对 fixture 的 `file://` gitlink 子模块 URL
  失败（git 默认禁止 `file` 协议克隆；需 bare-repo fixture 或
  `protocol.file.allow=always` 等测试策略）。后续：恢复该测试、修正 fixture 或
  `init_submodules()` 路径，再跑 `pytest tests/test_repo_init*.py -q` 直至工作包 B
  验收全绿。

  **2026-07-30 定向复现**（`pytest …::test_apply_init_submodules_fixture -q --tb=short`）：
  当前保留工作区中该用例已不存在，pytest 报 `ERROR: not found`（exit 4）；全文件
  现为 20 passed。债务项仍指向上述冻结时的失败断言，而非“已修复”。

### machine-management 尚需完成

- [ ] 将 `machine-ready` 结果契约固定为：SSH/远端命令可用、固定目录映射
  合法、目录可创建/读写/清理、parity 所需基础工具可用。
- [ ] 补全 inventory 的 add/list/update/remove/repair 工作流；破坏性或
  bootstrap 操作继续要求用户 consent。
- [ ] 验证 `remote_workspace_root` 必须位于允许的 `mount_root` 内，避免只
  检查绝对路径而允许越出共享根。
- [ ] 明确凭据只保存引用，不将 SSH key、token 或 kubeconfig 内容写入
  tracked 文件。
- [ ] kube context、hardware profile 和基础 NPU 观察结果可以登记，但不得
  被解释为 `machine-ready` 的集群部署证明。
- [ ] 从 machine verify 中移出 Kubernetes、namespace RBAC、Pod readiness、
  MindCluster/Volcano/CRD 和部署级 NPU 调度检查。
- [ ] 为 machine-ready 增加 fixture 测试，覆盖连接失败、目录越界、目录
  不可写、基础工具缺失和 cleanup 失败。
- [ ] 更新 `machine-management/SKILL.md`，删除“MindCluster/K8s checks are
  part of verify”的旧边界。

当前
`scaffold/.agents/skills/machine-management/scripts/machine_verify.py`
仍把 kube context、namespace RBAC、CRD 和 Pod readiness 混在 machine
verify 中，需要按新的三步边界迁移：K8s/MindCluster 基础检查进入第二部分
第一步，namespace 存在性和本次 manifest 的 RBAC 检查进入第二步；历史 Pod
readiness、候选节点 hostPath 和运行期调度探测不进入前两步。

### remote-code-parity 尚需完成

- [ ] 要求并引用成功的 `machine-ready` 证据，而不只根据 machine alias
  隐式假设远端已经可用。
- [ ] 固定 tracked、staged、unstaged、untracked non-ignored 文件以及
  python-overlay 的纳入和排除规则。
- [ ] 在远端发布完成后重新计算目标文件集合和内容摘要，证明解包后的实际
  远端内容与本地一致；不能只记录本地 tarball 摘要。
- [ ] manifest 明确记录本地源状态、固定目标目录、远端验证结果、排除项和
  失败节点/文件。
- [ ] 保持 staging 发布和失败清理；覆盖过程中任何一步失败时不得错误返回
  `parity-complete`。
- [ ] parity 只验证同步目标中的文件正确，不检查 K8s 候选节点、Pod 挂载、
  `PYTHONPATH` 或 Pod 内 import 路径。
- [ ] 增加远端内容不一致、文件删除、空 overlay、上传/解包/切换失败和
  manifest 不完整的 fixture 测试。
- [ ] 更新 `remote-code-parity/SKILL.md`，明确输入、交付物、consent 和
  “不负责 Pod 运行证明”。

### 第一部分公共底座和文档尚需完成

- [ ] 明确 `.remote-dev` 只提供通用 endpoint read/write/bash/search/job/
  artifact 原子操作，不理解 machine-ready、parity 或 Motor/Kubernetes。
- [ ] 收敛 `.remote-dev` transport 与 `.agents/lib/mws_transport.py` 的重复
  SSH/传输实现，选择复用或明确分层，避免两套错误处理和 endpoint 语义。
- [ ] 清理不再参与主链的 `SESSIONS_DIR` 等 session 残留；内部
  `workspace_id` 如保留，只能用于本地诊断，不能进入远端路径或下游必需
  输入。
- [ ] 统一 `workspace-ready`、`machine-ready`、`parity-complete` 的结果
  字段、错误语义、run 引用和 evidence 路径。
- [ ] 同步根 `AGENTS.md`、第一部分各 `SKILL.md`、生成的 Agent shim、
  README 和测试中的责任描述。
- [ ] 在真实远端执行第一部分纵向操作；无远端环境时只能声明实现与 fixture
  测试完成，不能声明环境验收完成。

### VAWS 参考实现的复用边界

已审计的参考 checkout：

```text
repository: https://github.com/maoxx241/vllm-ascend-workspace.git
observed checkout: /tmp/vllm-ascend-workspace
commit: 4a952fcc2b6bce045ca2f5a472ea1af93af2858c
license: MIT
```

`/tmp` 路径不是持久依赖。执行迁移的 Agent 必须先确认参考 checkout 和 commit；
如果本地参考不存在，应从上述仓库取得明确版本，不能凭本文重写一份“类似
VAWS”的实现。复制较大代码段时保留 MIT license/attribution 要求。

#### 可直接迁移后做机械改名的通用能力

以下逻辑不包含 vLLM 容器、session 或服务语义，可优先从 VAWS 迁移，并补
Motor fixture：

| 能力 | VAWS 参考位置 | Motor 目标位置/注意事项 |
|---|---|---|
| 原子 JSON 写、文件锁、并发 inventory 更新 | `.agents/skills/machine-management/scripts/inventory.py` | 收敛进 `.agents/lib/` 公共状态层，不照搬 VAWS record 字段 |
| ID、env、路径边界和 device CSV 校验 | `.agents/lib/vaws_validate.py` | `mws_validate.py` 已由此扩展，做差异审计即可 |
| progress stderr + 单一 JSON stdout 的 wrapper 骨架 | `machine-management/scripts/_workflow_common.py` | 抽取通用 result/workflow helper，统一 Motor 状态枚举 |
| verify-only 不修复、probe-first、needs-input/needs-repair 分流 | `machine-management/scripts/machine_verify.py` 与 references | 保留行为模式，替换实际检查项 |
| Git/gh/子模块/remote topology 的只读 probe | `repo-init/scripts/repo_init_probe.py` | 扩展为 workspace + Motor + vLLM + vLLM-Ascend 四仓视图 |
| 保守配置 origin/upstream、保留额外 remote | `repo-init/scripts/repo_topology.py` | 基本可迁移，替换 repo role 和 URL 规则 |
| repo-init behavior/acceptance/command recipe 的包结构 | `repo-init/references/` | 按 Motor 的 workspace-ready 契约裁剪 |

机械迁移不等于直接覆盖当前文件。每次迁移必须先列出保留函数、删除函数和字段
映射，再通过 `apply_patch` 落地，避免把 VAWS 路径或状态名混入 Motor。

#### 当前已经从 VAWS 复用、不要再次整包复制的能力

`scaffold/.remote-dev/` 与参考 VAWS `.remote-dev/` 的绝大多数 core、schema、
tool 和 test 源文件相同。Motor 当前只对 repo root、endpoint selector、
managed session/machine 解析和 shim 路径做了定向适配。

后续 Agent 应：

- 以 Motor 版本为基线；
- 对参考版本逐文件 diff，只迁移明确修复；
- 保留 Motor 删除 `session_id/session_file/machine` endpoint selector 的决定；
- 不用 VAWS 整目录覆盖 Motor `.remote-dev/`；
- 优先修复 Motor 重构造成的测试变量错误，再运行双方契约测试比较。

`.agents/lib/mws_validate.py` 和 `mws_local_state.py` 也已明显源自 VAWS 的通用
实现，应做差异补齐，而不是创建第三套状态/校验库。

#### 只能适配复用、不能照抄的能力

| VAWS 能力 | 可复用思想 | 必须重写的 Motor 差异 |
|---|---|---|
| machine-management | inventory、auth boundary、public wrapper、read-only verify、repair/remove 安全边界 | Motor 登记现有 SSH/K8s/shared mount 目标，不创建 vLLM 容器，不选择运行镜像，不配置容器 sshd |
| remote-code-parity | dirty tree 作为 source of truth、manifest、锁、consent、fail closed、完成后内容证明 | Motor 同步到固定共享目录；不用 synthetic Git refs、container cache、session path、editable install 或 `/vllm-workspace` |
| remote toolbox | job/artifact/hash/cleanup 的验收思路 | 通用原子操作已在 `.remote-dev`；不迁移 managed session/service adapter |
| serving status/readiness | 先验证新输入再停止旧服务、状态与日志证据 | Motor 使用 Kubernetes/MindCluster 资源和 upstream deployer，不使用单容器 vLLM service lifecycle |

#### 明确禁止从 VAWS 搬入的语义

- session-management、worktree/session container、NPU/port lease。
- 创建或维护 Docker 容器、容器 mesh、专用容器 sshd。
- `rc/main/stable` 容器镜像选择和镜像 bootstrap。
- `/vllm-workspace`、`.vaws-local`、workspace-id cache、synthetic Git mirror/ref。
- parity 中的 pip/editable install、CMake/native rebuild 和包替换 consent。
- VAWS 单容器 serving、benchmark、profiling 生命周期作为 Motor Deploy 实现。
- “VAWS 禁止 scp”这一传输策略本身；Motor 应统一复用 `.remote-dev` 或选定的
  transport adapter，而不是同时保留第三套传输。

#### VAWS 无法提供、必须按 Motor 设计实现的部分

- K8s/MindCluster environment preflight。
- Motor user config、namespace/job-id、模型/镜像/NPU/调度输入解析。
- upstream Motor deployer dry-run 和最终 manifest 生成/替换。
- immutable config bundle、`config_fingerprint` 和配置复用。
- Kubernetes server-side dry-run、apply、Ready、Pod 内代码加载证明。

因此 VAWS 可以显著减少第一部分和公共底座工作量，但不能替代第二部分三个
步骤的 Motor/Kubernetes 设计与实现。

## 第二部分：环境、配置、部署三个完整步骤

第二部分与第一部分一样固定拆成三个完整步骤，使用独立顺序编号：

```text
motor-deploy-preflight
  → deploy-environment-ready

motor-deploy-configure
  → deploy-config-ready + immutable config bundle

motor-k8s-deploy
  → deploy-complete + Ready/运行代码证据
```

已确认的原则：

- 第一步只判断 K8s 与 MindCluster 基础环境，不读取或验证 Motor 配置。
- 第二步拥有配置生成、staging、全部替换、upstream dry-run、manifest 校验、
  server-side dry-run、配置与 parity 路径对应以及配置复用判断。
- 第三步不重新 render；它只校验并原样 apply 第二步的不可变配置包，等待
  Ready，并证明 Pod 实际加载当前 parity 对应代码。
- 前两步不得修改 Kubernetes 状态；第二步允许写 run-scoped 本地 staging、
  配置包和证据。
- 三个步骤分别有独立入口、run、结果和失败边界。上层可以顺序编排，但不得
  合并结果。

### 执行顺序和目标证据链

下游只通过显式 run ID 消费上游结果，不用 inventory 中的
`last_verified_at` 或模糊 `last_*` 字段代替证据。

```text
workspace-ready
  → machine-ready
  → parity-complete
  → deploy-environment-ready
  → deploy-config-ready
  → deploy-complete
```

目标记录至少包含：

| 结果 | 责任 skill | 目标证据目录 | 必需引用 |
|---|---|---|---|
| `workspace-ready` | `repo-init` | `.motor-workspace-local/workspace-runs/{workspace_run_id}/` | workspace root、三仓状态 |
| `machine-ready` | `machine-management` | `.motor-workspace-local/machine-runs/{machine_run_id}/` | workspace、machine-ref、checks |
| `parity-complete` | `remote-code-parity` | `.motor-workspace-local/parity-runs/{parity_run_id}/` | machine-ready、固定路径、内容摘要 |
| `deploy-environment-ready` | `motor-deploy-preflight` | `.motor-workspace-local/environment-runs/{environment_run_id}/` | machine-ready、kube context、环境契约版本、cluster identity、checks |
| `deploy-config-ready` | `motor-deploy-configure` | `.motor-workspace-local/config-runs/{config_run_id}/` | environment、machine、parity、Motor 原生配置、bundle ID、fingerprint、checks |
| `deploy-complete` | `motor-k8s-deploy` | `.motor-workspace-local/deploy-runs/{deploy_run_id}/` | config run/bundle、parity、apply、Ready、runtime source evidence |

每个目录中的完成结果文件名、公共 envelope 和 schema version 仍需在公共结果
契约中一次性固定，不能由各 skill 自行发明。

### 已固定的六项设计定义

这些定义已确认，实施 Agent 不得自行选择其他方案。

#### 1. 检查状态和中断语义

- 每个执行的检查使用 `ok`、`warning`、`error`、`unavailable`。
- `warning` 保存证据后继续，允许步骤最终 ready。
- `error` 或 `unavailable` 立即中断当前步骤、非零退出，不执行下游步骤，
  不得发布 ready。
- 人修复后重新运行并产生新 run；不做自动修复或断点续跑。
- 不适用的检查不执行，不输出 `skipped/not_applicable` 冒充成功。

#### 2. 只使用 Motor 原生配置

- namespace、job-id、镜像、模型、NPU、scheduler、queue、affinity 等字段只
  来自 Motor upstream deployer 原生 `user_config.json` 和 `env.json`。
- Workspace 不提供第二套字段级 CLI override 或 deploy profile。
- Workspace 只选择 machine/run/原生配置路径并调用 upstream
  `deploy.py --config_dir ... --dry-run` 或等价原生参数。
- `workspace.lock.yaml` 只作诊断，不覆盖 Motor 配置。

#### 3. namespace 必须预先存在

- namespace 使用 `user_config.json` 中 `motor_deploy_config.job_id` 的原生
  Motor 语义。
- 第一步不读取 namespace。
- 第二步检查 namespace 存在和本次 manifest 所需权限。
- 第三步不创建 namespace；不实现 `create-on-apply`。

#### 4. environment-ready 不跨工作流复用

- 每个新的 configure/deploy 工作流都必须重新执行 preflight。
- 同一工作流的后续步骤通过显式 run ID 引用结果。
- 第一版不实现 TTL、`expires_at` 或 `last_verified_at` 命中。
- 结果仍记录 cluster identity、API server、kube context、组件版本和时间。

#### 5. 结构配置和代码内容分开

`config_fingerprint` 覆盖：

- Motor 原生 `user_config.json` 和 `env.json`；
- machine 固定路径映射；
- Motor upstream deployer 版本；
- workspace hostPath/volumeMount/`PYTHONPATH` 注入器版本。

parity 的代码内容摘要不进入结构 fingerprint。第二步只验证最终 manifest
引用当前 machine/parity 声明的固定路径，不重新校验或管理代码内容。配置
结构未变化时，可以复用不可变 bundle，并用新的 config run 重新绑定当前
parity 路径引用。最终 manifest、原生配置副本和验证证据由独立
`bundle_digest` 保证完整性；第一版不做自动垃圾回收。

#### 6. apply 前不做运行期诊断验证

- 前两步不创建临时 Pod/Job 或其他诊断资源。
- 第二步只做 upstream dry-run、最终 manifest 结构与替换检查、
  namespace/RBAC 和 Kubernetes server-side dry-run。
- apply 前不检查镜像实际拉取、容器内模型可读或候选节点 hostPath 实际可见。
- 第三步 apply 并等待 Ready；这些条件导致拉起失败时，步骤报错并保留现场，
  再由 diagnosis 处理。
- 这些运行期条件不是第二步的 `deferred` 检查，也不是
  `deploy-config-ready` 的完成门禁。

日常代码更新的快速路径固定为：

```text
remote-code-parity
  → motor-deploy-configure 复用结构配置 bundle 并绑定当前固定路径
  → motor-k8s-deploy restart
  → Ready + runtime source evidence
```

新的工作流仍需重新执行 environment preflight；代码摘要变化本身不触发配置
重新生成。

### 已定义清楚、属于纯实现偏差的工作

下面无需再讨论责任归属，应直接按三步目标迁移。

#### 第一步：环境前置验证

- [ ] 新建独立 `motor-deploy-preflight` skill、脚本、结果 schema 和测试。
- [ ] 只消费 machine-ready、kube context 和 workspace 固定的环境契约，不消费
  parity、user config、namespace、模型、镜像或最终 manifest。
- [ ] 从 `machine_verify.py` 迁入 Kubernetes API 和
  MindCluster/Volcano/CRD/controller/device plugin 等基础环境检查。
- [ ] 不迁入 namespace RBAC、配置调度、候选节点、模型、镜像、dry-run 或
  Pod readiness。
- [ ] 在真实 K8s + MindCluster + Ascend NPU 环境完成独立验收；fixture
  测试不得冒充环境验收。

#### 第二步：配置准备与验证

- [ ] 新建独立 `motor-deploy-configure` skill、脚本、config run、不可变
  bundle 和测试。
- [ ] 将当前 `deploy_plan.py`、`mws_deploy.render_plan()`、staging、
  `run_deploy_dry_run()`、manifest 替换、`kubectl_dry_run_and_diff()` 迁入
  第二步；迁移而不是复制。
- [ ] 第二步显式消费已有 parity run；不得隐式替用户执行覆盖同步，也不得
  自动伪造 `--approved-overwrite`。
- [ ] 只消费 Motor 原生 `user_config.json` 和 `env.json`；不得增加 deploy
  profile 或字段级 CLI override。
- [ ] 在最终 manifest 生成后检查 namespace 存在、精确 RBAC、manifest
  结构以及 hostPath/volumeMount/`PYTHONPATH` 固定路径注入。
- [ ] 不做候选节点 hostPath、镜像实际拉取或模型容器内可读性验证，也不创建
  诊断 Pod/Job。
- [ ] server-side dry-run 失败、本地缺 kubectl 或必要检查不可用时立即报错；
  `warning` 保存后允许继续。
- [ ] 生成最终 manifest、diff、bundle digest、config fingerprint、
  `deploy-config-ready` 和复用证据。

#### 第三步：实际部署与运行验收

- [ ] 将 `motor-k8s-deploy` 收敛为消费 `deploy-config-ready` 和不可变配置包；
  不再自动 parity、render、替换或 dry-run。
- [ ] apply 前校验 config run、bundle digest、fingerprint、machine 和当前
  parity 绑定；缺失或不匹配时 fail closed。
- [ ] 修复 `deploy_apply.py` 引用未定义 `args.plan_dir` 的运行错误。
- [ ] apply 后保存资源级结果，等待关键组件和 Pod Ready，并验证最小服务。
- [ ] 在 Pod 内采集 `motor`、`vllm`、`vllm_ascend` 实际加载路径，不能仅以
  Pod Ready 声明部署完成。
- [ ] restart/stop/status 关联明确 deploy run 和状态机；restart 后重新采集
  Ready 和当前 parity 的运行代码证据。

#### 公共交接、文档和测试

- [ ] parity 只依赖 `machine-ready`，不依赖第二部分环境检查。
- [ ] 更新 `mws_run_state.py`，支持 workspace/machine/environment/config/
  deploy 独立 run 和显式引用。
- [ ] 更新根 README、`AGENTS.md`、`directory-ownership.md`、三个第二部分
  skill、machine-management skill、Agent shim、CLI help 和测试。
- [ ] 删除旧的字母子步骤表述和 `deploy-preflight-ready` 旧结果名。
- [ ] 修复当前测试重构遗留的 `SCAFFOLD`、`REPO_ROOT` 和
  `SCAFFOLD_SCAFFOLD_ROOT` 未定义问题，恢复完整测试收集。
- [ ] 增加 3+3 纵向契约测试：缺上游结果、结果过期、fingerprint 不匹配、
  bundle 被修改、dry-run 失败、apply 失败、Ready 失败、代码路径不匹配。

## 当前验收口径

完成 machine-management 代码与 fixture 测试，只能声明：

> 远程开发目标和固定同步目录已经准备好。

完成环境 preflight 代码与 fixture 测试，但没有真实集群，只能声明：

> 环境检查实现和本地测试完成，尚未完成 K8s/MindCluster 环境验收。

完成配置步骤只能声明：

> 本次不可变配置包已经生成或经 fingerprint 证明可复用，并通过约定的配置
> 校验和 dry-run。

只有第三步在真实环境 apply 成功、达到 Ready、最小服务可访问且 Pod 内代码
路径证据与当前 parity 一致后，才能声明：

> 本次 Motor deploy-complete。
