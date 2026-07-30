# Motor Workspace 3+3 实施计划

本文把 `technical-debt.md` 中已经明确的实现偏差拆成可并行工作包。目标不是让
各 Agent 自行补设计，而是在固定文件边界内迁移、修复和补测试。

## 开工前基线

并行修改前先建立同一个 Git 基线：

1. review 并提交当前 3+3 设计文档；
2. 所有 Agent 从同一 commit 开工；
3. VAWS 参考版本固定为
   `maoxx241/vllm-ascend-workspace@4a952fcc2b6bce045ca2f5a472ea1af93af2858c`；
4. 较大代码迁移保留 VAWS 的 MIT license/attribution；
5. 每个 Agent 只修改分配给自己的文件。发现公共层必须变化时，先在交付报告中
   提出，不得顺手修改保留文件。

当前文档工作树未提交前，不要让其他 Agent 基于不同文档版本同时修改根文档。

## 第一阶段：不依赖六项待定定义的并行工作

### 工作包 A：`.remote-dev` 差异审计和测试恢复

目标：

- 以 Motor `.remote-dev` 为基线逐文件对比固定 VAWS commit；
- 修复 Motor 重构造成的测试变量和路径错误；
- 只迁移 VAWS 中明确的通用 bug fix，不恢复 managed
  `session_id/session_file/machine` selector；
- 证明 endpoint、read/write/edit/bash/search/job/artifact 契约仍成立。

文件所有权：

```text
scaffold/.remote-dev/**
```

禁止：

- 不修改 `scaffold/.agents/**`；
- 不用 VAWS 整目录覆盖 Motor 版本；
- 不增加 session/container/machine inventory 解析。

验收：

- `.remote-dev` 测试完整收集并通过；
- 形成逐文件差异表，说明每项差异是 Motor 定制、VAWS 修复或待集成问题；
- `git diff --check` 通过。

### 工作包 B：repo-init 的 VAWS 通用能力迁移

目标：

- 迁移 Git、gh、GitHub auth、submodule 和 remote topology 的 probe；
- 支持 workspace、Motor、vLLM、vLLM-Ascend 四仓视图；
- 保守设置 `origin/upstream`，保留额外 remotes；
- 生成可验证的 `workspace-ready` 内容，但暂不发明新的公共 result envelope。

文件所有权：

```text
scaffold/.agents/skills/repo-init/**
scaffold/tests/test_repo_init*.py
```

禁止：

- 不修改 `scaffold/.agents/lib/mws_result.py`、`mws_run_state.py`；
- 不迁移 VAWS 的 machine username/profile、容器或 vLLM pin 专属策略；
- probe 必须只读；任何 remote 改写必须由 apply 路径和明确 consent 执行。

验收：

- 未安装 gh、未认证、submodule 未初始化、remote 冲突、额外 remote 等 fixture
  均有测试；
- probe 无写操作；
- apply 幂等且不会删除未知 remote；
- progress 在 stderr，最终结果在 stdout。

### 工作包 C：machine-management 边界和状态安全

目标：

- 从 VAWS 迁移 inventory 原子写、文件锁、并发更新和输入校验；
- 保留 Motor 的“登记现有 SSH 机器 + kube context + mount root + 固定远端
  workspace”模型；
- `verify` 只验证机器和远程开发底座，不验证 Kubernetes/MindCluster；
- 把当前 K8s/MindCluster 检查列成可迁移清单，暂不创建新 preflight skill。

文件所有权：

```text
scaffold/.agents/skills/machine-management/**
scaffold/.agents/lib/mws_machine_target.py
scaffold/.agents/lib/mws_validate.py
scaffold/.agents/lib/mws_local_state.py
scaffold/.agents/lib/mws_state.py
scaffold/tests/test_machine*.py
```

禁止：

- 不创建 Docker/container/sshd，不引入 session、端口或 NPU lease；
- 不修改公共 `mws_result.py`、`mws_run_state.py`；
- 不把 `last_verified_at` 当作下游 machine-ready 证据；
- 不修改第二部分 deploy skill。

验收：

