# Motor Workspace 选择性运行包替换设计

> 状态：4-Phase Coding Workflow Phase 0 + Phase 1 讨论稿  
> 日期：2026-08-10  
> 范围：Motor、vLLM、vllm-ascend 的 snapshot、选择性构建、Pod 启动安装与部署证据  
> 参考实现：`vllm-ascend-workspace`（VAWS）`remote-code-parity`

## 1. 结论先行

VAWS 的核心逻辑可以平移：snapshot、changed-path 判断、HEAD drift、依赖级联、
选择性重建、安装验证和状态记录都应保留。

需要适配的不是业务逻辑，而是执行位置：

```text
VAWS
  长期 container 内：pip install -e source → 同一个 container 启服务

Motor workspace
  build container 内：source → wheel
  新 Pod 内：boot.sh → pip install wheel → 启动对应角色
```

因此本设计不是重新发明包替换机制，而是给 VAWS 的选择与状态逻辑换一个
Kubernetes/Pod 执行器。

## 2. 目标行为

### 2.1 组件选择矩阵

| Motor 变化 | vLLM 变化 | vllm-ascend 变化 | 需要重新构建 | 运行时替换 |
|---|---|---|---|---|
| 否 | 否 | 否 | 无 | 沿用当前 active package policy |
| 是 | 否 | 否 | Motor | Motor |
| 否 | 否 | 是 | vllm-ascend | vllm-ascend |
| 否 | 是 | 任意 | vLLM + vllm-ascend | vLLM + vllm-ascend |
| 是 | 是 | 任意 | Motor + vLLM + vllm-ascend | 三者 |
| 是 | 否 | 是 | Motor + vllm-ascend | 两者 |

vLLM 变化级联 vllm-ascend，保持 VAWS 现有规则。多个条件同时命中时，每个
组件只构建一次。

### 2.2 两类状态不能混为一谈

必须区分：

- `rebuild_components`：相对上次 snapshot，本次哪些 wheel 需要重新构建；
- `active_override_components`：新 Pod 启动时，哪些组件仍应安装本地 wheel。

VAWS 的长期 container 天然保留已安装 package，所以没有新 delta 时只需跳过
reinstall。Motor Pod 每次新建，不能仅凭本次 `changed_paths` 决定安装项；否则
第一次构建 Motor、第二次无新变化时会错误回到镜像 Motor。

推荐把 `active_override_components` 作为显式、可审计的运行包状态写入
`runtime-wheel-build` run，并由 configure bundle 固化。切回镜像必须是显式
动作，不得因为“本次没有新 delta”隐式发生。

## 3. VAWS 机制事实

VAWS `remote-code-parity` 的真实链路：

1. 为 workspace、vLLM、vllm-ascend 生成 synthetic snapshot；
2. 用 repo 的 `changed_paths` 匹配 reinstall patterns；
3. 用 container runtime state 的 `last_head_commits` 检测 HEAD drift；
4. vLLM reinstall 时级联 vllm-ascend；
5. 只卸载本次确实要重新安装的 package；
6. 在长期 container 中执行 `pip install --no-deps -e . --no-build-isolation`；
7. 执行 `import torch_npu, vllm, vllm_ascend` 和 dependency check；
8. 写 container marker 和 runtime state。

关键证据：

- `reinstall_required_for_repo`：VAWS
  `.agents/skills/remote-code-parity/scripts/remote_code_parity.py:799`
- vLLM editable install：同文件 `runtime_install_step_script:920-927`
- vllm-ascend editable install：同文件 `runtime_install_step_script:936-941`
- vLLM → vllm-ascend 级联：同文件 `run_sync:1338-1339`
- 选择性 uninstall/install：同文件 `run_sync:1655-1713`
- import/dependency proof：同文件 `run_sync:1714-1729`

### 3.1 可直接平移

- component changed-path patterns；
- HEAD drift；
- vLLM → vllm-ascend dependency cascade；
- no-change fast path；
- force reinstall；
- 只处理命中的 package；
- build/install progress；
- fail closed；
- import/dependency proof；
- runtime package state。

