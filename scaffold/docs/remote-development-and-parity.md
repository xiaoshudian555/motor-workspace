# 第一部分：本地工作区、远程目标与代码同步

本部分从“刚 clone 下来的本地仓库”开始，到“远端共享目录中已经存在可供
Motor Deploy 消费的目标代码”结束。

目标运行模式是一对一固定映射：

```text
一个本地 motor-workspace
  → 一台登记的远程入口机器
  → 一个固定的远端共享源码根目录
```

不为每次任务创建 session，不为每次同步创建新的源码路径，也不通过
motor-workspace 给 Motor 分配 namespace 或 job-id。

## 三个连续阶段

```text
1. 本地工作区可用
   repo-init
        ↓ 交付：本地三仓 workspace + workspace-ready 结果

2. 远程目标可用
   machine-management
        ↓ 交付：machine-ref + 固定远端源码映射 + machine-ready 结果

3. 远端代码就绪
   remote-code-parity
        ↓ 交付：固定远端代码目录 + parity manifest + 内容一致性证据
```

三个阶段是责任和交接顺序，不要求用户每次手工调用三个 skill。机器登记和
验证通常只在首次配置或环境变化后执行；日常循环主要是 parity → 配置包
兼容性检查/复用 → deploy restart。

## 1. 本地工作区可用

责任单元：`repo-init`

核心问题：

> 本地 Motor、vLLM、vLLM-Ascend 三仓是否已经具备继续开发和同步的条件？

### 1.1 本地工具和代码托管认证

属于 `repo-init`：

- 检查 Git、Python 和 GitHub CLI 等必要工具。
- 在缺少 `gh` 时提供安装或引导；实际安装需要用户同意。
- 检查 GitHub 登录状态。
- 处理工作区实际需要的 GitHub/GitCode 认证。

不属于 `repo-init`：

- SSH 登录远程 NPU 机器。
- kubeconfig 或 Kubernetes 认证。

### 1.2 三仓初始化

属于 `repo-init`：

- 初始化 `motor`、`vllm`、`vllm-ascend` 三个子模块。
- 检查三个源码目录和 gitlink 状态。
- 确认 `.gitmodules` 保持社区 upstream URL。

### 1.3 仓库拓扑和版本基线

属于 `repo-init`：

- 创建或确认 Fork。
- 配置各仓库的 `origin` / `upstream`。
- 选择 Motor、vLLM、vLLM-Ascend 的目标版本。
- 检查 vLLM 与 vLLM-Ascend 的兼容关系。
- 在用户确认后更新 gitlink。
- 将 `workspace.lock.yaml` 作为诊断记录校验或更新，而不是把它当作
  dirty workspace 的部署门禁。
- 报告本地未提交和未跟踪修改，但不要求工作区必须 clean。

### 1.4 workspace-ready 证明

属于 `repo-init`：

- 输出本地 workspace 根目录。
- 输出三个源码目录、HEAD/gitlink、dirty 状态和仓库拓扑。
- 生成 `workspace-ready` 结果。

本地运行记录可以有内部 workspace 标识，但它不进入远端源码路径，也不成为
parity 或 deploy 的必需用户概念。

不属于 `repo-init`：

- machine inventory。
- hardware profile、`mount_root` 和固定远端源码根。
- `base_image_ref`。
- kube context。
- 远端代码同步。

完成时交付：

```text
workspace-ref
local workspace root
motor / vllm / vllm-ascend local paths
HEAD / gitlink / dirty status
remote topology
workspace-ready result
```

## 2. 远程目标可用

责任单元：`machine-management`

核心问题：

> 指定远程机器是否可以作为远程开发和代码同步目标？

`machine-management` 只证明远程连接、固定源码位置和 parity 所需的基础
文件操作能力。它不证明本次 Motor 部署所需的 Kubernetes、MindCluster、
NPU 调度、模型或镜像条件已经满足。

### 2.1 机器登记

属于 `machine-management`：

- 添加、查看、更新和移除 machine inventory。
- 保存 SSH endpoint、用户、端口和凭据引用。
- 记录 `mount_root` 和固定 `remote_workspace_root`。
- 可以记录 kube context、hardware profile 等引用，供 Deploy 使用。

凭据内容只进入允许的本地未跟踪位置，不进入 tracked profile 或文档。

### 2.2 连接和必要 bootstrap

属于 `machine-management`：

- 验证 SSH 可连接。
- 检查远端基础 shell、文件和命令执行能力。
- 检查 parity 所需的基础工具，例如 `tar` 和内容摘要工具。
- 在用户明确同意时进行 SSH key/bootstrap 或必要修复。

