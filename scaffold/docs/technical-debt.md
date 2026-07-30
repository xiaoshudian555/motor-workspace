# 已确认技术债

并行实施的文件边界、工作包和验收顺序见
[`implementation-plan.md`](implementation-plan.md)。

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
第一步，namespace/RBAC、配置调度和候选节点检查进入第二步，历史 Pod
readiness 不进入前两步。

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
| `deploy-environment-ready` | `motor-deploy-preflight` | `.motor-workspace-local/environment-runs/{environment_run_id}/` | machine-ready、kube context、environment profile、cluster identity、checks |
| `deploy-config-ready` | `motor-deploy-configure` | `.motor-workspace-local/config-runs/{config_run_id}/` | environment、machine、parity、配置输入、bundle ID、fingerprint、checks |
| `deploy-complete` | `motor-k8s-deploy` | `.motor-workspace-local/deploy-runs/{deploy_run_id}/` | config run/bundle、parity、apply、Ready、runtime source evidence |

每个目录中的完成结果文件名、公共 envelope 和 schema version 仍需在公共结果
契约中一次性固定，不能由各 skill 自行发明。

### 尚需讨论并固定的设计定义

以下不是单纯编码任务；未决定前会导致不同实现互不兼容。

#### 1. environment-ready 的有效期和失效条件

需要明确：

- 是否每次配置前都重跑，还是允许按 cluster identity、kube context、环境
  profile 摘要和 TTL 复用。
- controller/CRD/device plugin 版本变化是否立即失效。
- machine 的 kube context 引用变化时如何发现和拒绝旧结果。
- 本地缺少 `kubectl`、API 不可达或必要状态无权读取时统一 fail closed。

建议把 `cluster_uid`、context、environment profile digest、检查时间和
`expires_at` 放入结果；具体 TTL 需讨论后固定。

#### 2. 配置输入的唯一来源和冲突策略

必须固定：

- namespace/job-id 以 Motor user config、deploy profile 还是显式 CLI 为准。
- `base_image_ref` 以 user config、显式输入还是 `workspace.lock.yaml` 为准；
  `workspace.lock.yaml` 已被定义为诊断信息，不能无说明覆盖本次配置。
- 模型可能有多个角色和多个路径，不能只定义一个模糊的 `model_path`。
- hardware/NPU、scheduler、queue、affinity 等字段分别由哪个输入拥有。
- 两个输入同时提供且不一致时是失败、显式覆盖还是仅 warning。

在该优先级表确定前，不应实现配置 fingerprint。

#### 3. namespace 创建策略

第二步不修改 Kubernetes，第三步才允许 apply。仍需决定：

- `require-existing`：namespace 必须预先存在，第二步检查精确 RBAC；或
- `create-on-apply`：第二步把 Namespace 纳入不可变配置包，第三步在 consent
  后创建。

还需定义 `create-on-apply` 时 namespaced manifest 的 server-side dry-run
如何处理，因为 dry-run 创建 Namespace 不会为后续 dry-run 持久保留它。

#### 4. `config_fingerprint` 和配置复用

“配置与之前相同可省略”必须是可证明的复用，不是人工猜测。需要固定
fingerprint 至少覆盖：

- 规范化 user config、deploy profile 和显式覆盖；
- machine 固定路径映射、namespace/job-id、模型、镜像、NPU 和调度输入；
- Motor upstream deployer 版本；
- hostPath/PYTHONPATH/image 注入器版本；
- 最终 manifest 规范化摘要。

parity **内容摘要不进入结构配置 fingerprint**，否则每次代码修改都会迫使
重新生成相同 YAML。但复用时必须验证当前 parity 的 machine 和固定路径与
配置包一致，并生成新的 `deploy-config-ready` 绑定：

```text
旧 config bundle + 当前 parity
  → fingerprint/path compatibility check
  → 新 config run 引用 reused_config_bundle_id
```

仍需固定 config bundle 的不可变存储方式、完整性摘要和垃圾回收策略。

#### 5. “配置与代码一致”的精确定义

第二步证明：

- parity manifest 成功且引用的 machine/固定路径与本次配置相同；
- 最终 manifest 的 hostPath、volumeMount 和 `PYTHONPATH` 精确指向这些路径；
- 本次生成或复用的配置包没有引用 session/snapshot/旧 workspace 路径。

第二步不证明 Pod 已经 import 这些文件。第三步必须在运行 Pod 中记录三个包
的 `__file__` 等运行证据，并与当前 parity 路径对应。

是否还要求第二步重新读取远端内容摘要需讨论。默认建议复用
`parity-complete`，不在第二步复制 parity 的内容校验责任。

#### 6. 只读检查能力边界

第二步可以检查 secret/reference 存在、admission/server dry-run 和已有
只读节点证据，但不创建 probe Pod。因此：

- 镜像“实际可拉取”只能在第三步观察 image pull 结果。
- 模型“容器内实际可读”只能在第三步验证。
- 如果候选节点 hostPath 可见性没有现成只读证据，第二步应返回无法验证并按
  策略失败，不能假装已证明。

如果要求 apply 前强证明以上条件，就必须另行授权临时诊断资源；这会改变
“前两步不修改 Kubernetes”的已确认边界，需要单独讨论。

#### 7. 状态和 fail-closed 契约

需要统一单项检查、步骤结果和 CLI 退出码。目前文档与实现混用
`ok/error/warning/ready/failed/skipped/unknown`。

最低要求：

- 必需检查失败或无法验证时，步骤整体不得 ready。
- `skipped` 只允许用于明确不适用的可选检查，不能代替成功。
- 第三步区分 `planned/config-ready/applied/ready/code-verified/failed/stopped`
  等状态，只有 Ready、最小可访问和代码路径验证全部通过才能
  `deploy-complete`。

具体字段枚举需要在公共结果 schema 中固定。

#### 8. 日常代码更新与 restart

目标快速路径应为：

```text
remote-code-parity
  → motor-deploy-configure 复用已有 config bundle 并绑定当前 parity
  → motor-k8s-deploy restart
  → Ready + runtime source evidence
```

代码内容变化但路径和配置不变时，不重跑配置生成和 dry-run。是否要求重新
执行第一步 environment preflight，取决于 environment-ready 的有效期规则；
不得因为一个模糊的历史 `last_verified_at` 静默跳过。

### 已定义清楚、属于纯实现偏差的工作

下面无需再讨论责任归属，应直接按三步目标迁移。

#### 第一步：环境前置验证

- [ ] 新建独立 `motor-deploy-preflight` skill、脚本、结果 schema 和测试。
- [ ] 只消费 machine-ready、kube context 和 environment profile，不消费
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
- [ ] 将 namespace 精确 RBAC、NPU/affinity/候选节点、hostPath、模型和镜像
  的配置相关检查放在最终 manifest 生成之后。
- [ ] server-side dry-run 失败、本地缺 kubectl 或必要检查无法验证时
  fail closed，不以 `warning` 继续。
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