### 3.2 需要执行器适配

| VAWS | Motor workspace |
|---|---|
| 长期 container 是 build target 也是 runtime target | build container 与 Pod 是两个文件系统 |
| editable install 让 Python 文件直接生效 | wheel 模式下 Python 文件变化也要重建 wheel |
| container marker 表示 package 已安装 | build run + bundle policy 表示新 Pod 应安装哪些 wheel |
| materialize 后立即 install | parity → build wheels → configure → rollout |
| 一份 container Python 环境 | 每个 Pod 在启动时安装到自己的 site-packages |

## 4. Motor Workspace 当前事实

### 4.1 Snapshot 已覆盖三个仓

`mws_parity.build_source_manifest` 已遍历 `REPO_DIRS`，manifest 已包含 Motor、
vLLM、vllm-ascend 的 HEAD、dirty 状态和 working-tree content digest。

`mws_parity.build_synthetic_snapshot` 已返回：

- synthetic commit；
- tree；
- changed paths。

当前缺口是 `sync_workspace_to_remote` 只把 `changed_files` 数量写入 `synced`，
没有把 `changed_paths`、source HEAD 和 component decision 暴露给下游 run。

### 4.2 Motor wheel 链路已经成立

`mws_build.build_motor_wheel_in_docker` 当前执行：

1. 以目标 `base_image_ref` 创建 build container；
2. 只读挂载远端固定 Motor source；
3. 在容器副本中执行 `build.sh`；
4. 复制唯一 `motor-*.whl` 到共享盘；
5. 写 SHA256；
6. 把 wheel 目录写入远端固定 Motor `boot.sh`。

Pod 中 `boot.sh` 执行 `pip install --no-deps --force-reinstall --no-index`，安装
失败立即停止角色启动。

### 4.3 当前证据链只认识 Motor wheel

- run kind 只有 `motor-wheel-build`；
- configure CLI 只接受 `--motor-wheel-build-run-id`；
- config fingerprint 只包含 `motor_wheel_dir`；
- bundle policy 只有 `image` / `motor-wheel`；
- deploy runtime proof 只根据一个任意 Ready Pod 导入三个 module；
- import path 只能证明来自 site/dist-packages，不能证明具体安装了哪个 wheel。

## 5. 推荐总体设计

### 5.1 调用链

```text
parity_sync.py
  → mws_parity.sync_workspace_fanout
  → 每仓 snapshot evidence
  → package selection plan
       Motor delta              → motor
       vLLM delta               → vllm + vllm-ascend
       vllm-ascend delta        → vllm-ascend
       explicit reset/force     → 调整 active/rebuild 集合
  → runtime-package-build
       先构建全部 selected wheels
       全部成功后一次性更新 boot.sh package block
       写 runtime-wheel-build run
  → motor-deploy-configure
       消费 parity run + runtime-wheel-build run
       固化 package policy/artifact digests
       upstream dry-run 生成含目标 boot.sh 的 ConfigMap
  → motor-k8s-deploy
       apply + rollout
       engine Pod：校验 vllm/vllm-ascend
       control/engine Pod：校验 Motor
       import/dependency/installed-wheel proof
```

### 5.2 为什么推荐一个聚合 build run

不建议三个 component build 各自立即修改远端 `boot.sh`。如果 Motor 已构建并写入
boot，而 vllm-ascend 随后构建失败，会留下半完成状态。

推荐一个 `runtime-wheel-build` transaction：

1. 先计算完整 component plan；
2. 所有 wheel 分别构建到共享盘；
3. 校验每个目录恰好一个目标 wheel并记录 SHA256；
4. 所有构建成功后，一次性更新 `boot.sh` 的完整 package block；
5. 任一构建失败，不更新 boot package block，不产出 ready run。