- add/verify/repair/remove 的状态和副作用边界有 fixture；
- inventory 并发写不会丢记录或产生半文件；
- verify 不修复、不修改 K8s；
- machine-ready 包含固定 endpoint、mount root、remote workspace 和检查证据。

### 工作包 D：固定目录 remote-code-parity

目标：

- 保持本地 dirty tree 为 source of truth；
- 同步到机器共享 mount 下三个固定源码目录；
- 补齐 manifest、内容摘要、锁、明确覆盖 consent、no-change fast path 和
  post-sync proof；
- parity 只消费 machine-ready，不执行 deploy/preflight/configure。

文件所有权：

```text
scaffold/.agents/skills/remote-code-parity/**
scaffold/.agents/lib/mws_parity.py
scaffold/.agents/lib/mws_transport.py
scaffold/tests/test_parity_sync.py
scaffold/tests/test_parity*.py
```

禁止：

- 不引入 VAWS session/snapshot/synthetic ref/container cache；
- 不执行 pip install、editable install、native rebuild；
- 不自动伪造 `--approved-overwrite`；
- 不修改 deploy skill 或公共 run/result schema。

验收：

- clean、dirty、untracked、delete、no-change、远端漂移、部分失败和并发同步有
  fixture；
- manifest 能证明三个仓库的本地输入、远端固定路径和远端落盘摘要；
- 未授权覆盖 fail closed；
- 中途失败不会发布成功的 parity-complete。

## 第一阶段集成

四个工作包完成后由单独的集成 Agent 执行：

1. 按文件所有权合并，拒绝越界修改；
2. 运行 `.remote-dev` 和 `scaffold/tests` 全量测试；
3. 收敛 `.remote-dev` 与 `mws_transport.py` 的分层，不创建第三套 transport；
4. 汇总各工作包提出的公共 schema 需求，但在六项定义冻结前不实现猜测；
5. 更新第一部分 SKILL、Agent shim 和文档；
6. 有真实机器时执行 `repo-init → machine-management → remote-code-parity`
   纵向验收；没有真实机器时只声明 fixture 验收。

## 六项待冻结定义

`technical-debt.md` 使用八个小节展开问题；实施时合并为以下六个决策主题。
其中“配置与代码一致”并入配置 bundle，“日常 restart”分别由 environment
有效期和 bundle 复用规则决定。

### 1. 公共结果和状态契约

决定每一步如何表达成功、失败、不可验证、上游引用和证据，以及 CLI 用什么
退出码。没有统一契约，下游只能猜测上一步的 `warning` 或 `ready` 是否可信。

建议：

- 单项检查只使用 `pass`、`fail`、`not_applicable`；
- 运行期才能检查的项目单独记录为 `deferred_to_deploy`，不伪装成已验证；
- 步骤完成结果使用 `ready` 或 `failed`；
- apply 生命周期另设 `planned/applied/ready/code_verified/failed/stopped`；
- 任一 required check 失败或无法完成时，不得发布 ready。

### 2. 配置输入优先级和冲突规则

决定 namespace、job-id、镜像、模型角色、NPU、scheduler、queue、affinity 等
字段由 CLI、Motor user config、deploy profile 或其他文件中的哪一层拥有。
如果不固定，同一输入会因 Agent 的合并顺序不同生成不同 manifest。

建议优先级：

```text
显式 CLI override
  > Motor user config
  > deploy profile
  > 代码内明确默认值
```

`workspace.lock.yaml` 只提供诊断/兼容性断言，不静默成为部署配置。所有 override
必须进入 normalized effective config 和证据；同一优先级来源冲突直接失败。

### 3. Namespace 策略

决定目标 namespace 必须提前存在，还是由第三步 apply 时创建。第二步不修改
Kubernetes，所以 create-on-apply 会使 namespace RBAC 和 server-side dry-run
更复杂。

建议 MVP 使用 `require-existing`：第二步验证 namespace 和必要权限；不存在就
fail closed。后续再把 `create-on-apply` 作为显式模式加入。

### 4. Environment-ready 的有效期

