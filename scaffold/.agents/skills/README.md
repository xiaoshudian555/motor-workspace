# motor-workspace Skill 通览

本目录是 **repo-local Skill 的权威源**。Skill 是给 Agent 的操作合同：路由、授权边界、验收标准和故障出口。它不是脚本仓库，也不是第二套部署器。

日常约定：

- 先按 `name` / `description` 匹配；命中后必须读完整 `SKILL.md` 再执行。
- 部署类说法（拉起/启动/部署/重启/停止、部署前检查、换 wheel、故障注入）先走 `motor-deploy`，不要直接点原子 Skill。
- Claude Code 看到的 `.claude/skills/*/SKILL.md` 是生成 shim，改 Skill 只改本目录。
- 远端读写搜命令走 `.remote-dev` 的 `remote.*`，不要为连机器装 `paramiko` / `sshpass`。

当前 canonical Skill：**25 个**。

## 一张图看完

```text
仓库/机器准备
  repo-init → machine-management → remote-code-parity → motor-build-wheel

部署（dispatcher: motor-deploy）
  motor-config-edit → motor-deploy-preflight → motor-deploy-configure
  → 用户授权 → motor-k8s-deploy

部署后验证（互相不能冒充）
  motor-smoke          Coordinator /readiness
  motor-functional     推理/特性是否工作
  motor-smoke-suite    把上面串成冒烟验收
  motor-benchmark      打流出数
  motor-performance-analysis  性能归因（只读）
  motor-reliability    授权后的故障注入与恢复

失败诊断（dispatcher: motor-startup-diagnosis）
  motor-diagnosis  采证
    ├ motor-diagnosis-environment
    ├ motor-diagnosis-deployer
    ├ motor-diagnosis-config
    ├ motor-diagnosis-runtime-code
    └ motor-diagnosis-controller-recovery-terminate
```

## 按职责

### 仓库与远端底座

| Skill | 干什么 |
|---|---|
| `repo-init` | 初始化 clone：Git、gh、submodule、fork remote。不管机器和 K8s。 |
| `machine-management` | 登记/查看 NPU 入口机到 `.motor-workspace-local/machine-inventory.json`。只存 host/port 等元数据，不存密码密钥。 |
| `remote-toolbox` | 指向 `.remote-dev`：远端 read/edit/bash/search/job/artifact。临时上机器用这个，不要手搓 SSH。 |
| `remote-code-parity` | 把本地 dirty 源码同步到远端固定目录 `/mnt/motor-workspace/{motor,vllm,vllm-ascend}`。不是编镜像，也不 `pip install -e`。 |
| `agent-skill-evolution` | 把已证实的 Agent/Skill 决策错误沉淀成 lesson，必要时升到规则或测试。普通产品故障不走这里。 |

### 部署链（先 `motor-deploy`）

| Skill | 干什么 |
|---|---|
| `motor-deploy` | **总调度**。把「拉起服务 / 检查环境 / 换包 / 注入故障」分到下面的原子 Skill。本身不执行 deploy。 |
| `motor-config-edit` | 把自然语言意图翻成原生 `user_config.json` + `env.json`。不部署、不 dry-run。 |
| `motor-deploy-preflight` | 部署前只读检查：K8s/MindCluster CRD、节点、镜像可达性等。不改集群。 |
| `motor-image-distribution-check` | 查各节点本地是否已有指定镜像，评估 `ErrImagePull` 风险。 |
| `motor-deploy-configure` | 用上游 `deploy.py --dry-run` 校验原生配置和生成 YAML。 |
| `motor-k8s-deploy` | 真正 apply / 查看 / 重启 / 停止。走 Motor 原生 `deploy.py` / `delete.sh`。覆盖前要明确授权。 |
| `motor-build-wheel` | 在目标运行时 Docker 里打 Motor wheel（protobuf + Rust kv-conductor），供 Pod `boot.sh` 安装。 |

只读预检和 dry-run 不需要授权；parity 覆盖、改配置、apply、重启、停止、注入故障都要针对具体目标再授权一次。

### 部署后验证