不属于 `machine-management`：

- 安装开发态 Python 包。
- 构建或拉取本次开发镜像。

### 2.3 固定远端源码映射

属于 `machine-management`：

- 记录并解析 `mount_root`，默认 `/mnt`。
- 记录固定的 `remote_workspace_root`，默认
  `<mount_root>/motor-workspace`。
- 建立本地三仓到远端三目录的一对一映射。

默认映射：

```text
本地 motor/
  → /mnt/motor-workspace/motor

本地 vllm/
  → /mnt/motor-workspace/vllm

本地 vllm-ascend/
  → /mnt/motor-workspace/vllm-ascend
```

如果后续确实需要 python-overlay，它也使用固定目录：

```text
/mnt/motor-workspace/python-overlay
```

这些路径属于 workspace 与 machine 的固定绑定，不包含 workspace ID、
session ID、deploy run ID 或 validation run ID。

### 2.4 固定远端目录验证

属于 `machine-management`：

- 验证 `mount_root` 和 `remote_workspace_root` 的路径边界。
- 验证固定源码根可创建、可读写。
- 验证创建、读取、覆盖和清理测试文件的基础能力。

这里证明的是“可以把代码同步到固定远端位置”，不是“Motor Pod 已经能够
挂载该位置”。

### 2.5 与 Motor Deploy 三个步骤的边界

不属于 `machine-management`，而属于第二部分第一步
`motor-deploy-preflight`：

- 验证 kube context 和 Kubernetes API 可用。
- 检查基础读取权限。
- 检查 MindCluster、Volcano、AscendJob、PodGroup、device plugin 等基础
  组件、controller、scheduler 和 CRD。
- 检查集群是否报告环境 profile 要求的 NPU resource 类型。

不属于 `machine-management`，而属于第二部分第二步
`motor-deploy-configure`：

- 读取和验证本次 Motor user config。
- 检查本次 namespace 和资源操作所需的精确 RBAC。
- 检查本次配置所需的 NPU 数量、节点标签、affinity 和可调度条件。
- 根据本次部署的候选节点验证相同 `mount_root` 和内容可见。
- 检查本次模型路径、`base_image_ref`、registry 和 imagePullSecret。
- 运行 upstream deployer dry-run、manifest 校验和 Kubernetes
  server-side dry-run。

Pod Ready、容器内模型/挂载可用以及实际代码加载证明属于第二部分第三步
`motor-k8s-deploy`。

`machine-management` 可以记录 kube context、hardware profile 或 NPU
硬件观察结果，但这些只是引用或事实，不等于本次部署可调度。

### 2.6 machine-ready 证据

属于 `machine-management`：

- 汇总 SSH、远端命令执行、固定源码映射、目录读写和 parity 基础工具检查。
- 输出明确的通过、失败或无法验证项。
- 生成可被 parity 和 deploy 引用的 `machine-ready` 结果。

`machine ready` 不代表：

- kube context、RBAC、MindCluster、Volcano 或 CRD 已满足本次部署。
- NPU 已经能够按本次配置成功调度。
- 所有候选节点或 Pod 已经能看到固定共享目录。
- 模型路径已经准备好。
- `base_image_ref` 已经可运行。
- 本地代码已经同步。
- Motor namespace 或 Pod 已经创建。
- Motor 服务已经启动。

## 3. 远端代码就绪

责任单元：`remote-code-parity`

核心问题：

> 本地当前 dirty workspace 是否已经正确出现在 machine 的固定远端共享
> 目录，并且有内容一致性证据？

### 3.1 本地源状态采集

属于 `remote-code-parity`：

- 读取 Motor、vLLM、vLLM-Ascend 当前 HEAD。
- 采集 tracked 修改和未跟踪文件。
- 记录必要的状态摘要和排除项。

这里直接以本地当前工作区为源，不创建持久化 immutable snapshot。

### 3.2 固定目标解析和同步前检查

属于 `remote-code-parity`：

- 消费 `workspace-ready` 和 `machine-ready`。
- 从 machine 解析固定 `remote_workspace_root` 和三仓目标目录。
- 确认目标位于允许的 `mount_root` 下。
- 确认远端可达并且固定目标父目录可写。
- 在覆盖已有远端内容前取得用户 consent。

parity 不创建 session，也不生成新的远端源码路径。

### 3.3 单次代码同步

属于 `remote-code-parity`：

- 创建固定远端源码目录。
- 将本地三个 dirty tree 分别同步到对应固定目录。
- 同步明确属于开发态代码的固定 python-overlay。
- 避免把本地凭据、缓存和无关运行状态带到远端。