现有 `motor-build-wheel` 可保留为强制只构建 Motor 的兼容入口，但标准 deploy
链路消费聚合 `runtime-wheel-build` run。

### 5.3 Artifact key

当前 Motor artifact 路径按 Git HEAD SHA 命名。dirty working tree 在同一 HEAD 下
会覆盖同一路径，不适合作为可复用 bundle 的稳定身份。

新路径应使用 parity snapshot/content digest：

```text
<remote_workspace_root>/runtime-wheel-builds/
  motor/<motor-snapshot-or-content-digest>/dist/
  vllm/<vllm-snapshot-or-content-digest>/dist/
  vllm-ascend/<ascend-snapshot-or-content-digest>/dist/
```

build run 同时记录 source HEAD，便于阅读；真正的产物身份使用 snapshot/content
digest。

### 5.4 Build 命令适配

Motor 保持 `build.sh`。

vLLM 沿用 VAWS 构建环境：

```bash
export VLLM_TARGET_DEVICE=empty
export TORCH_DEVICE_BACKEND_AUTOLOAD=0
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir /out .
```

vllm-ascend 沿用 VAWS 的 runtime env、Ascend env 和并行度设置：

```bash
export MAX_JOBS="${MAX_JOBS:-128}"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-$MAX_JOBS}"
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir /out .
```

上述 wheel 命令是待真机验证方案。VAWS 已证明相同运行镜像能执行 editable
build，但没有直接证明 Motor 的具体 `base_image_ref` 能完成 wheel build。

### 5.5 Pod 安装范围

Motor wheel 应保持所有相关角色安装。

vLLM/vllm-ascend wheel 只应在 engine 类角色安装：

- `SINGLE_CONTAINER`
- `encode`
- `prefill`
- `decode`
- `union`

Controller、Coordinator、KV store 等角色不需要为 vLLM wheel 承担重复编译或
安装风险。`boot.sh` 使用角色判断选择 package block，但所有路径仍来自同一个
不可变 build run。

### 5.6 Runtime proof

当前 `_pick_runtime_pod` 返回第一个 Ready Pod，不能保证它是 engine Pod。

目标 proof 至少分两类：

- Motor：在一个实际使用 Motor 的 control/engine Pod 中验证；
- vLLM、vllm-ascend：必须在 engine Pod 中验证。

仅检查 `/site-packages/` 不足以区分 image wheel 与 workspace wheel。建议
`boot.sh` 安装成功后写 Pod-local marker，包含：

- component；
- wheel basename；
- wheel SHA256；
- install timestamp。

deploy proof 读取 marker，并与 bundle 中的 artifact SHA256 对比；image component
则要求 marker 不存在且 module 来自 site/dist-packages。

## 6. Phase 1 文件职责表

### 6.1 必改核心文件

