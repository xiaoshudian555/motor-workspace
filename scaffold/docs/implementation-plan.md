# Motor Workspace 3+3 实施计划（历史材料）

> **状态：历史参考。** 当前待办以 [`technical-debt.md`](technical-debt.md) 为准。
> 本文保留并行工作包与 VAWS 迁移记录，**不要**再作为 Agent 执行入口。

本文把 `technical-debt.md` 中已经明确的实现偏差拆成可并行工作包。目标不是让
各 Agent 自行补设计，而是在固定文件边界内迁移、修复和补测试。

六项定义冻结后的可执行工作单见
[`agent-work-orders.md`](agent-work-orders.md)。

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
4. 按下文已经冻结的六项定义实现公共 schema，不再自行扩展状态或配置来源；
5. 更新第一部分 SKILL、Agent shim 和文档；
6. 有真实机器时执行 `repo-init → machine-management → remote-code-parity`
   纵向验收；没有真实机器时只声明 fixture 验收。

## 六项已冻结定义

以下决策已经确认，不再留给实现 Agent 自行选择。

### 1. 公共结果和状态契约

每个被执行的检查只有四种结果：`ok`、`warning`、`error`、`unavailable`。

- `warning` 记录证据后继续，允许步骤最终 ready。
- `error` 或 `unavailable` 立即中断当前步骤，进程非零退出，不执行后续检查或
  下游步骤，也不得发布 ready。
- 人修复问题后重新运行，产生新的 run；不实现自动修复或断点续跑。
- 不适用的检查不执行，不用 `skipped` 或 `not_applicable` 冒充成功。
- 结果必须保存已完成检查、warnings、停止位置、错误和上游 run 引用。

### 2. 部署配置只来自 Motor 原生配置

Workspace 不提供 namespace、job-id、镜像、模型、NPU、scheduler、queue、
affinity 等字段的第二套 CLI override 或 deploy profile。

- 配置输入是 Motor upstream deployer 原生支持的 `user_config.json` 和
  `env.json`，通过其原生 `deploy.py --config_dir ...` 或等价的原生路径参数
  调用。
- Workspace 的命令参数只允许选择 machine/run/config 文件路径和操作，
  不得成为 Motor 字段的第二配置源。
- 第二步在 run-scoped staging 中复制原生配置，调用 upstream `--dry-run`，
  再对生成 manifest 做共享 hostPath、volumeMount 和 `PYTHONPATH` 注入。
- `workspace.lock.yaml` 只提供诊断信息，不覆盖 Motor 原生配置。

### 3. Namespace 策略

namespace 固定使用 `user_config.json` 中
`motor_deploy_config.job_id` 的 Motor 原生语义，并且必须预先存在。

- 第一步不读取 namespace。
- 第二步检查 namespace 存在，并验证本次 manifest 所需权限。
- 第三步不创建 namespace。
- 不实现 `create-on-apply`。

### 4. Environment-ready 的有效期

第一版不跨工作流复用 environment-ready。

- 每个新的 3+3 deploy/configure 工作流必须运行一次 preflight。
- 同一工作流内的后续步骤显式引用该 environment run。
- 不读取 `last_verified_at`，不实现 TTL、`expires_at` 或跨工作流命中。
- 结果仍记录 cluster identity、API server、kube context、组件版本和检查
  时间，供审计与未来扩展使用。

### 5. Config fingerprint、不可变 bundle 和代码绑定

结构配置和代码内容完全分开。

- fingerprint 覆盖 Motor 原生 `user_config.json`、`env.json`、machine 固定路径
  映射、upstream deployer 版本和 workspace manifest 注入器版本。
- parity 的代码内容摘要不进入 config fingerprint。
- 第二步只校验最终 manifest 使用当前 machine/parity 声明的固定路径，不重新
  判断或管理代码内容。
- bundle 另有覆盖最终 manifest 和证据的 `bundle_digest`，原子创建后不可
  修改。
- 配置结构未变化时复用旧 bundle；新的 config run 只重新绑定当前 parity
  路径引用。
- 第一版不自动清理 bundle。

### 6. Apply 前只读验证边界

apply 前不创建临时 Pod/Job，也不尝试证明镜像可拉取、模型在容器内可读或
候选节点 hostPath 实际可见。

- 第二步只做原生 deployer dry-run、manifest 结构/替换检查、namespace/RBAC
  检查和 Kubernetes server-side dry-run。
- 不把上述运行期条件登记为第二步的 `deferred` 检查；它们不属于
  deploy-config-ready 的完成条件。
- 第三步 apply 并等待 Ready。拉取、挂载、模型或调度问题导致无法 Ready 时，
  当前步骤报错并保留现场。
- 深入验证镜像、模型、hostPath 等属于失败后的 diagnosis，不是正常 apply
  之前的门禁。

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