| Skill | 干什么 | 不能当成 |
|---|---|---|
| `motor-smoke` | 最小就绪：Coordinator 管理口 `GET /readiness` 且 `ready=true`。Pod Ready 不算过。 | 功能/性能/可靠性证明 |
| `motor-functional` | 真发推理、看 metrics/tracing、验某个已部署特性。 | 性能或 RAS 证明 |
| `motor-smoke-suite` | 编排「状态 → readiness → 非流式/流式推理」冒烟；失败自动转只读诊断。 | 默认去打压测 |
| `motor-benchmark` | 对已部署服务跑 AISBench 打流，出 TTFT/TPOT/QPS。 | 瓶颈归因 |
| `motor-performance-analysis` | 只读归因：调度开销、P/D 是否失衡、问题在 Motor 还是 vLLM-Ascend。 | 部署或启动诊断 |
| `motor-reliability` | 授权后的 RAS 实验：Coordinator 主备、Decode 进程重拉、Prefill 掉卡隔离与恢复。 | 只读排障（那是 diagnosis） |

### 诊断

启动失败入口是 `motor-startup-diagnosis`，不是直接挑某个专项。

| Skill | 干什么 |
|---|---|
| `motor-startup-diagnosis` | **启动失败总入口**。先采证，再按证据分到环境 / deployer / 配置 / 运行时代码。只读，不授权重试或修集群。 |
| `motor-diagnosis` | 通用采证：kubectl get/describe/logs、deployer auto-log。不分类、不修复。 |
| `motor-diagnosis-environment` | 平台侧：API、RBAC、调度、NPU、镜像、存储、节点、网络。不是改配置，也不是修节点。 |
| `motor-diagnosis-deployer` | `deploy.py` 参数、模板、YAML 生成、apply 编排失败。 |
| `motor-diagnosis-config` | 原生配置无效、不一致，或 intent / YAML / ConfigMap / Pod 生效值漂移。 |
| `motor-diagnosis-runtime-code` | 进程已经起来之后的崩溃、挂起、注册失败、Motor/vLLM/Ascend 集成错误。 |
| `motor-diagnosis-controller-recovery-terminate` | 精度自动恢复把 P/D 实例杀掉这条专用路径，吃已采集日志。 |

诊断结论可以建议下一步，但修配置、重启、删资源、注入故障都要新的明确授权。

## 全量清单（按目录名）

| 目录 | 角色 |
|---|---|
| `agent-skill-evolution` | Skill 自身进化 |
| `machine-management` | 机器登记 |
| `motor-benchmark` | 压测出数 |
| `motor-build-wheel` | 打 Motor wheel |
| `motor-config-edit` | 写原生配置 |
| `motor-deploy` | 部署总调度 |
| `motor-deploy-configure` | dry-run 校验配置 |
| `motor-deploy-preflight` | 部署前只读环境检查 |
| `motor-diagnosis` | 采证 |
| `motor-diagnosis-config` | 配置域诊断 |
| `motor-diagnosis-controller-recovery-terminate` | 精度恢复 terminate 诊断 |
| `motor-diagnosis-deployer` | deployer 域诊断 |
| `motor-diagnosis-environment` | 平台环境域诊断 |
| `motor-diagnosis-runtime-code` | 运行时代码域诊断 |
| `motor-functional` | 功能/推理检查 |
| `motor-image-distribution-check` | 节点镜像是否到位 |
| `motor-k8s-deploy` | apply/启停/查看 |
| `motor-performance-analysis` | 性能归因 |
| `motor-reliability` | 故障注入与恢复验证 |
| `motor-smoke` | Coordinator readiness |
| `motor-smoke-suite` | 冒烟编排 |
| `motor-startup-diagnosis` | 启动失败总入口 |
| `remote-code-parity` | 本地→远端源码同步 |
| `remote-toolbox` | 远端原子工具入口 |
| `repo-init` | 仓库初始化 |

## 不是 Skill 的东西

| 路径 | 关系 |
|---|---|
| `scaffold/.remote-dev/` | 远端 MCP 工具实现。Skill 调用它，不替代它。 |
| `scaffold/bin/motorws` | 只做 parity backend，不是产品 CLI，也不再扩张成部署入口。 |
| `scaffold/.claude/skills/` | 由 `.remote-dev/tools/sync_claude_skills.py` 生成的 shim。 |
| `.motor-workspace-local/` | 本机 inventory 和运行时状态，untracked。 |

仓库根还有一份过期 shim：`.claude/skills/mindcluster-node-maintenance/`。它指向已经不在本目录的 canonical 源，**不要当有效 Skill 用**。节点摘除/复位见 `scaffold/docs/mindcluster-worker-node-rejoin.md`。