| # | 文件 | 原本职责 | 入口/调用关系 | 为什么改这里 |
|---|---|---|---|---|
| 1 | `scaffold/.agents/lib/mws_parity.py` | 三仓 dirty tree snapshot、传输和远端一致性证明 | `build_source_manifest`、`build_synthetic_snapshot`、`sync_workspace_to_remote` | 唯一掌握每仓 snapshot/changed paths 的位置；必须输出 component evidence |
| 2 | `scaffold/.agents/skills/remote-code-parity/scripts/parity_sync.py` | 把 parity manifest 包装为 `parity-complete` run | `main` → `sync_workspace_fanout` | 下游只消费显式 run；需把 repo changes/component plan 写入 run |
| 3 | `scaffold/.agents/lib/mws_build.py` | 当前 Motor wheel 构建、artifact 和 boot path patch | `build_motor_wheel_in_docker` | 复用 Docker/build/artifact 公共逻辑；增加 vLLM/ascend builder并把 build 与 boot commit 分离 |
| 4 | `scaffold/.agents/lib/mws_run_state.py` | run kind、目录和 upstream ref 校验 | `RunKind`、`RUN_KINDS`、`RUN_DIRS` | 注册聚合 `runtime-wheel-build` 证据类型 |
| 5 | `sources/motor/examples/deployer/startup/boot.sh` | 所有 Pod 角色的统一启动入口 | ConfigMap 挂载后由 workload source | 唯一能在每个新 Pod 启动前安装选定 wheel 的位置 |
| 6 | `scaffold/.agents/lib/mws_deploy.py` | bundle fingerprint/metadata、apply backend、runtime proof | `compute_config_fingerprint`、`configure_deploy_bundle`、`collect_runtime_code_paths` | package policy 和 wheel digests 必须进入不可变 bundle；proof 需角色化 |
| 7 | `scaffold/.agents/skills/motor-deploy-configure/scripts/deploy_configure.py` | 消费显式 upstream runs 并产出 bundle | `main` | 由单 Motor run 改为消费聚合 runtime build run |
| 8 | `scaffold/.agents/skills/motor-k8s-deploy/scripts/deploy_apply.py` | apply、rollout、runtime code proof | `main` | 按 bundle policy 验证 engine/control Pod 和 wheel markers |
| 9 | `scaffold/.agents/skills/motor-k8s-deploy/scripts/deploy_status.py` | 已部署状态复验 | `main` | status 必须使用与 apply 相同的 package proof |
| 10 | `scaffold/.agents/skills/motor-k8s-deploy/scripts/deploy_restart.py` | 重启既有 deploy workload并复验证据 | `main` | restart 需沿用 bundle 中 active wheels，不能仅看本次 delta |

### 6.2 新增/扩展 Skill package

| # | 文件 | 原本职责 | 为什么改这里 |
|---|---|---|---|
| 11 | `scaffold/.agents/skills/runtime-package-build/SKILL.md`（新增） | 无 | 标准选择性 wheel build 原子阶段，承接 VAWS runtime-install 语义 |
| 12 | `scaffold/.agents/skills/runtime-package-build/scripts/build_runtime_packages.py`（新增） | 无 | 消费 parity run、计算 rebuild/active 集合、事务式构建并提交 boot block |
| 13 | `scaffold/.agents/skills/motor-deploy/SKILL.md` | 薄部署路由 | 在 full deploy 中插入 runtime-package-build 阶段 |
| 14 | `scaffold/.agents/skills/motor-deploy-configure/SKILL.md` | configure 契约 | 说明聚合 build run 和 bundle policy |
| 15 | `scaffold/.agents/skills/motor-k8s-deploy/SKILL.md` | apply/status/restart 契约 | 说明角色化 runtime proof 和 active wheel 语义 |

现有 `motor-build-wheel` package 保留，内部调用共享 Motor builder；不再作为标准
full deploy 的组合编排入口。

### 6.3 测试文件

| 文件 | 主要新增覆盖 |
|---|---|
| `scaffold/tests/test_parity_sync.py` | 三仓 changed paths、HEAD/snapshot evidence、no-change fast path |
| `scaffold/tests/test_build_wheel.py` | 三类 wheel 命令、artifact digest、事务式 boot commit、部分失败不提交 |
| `scaffold/tests/test_mws_run_state_contract.py` | `runtime-wheel-build` run kind/upstream refs |
| `scaffold/tests/test_deploy_runtime.py` | component matrix、角色化 Pod 选择、marker/digest proof |
| `scaffold/tests/test_deploy_apply.py` | bundle policy 进入 apply run |
| `scaffold/tests/test_deploy_restart_parity.py` | 无新 delta 时沿用 active wheel，而非切回 image |

## 7. 改什么 / 不动什么

### 改什么

改 snapshot evidence 的输出、运行包选择器、三类 wheel 构建、事务式 boot package
block、聚合 build run、bundle package policy、角色化 runtime proof。所有变化围绕
“哪些包应构建、哪些包应在新 Pod 中安装、如何证明安装的是目标 artifact”。

### 不动什么