决定一次 K8s/MindCluster 前置验证能否复用，以及 cluster、context、CRD、
controller 或 device plugin 变化后何时失效。它同时决定日常 restart 是否要
重跑 preflight。

建议第一版不跨工作流复用：每次新的 configure 工作流都要求一个新
environment run；同一工作流内可引用它。结果仍记录 cluster UID、API server、
context、environment profile digest 和组件版本，后续再安全加入 TTL。

### 5. Config fingerprint、不可变 bundle 和代码绑定

决定怎样证明“配置和上一次相同”，以及复用旧 YAML 时怎样绑定本次 parity。
结构配置和代码内容必须分开，否则每次改 Python 都会无意义地重新 render。

建议：

- fingerprint 覆盖 normalized effective config、machine 固定路径、deployer
  版本、注入器版本和规范化 manifest；
- parity 内容摘要不进入结构 fingerprint；
- bundle 以内容摘要命名，原子创建后只读，包含 manifest、effective config、
  输入/工具版本和验证证据；
- 代码变化但路径兼容时，创建新的 config run 引用旧 bundle 和新 parity run；
- bundle 清理只删除没有任何 run 引用且超过保留期的对象。

### 6. Apply 前只读验证边界

决定第二步是否允许创建临时 Pod/Job 来证明镜像能拉取、模型可读和 hostPath
真实可见。如果允许，它就不再是纯配置步骤；如果不允许，这些运行期事实只能在
第三步证明。

建议保持前两步零 Kubernetes 写入：

- 第二步负责 schema、引用存在性、RBAC、admission/server-side dry-run、
  manifest 路径映射等只读/结构证明；
- 无法在只读条件下证明的 image pull、容器内模型读取和实际 import 路径，
  明确标记 `deferred_to_deploy`；
- 第三步 apply 后必须验证这些项目，失败则不得发布 deploy-complete；
- 不引入隐式诊断 Pod。未来如有需要，另设带明确 consent 的诊断操作。

## 第二阶段：六项定义冻结后的实现顺序

### 阶段 2.1：公共契约

单独实现并先合入：

- 公共 result envelope、check schema、状态和退出码；
- workspace/machine/parity/environment/config/deploy 六类独立 run；
- 显式上游 run 引用、artifact digest 和过期判定；
- config bundle 的目录、不可变写入和引用规则。

主要保留文件：

```text
scaffold/.agents/lib/mws_result.py
scaffold/.agents/lib/mws_run_state.py
scaffold/docs/**
README.md
AGENTS.md
```

### 阶段 2.2：第二部分三个步骤并行实现

公共契约合入后再拆三个 Agent：

1. `motor-deploy-preflight`
   - 只验证 K8s API、cluster identity、MindCluster/Volcano/CRD/controller/
     device plugin；
   - 产出 `deploy-environment-ready`。
2. `motor-deploy-configure`
   - 消费 environment-ready 和 parity-complete；
   - 负责有效配置、render、替换、upstream dry-run、server-side dry-run、
     immutable bundle、fingerprint 和复用；
   - 产出 `deploy-config-ready`。
3. `motor-k8s-deploy`
   - 只消费不可变 config bundle；
   - 负责 consent、apply、等待 Ready、最小服务验证和 Pod 内三个包的加载路径
     证明；
   - 产出 `deploy-complete`。

三个 Agent 不互改对方 skill。公共库新增需求提交给集成 Agent，避免各自扩展
不兼容的 result/run 格式。

## 最终验收

必须覆盖：

- 缺少、过期或指向错误 machine 的上游结果；
- parity 路径/摘要不匹配；
- environment-ready 失效；
- config 输入冲突、fingerprint 不匹配、bundle 被修改；
- local/upstream/server-side dry-run 失败；
- apply、调度、镜像拉取和 Ready 失败；
- Pod 实际加载路径与 parity 固定路径不一致；
- 相同配置复用 bundle、代码更新后重新绑定 parity 并 restart 的快速路径。

只有真实集群上的关键资源 Ready、最小服务可访问和运行代码路径证明都成功，
才能声明 3+3 流程完成；fixture 通过不能替代真实环境验收。