每次 parity 更新同一组固定目录，不保留按 session 或 run 划分的源码副本。

### 3.4 内容验证

属于 `remote-code-parity`：

- 验证远端目标文件集合和内容摘要。
- 验证共享目录中的内容与本地当前 workspace 一致。
- 明确报告无法覆盖的节点或文件。

这里证明“共享目录中的文件已经同步正确”。Pod 是否实际看到并导入这些文件，
由 `motor-k8s-deploy` 证明。

本次 Motor 候选节点是否都能在相同路径看到这些内容，依赖最终 deploy
profile、manifest、调度和节点选择，属于第二部分第二步配置准备与验证，
不属于 parity。

### 3.5 manifest 和下游交接

属于 `remote-code-parity`：

- 为本次同步生成 parity run ID。
- 生成同步 manifest。
- 记录本地源状态、固定远端目录、同步结果和验证证据。
- 输出可被 Deploy 明确引用的 parity run/manifest。

parity run ID 只用于记录和追溯，不参与远端源码路径。

manifest 是同步结果证据，不是 source snapshot、build context 或镜像输入。

不属于 `remote-code-parity`：

- `pip install` 或 editable install。
- 镜像构建。
- namespace 或 job-id 分配。
- 向 Pod 注入 `PYTHONPATH`。
- 创建、重启或删除 Pod。
- Pod 内 `__file__` 验证。
- Motor Deploy、OpenAI smoke、benchmark 或 profiling。

## Motor namespace 和运行名称的归属

Motor 的 `job_id` 来自 deploy `user_config.json`，由 Motor deployer用于
namespace 和服务配置。Motor 各角色的运行 `job-name` 由 upstream deployer
生成。

因此：

- `repo-init` 不分配 namespace/job-id。
- `machine-management` 不分配 namespace/job-id。
- `remote-code-parity` 不分配 namespace/job-id。
- Motor workspace Deploy 只消费和验证 user config，并在 apply 阶段按需
  确认 namespace 存在。

namespace、job-id 和 K8s 资源生命周期属于第二部分 Motor Deploy。

## 公共支撑能力

这些能力参与三个阶段，但不构成第四个业务阶段：

| 能力 | 归属 | 使用位置 |
|---|---|---|
| 远端 read/edit/bash/search/job/artifact | `.remote-dev/` | machine verify、parity 和临时远端排查 |
| machine 到 endpoint 的解析 | `.agents/lib/` adapter | machine、parity 及后续 deploy |
| 本地状态 | `.agents/lib/` + `.motor-workspace-local/` | workspace、machine 和各类 run |
| 公共结果契约 | `.agents/lib/` | 三个阶段的完成结果和错误结果 |
| consent/safety | 仓库规则 + `.agents/lib/` | 安装、bootstrap、覆盖同步和 cleanup |
| Agent 路由 | `AGENTS.md` + skill `SKILL.md` | 决定调用哪个工作流 |

`remote-toolbox` 只保留尚未迁移的兼容能力，不作为新的业务阶段。

## 第一部分的完成标准

第一部分只有在下面三个结果可以连续追溯时完成：

```text
workspace-ready
  → machine-ready
  → parity-complete
```

最终交付给 Motor Deploy 的是：

```text
machine-ref
remote workspace root
motor / vllm / vllm-ascend fixed remote paths
parity run/manifest ref
content consistency evidence
```

不交付 session-ref，也不为 Deploy 生成 namespace 或 job-id。

第二部分第一步 `motor-deploy-preflight` 先独立证明 K8s 与 MindCluster
基础环境可用，不读取本次 Motor 配置。第二步 `motor-deploy-configure` 再结合
deploy profile、Motor user config 和 parity manifest 生成或复用不可变
配置包并交付 `deploy-config-ready`。第三步 `motor-k8s-deploy` 只消费成功的
配置结果，执行实际部署和运行验收。

该边界对应的当前实现缺口见
[technical-debt.md](technical-debt.md#第一部分远程开发准备与代码同步)。

## 当前实现状态

parity/deploy 已绑定 machine 固定远端目录与独立 run 记录：

- 远端路径为 `/mnt/motor-workspace/{motor,vllm,vllm-ascend,python-overlay}`。
- parity 和 deploy CLI 使用 `--machine` 与 `--deploy-run-id`。
- namespace/job-id 来自 Motor `user_config.json`，不由 workspace 分配。

旧 `.motor-workspace-local/sessions/` 数据属于历史证据，不会被自动删除或改写。