- 不改 Motor deployer 的 P/D controller、Coordinator 或资源模型；
- 不实现新的 serving controller；
- 不恢复源码 `PYTHONPATH`；
- 不把 VAWS Docker session/container identity 搬进来；
- 不修改 `sources/vllm` 或 `sources/vllm-ascend` 业务源码；
- 不改 machine-management、preflight、functional、diagnosis；
- 不让 parity 阶段直接 apply Kubernetes；
- 不在 build 未全部成功时修改生效中的 boot package block。

## 8. 最小修改方案与推荐方案

### 最小方案

- parity 暴露 changed paths；
- 在现有 `motor-build-wheel` 内增加 vLLM/vllm-ascend 参数；
- 依次构建并修改 boot；
- configure 增加三个 wheel dir。

问题：中途失败可能留下半完成 boot 状态，run kind 语义失真，active override 状态
也没有可靠归属，不建议。

### 推荐方案

- 新增一个聚合 `runtime-package-build` stage；
- component builder 共享 `mws_build.py`；
- 所有 artifact 成功后一次性提交 boot package block；
- 一个 run 固化 active/rebuild component、artifact path/digest 和 package policy；
- configure 只消费这一个 run。

推荐方案比最小方案多约 1 个 skill package和 1 个 run kind，但状态更简单，失败
边界也与 VAWS 的单次 runtime-install transaction 一致。

## 9. 改动量估算

| 类别 | 文件数 | 预计改动 |
|---|---:|---:|
| parity evidence/selection | 2–3 | 80–140 行 |
| shared builders + aggregate skill | 4–6 | 260–420 行 |
| boot/configure/deploy proof | 5–7 | 180–300 行 |
| tests | 5–7 | 300–500 行 |
| Skill/docs | 5–7 | 120–220 行 |
| 合计 | 15–22 | 约 940–1,580 行 |

该估算高于 Phase 0 的初估，主要新增量来自：vLLM 也进入替换范围、active override
状态不能等同 changed delta，以及 runtime proof 必须区分 engine/control Pod。

## 10. 动态生效与刷新判断

本功能不是配置热更新：

- changed paths 在 parity 时计算；
- wheel 在 build stage 生成；
- package policy 在 configure 时固化；
- 新 Pod source ConfigMap 中的 `boot.sh` 后安装；
- 已运行 Pod 不自动感知新 wheel。

因此代码更新完整链路是：

```text
parity → runtime-package-build → configure → apply/rollout
```

单独 `deploy_restart --skip-parity` 只能重用原 bundle 的 active wheels；不能引入
新 source snapshot。

## 11. 验证计划与通过标准

### 11.1 本地测试

1. selection matrix 全组合；
2. vLLM delta 级联 vllm-ascend；
3. docs/tests-only 不 rebuild；
4. active override 在无新 delta 时保持；
5. 显式 reset 后回 image；
6. 三类 wheel 唯一产物和 SHA256；
7. 任一 builder 失败时 boot block 不变；
8. engine/control role 安装范围；
9. bundle fingerprint 包含 package policy + artifact digests；
10. runtime marker 与 bundle digest 匹配。

通过标准：相关测试与 `scaffold/tests` 全量通过，Skill package validation 通过，
`git diff --check` 通过。

### 11.2 195 真机验证

至少执行：

1. image-only；
2. Motor-only；
3. vllm-ascend-only；
4. vLLM change，确认 vLLM + vllm-ascend cascade；
5. Motor + vllm-ascend；
6. 无新 delta 重部署，确认沿用 active wheel；
7. 显式 reset-to-image。

每个 case 检查 build artifact、boot 日志、Pod-local marker、module path、import
smoke、Coordinator readiness。

## 12. 资料同步范围

- `runtime-package-build` 整个 Skill package；
- `motor-deploy` 权威源与 workspace mirror；
- `remote-code-parity`、`motor-deploy-configure`、`motor-k8s-deploy`；
- `scaffold/docs/technical-debt.md`；
- Phase 3 维护笔记；
- 若复制 VAWS 非小段实现，保留 MIT attribution/参考提交。

## 13. 证据分级

### 已验证

- Motor workspace 已为三个 source repo 生成 manifest：
  `mws_parity.py:build_source_manifest`。
- synthetic snapshot 已计算 `changed_paths`：
  `mws_parity.py:build_synthetic_snapshot`。
- 下游 parity run 当前没有暴露 changed paths：
  `mws_parity.py:sync_workspace_to_remote`、`parity_sync.py:main`。
- VAWS 使用 changed path pattern + HEAD drift + vLLM cascade：
  `remote_code_parity.py:reinstall_required_for_repo`、`run_sync`。
- VAWS 在同一个长期 container 中执行 editable install：
  `remote_code_parity.py:runtime_install_step_script`。
- Motor wheel 由 runtime-based build container 产出，Pod boot 时安装：
  `mws_build.py:build_motor_wheel_in_docker`、Motor `startup/boot.sh`。
- Motor deployer 把 `boot.sh` 放入 `motor-config` ConfigMap：
  Motor `lib/generator/k8s_utils.py:create_motor_config_configmap`。
- 当前 runtime proof 只选择第一个 Ready Pod：
  `mws_deploy.py:_pick_runtime_pod`、`collect_runtime_code_paths`。

### 合理推断

- Motor base image大概率具备 vLLM/vllm-ascend build 所需工具链：VAWS 已在配对
  runtime container 中完成两者 editable build；但 Motor 的具体 image ref 尚未真机
  执行 wheel build。
- vLLM wheel 替换应级联 vllm-ascend：VAWS 已采用该规则，且 vllm-ascend 依赖
  vLLM 内部接口；wheel 执行器不改变依赖关系。
- vLLM/vllm-ascend 只需安装到 engine roles：从 Motor role 分工和 import 使用推断，
  仍需用真实 manifests/Pod import 行为验证。

### 待确认

1. active override 的显式退出接口：只支持“全部回 image”，还是支持按 component
   reset？
2. force 接口沿用 VAWS 的 `--force-reinstall`（全部组件），还是允许
   `--force-component`？
3. vllm-ascend wheel 是否需要按机器 `SOC_VERSION` 分 artifact；如何从 profile/
   runtime env 解析该值？
4. Motor 运行镜像是否包含 `pip wheel` 所需 build frontend、CMake、编译器和完整
   Ascend headers；需在 195 实测。
5. Pod-local marker 是否足够，还是还要收集 pip install 完整日志作为 deploy
   artifact？

## 14. 搜索边界

已检查：

- VAWS `.agents/skills/remote-code-parity/` 的 snapshot、selection、install、marker、
  import/dependency proof；
- Motor workspace `mws_parity.py`、`mws_build.py`、`mws_deploy.py`、run state；
- configure/apply/status/restart 入口；
- Motor upstream `startup/boot.sh` 与 ConfigMap 生成；
- vLLM/vllm-ascend `pyproject.toml`、`setup.py` 和 vllm-ascend release wheel workflow；
- 当前 parity/build/deploy tests。

未执行：

- 没有在 195 或真实 base image 中运行 vLLM/vllm-ascend wheel build；
- 没有验证各 Motor role 对 vLLM/vllm-ascend 的实际 import；
- 没有修改任何实现代码；
- 没有评估 release wheel 的 auditwheel/variant wheel 发布流程，因为本设计目标是
  同一 runtime image 内开发替换，不是发布 PyPI wheel。

## 15. Phase 2 前确认点

进入实现前需要确认：

1. 采用推荐的聚合 `runtime-package-build` transaction，而不是三个 builder 各自
   修改 boot；
2. vLLM 变化继续沿用 VAWS 规则，级联 vllm-ascend；
3. active override 必须显式 reset，不因本次无 delta 自动回 image；
4. vLLM/vllm-ascend 仅在 engine roles 安装；
5. 真机 build 命令允许在 Phase 2 后到 195 验证并按真实环境收敛。
