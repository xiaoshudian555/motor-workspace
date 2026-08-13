---
name: motor-build-wheel
description: Build a Motor wheel (protobuf + Rust kv-conductor) inside Docker on the machine host for boot.sh installation during deployment. Use for motor wheel build, release code replace, build path, motor-build-wheel, TD-P2-07.
---

# motor-build-wheel

Motor 代码替换（TD-P2-07）：把 Motor 源码构建成完整 wheel（protobuf 生成 +
Rust kv-conductor + `pip wheel`），供运行 Pod 的 `boot.sh` 安装。

## 两层构建物（不要混用）

| 层 | 位置 | 作用 |
|---|---|---|
| **上游** | `sources/motor/build.sh` | 在运行时 Docker 镜像内执行的实际构建（proto、cargo、`pip wheel`） |
| **workspace** | 无独立 build backend | Skill 直接用远端原生命令编排 docker、sha256 和 boot.sh 修改 |

## 为什么必须在 Docker 里构建

本地开发机（Windows/WSL）缺 CANN、grpcio-tools、Rust 工具链；直接 `pip wheel`
产出的是与运行环境 ABI 不一致的包，Pods 加载会失败。因此：

- **wheel 构建永远在 Docker 容器内进行**，容器基础镜像 = 目标运行时
  `base_image_ref`；
- 容器挂载共享盘上固定 `motor` 源码树（只读）与共享 build 输出目录；
- 构建成功后由 Skill 直接更新远端固定 motor 树的 `boot.sh` 标记块。

## 参数（Agent 自行收集，无 motorws 子命令）

- **machine**：inventory 别名（必填）。
- **source-sha**：motor 源码 git commit sha，用于输出目录幂等键（必填，≥8 hex）。
- **base-image-ref**：构建容器使用的运行时镜像（必填，从 native config 或
  运行时 workload 确认，禁止猜测）。
- **reuse**：仅当 caller 明确接受按 source-sha 复用已有 wheel 时跳过 docker
  build（仍会刷新 boot.sh 标记块）。

从 inventory 解析：

- `SOURCE_ROOT=<source_dirs.motor>`
- `REMOTE_WS=<remote_workspace_root>`
- `BUILD_DIR=${REMOTE_WS}/motor-wheel-builds/<source_sha>`
- `WHEEL_DIST=${BUILD_DIR}/dist`

## 流程

### 0. 构建前缺口检查（可选但推荐）

在 `SOURCE_ROOT` 上确认存在 `build.sh`；若 `.proto` 无对应 `_pb2.py` 或缺少
`motor/kv_conductor/bin/kv-conductor`，说明必须走 docker build 而非源码
PYTHONPATH。

### 1. 复用探测（仅 `--reuse` 或 caller 明确要求时）

```bash
test -f "${BUILD_DIR}/wheel.sha256" \
  && set -- "${WHEEL_DIST}"/motor-*.whl \
  && test "$#" -eq 1 && test -f "$1" && echo WHEEL_REUSE_OK
```

命中则跳过步骤 2–3（复用已有 wheel），但仍须通过步骤 4 的前置检查后再进入步骤 5。

### 2. Docker 内执行上游 build.sh

通过 `remote.bash` 的 `run_in_background=true` 启动下面的长命令，再使用
`remote.job_status` / `remote.job_tail` 等待完成。不要用普通 60s 同步 SSH，也不要
在超时后重复启动另一份构建。

```bash
mkdir -p "${WHEEL_DIST}"
rm -f "${WHEEL_DIST}"/motor-*.whl "${BUILD_DIR}/wheel.sha256"

docker run --rm --network=host \
  -v "${SOURCE_ROOT}:/src/motor:ro" \
  -v "${WHEEL_DIST}:/out" \
  -w /work \
  "${BASE_IMAGE_REF}" \
  bash -c 'set -euo pipefail; cp -r /src/motor /work/motor; cd /work/motor; bash build.sh; cp dist/motor-*.whl /out/'
```

镜像须已在节点本地；Skill **不** pull 镜像。

### 3. 单 wheel 校验 + sha256 marker

```bash
cd "${WHEEL_DIST}"
set -- motor-*.whl
test "$#" -eq 1 && test -f "$1" || { echo "expected exactly one motor-*.whl"; exit 1; }
sha256sum "$1" > "${BUILD_DIR}/wheel.sha256"
```

### 4. 前置条件检查：wheel 路径 pod 内可见（gate）

wheel 能否生效取决于 pod 启动时是否读得到 `MOTOR_WHEEL_DIR` 下的 wheel。
**前置检查必须全部通过，才允许改 boot.sh 并 rollout restart；不通过先解决，禁止跳过。**

**检查 1：wheel 放置路径是否宿主机共享存储**

> 共享存储指宿主机机器层面的共享文件系统（如 NFS/Lustre 等挂载在节点宿主机上），
> 同一绝对路径所有节点都可见。这是机器维度；pod 有没有挂载由检查 2 单独确认，
> 不要在这里假设。

在目标节点上判断 `${WHEEL_DIST}` 所在文件系统：

```bash
df -T "${WHEEL_DIST}"
findmnt -T "${WHEEL_DIST}"
```

- 网络文件系统（nfs / lustre / gpfs / ceph / glusterfs）→ 共享存储，放一次即可；
- 本地盘（ext4 / xfs 等）→ 仅本节点可见，需要解决：
  - **无共享存储**：逐台目标节点在同一绝对路径各复制一份 wheel（`scp`/`rsync`
    到每台节点相同的 `${WHEEL_DIST}`，含 `wheel.sha256`），保证各节点路径一致；
  - **有共享存储**：把 wheel 搬到共享存储下新路径，并同步更新 boot.sh
    标记块 `MOTOR_WHEEL_DIR` 指向新路径。

**检查 2：pod 是否挂载该路径、pod 内是否可见**

pod 有没有挂载不确定（即使宿主机是共享存储，pod 也可能没挂），以环境上实际
部署的 YAML 为准（环境与本地模板可能有差异，不要只看本地模板）：

```bash
kubectl --context "$CTX" get deployment <workload> -n <NS> -o yaml
```

确认 wheel 路径落在某 volume 的 `mountPath`（或该路径的子路径）下，且该 volume
挂进目标 pod；也可在运行中 pod 内直接验证：

```bash
kubectl --context "$CTX" exec <pod> -n <NS> -- ls "${WHEEL_DIST}"
```

**解决（未挂载）**：改环境实际使用的 YAML 挂载使该路径进入 pod；必要时可用
`nodeSelector` 把 pod 钉到放包的节点（如逐节点复制方案下，必须钉到已放包的
节点）。改挂载按角色区分：

- engine 等走 `motor_deploy_config.storage` 注入的 pod → 改 `user_config.json`
  的 storage（nfs/pvc/hostpath）后重新生成；
- Coordinator/Controller 不走 storage 注入 → 需改 `yaml_template/*.yaml`
  或生成后的 `output_yamls/`，再 apply 生效。

**gate 判定**：检查 1、2 均通过 → 继续步骤 5；任一不满足 → 先按上述解决，
解决后重新检查，全部通过前禁止 rollout restart。

### 5. 直接修改 boot.sh 标记块

先用 `remote.read` 读取
`${SOURCE_ROOT}/examples/deployer/startup/boot.sh`，再用 `remote.apply_patch`：

- 已存在 `MWS_MOTOR_WHEEL_DIR_BEGIN/END` 时，只替换两标记之间的整块内容；
- 不存在时，在原生 `if [ -n "${MOTOR_WHEEL_DIR:-}" ]; then` 前插入：

```bash
# >>> MWS_MOTOR_WHEEL_DIR_BEGIN
MOTOR_WHEEL_DIR="<WHEEL_DIST 的绝对路径>"
# <<< MWS_MOTOR_WHEEL_DIR_END
```

写后重新读取并确认 BEGIN/END 各恰好一次，且路径完全等于本次
`${WHEEL_DIST}`。找不到原生安装块、标记不成对或标记重复时停止，不猜插入位置。

切回镜像模式时，读取最新文件后用 `remote.apply_patch` 精确删除 BEGIN 到 END
整块，并重新读取确认两个标记均不存在。

### 6. 验收（必须全部满足）

- `${WHEEL_DIST}` 下恰好一个 `motor-*.whl`；
- `${BUILD_DIR}/wheel.sha256` 存在且与 whl 一致；
- boot.sh 含 `MWS_MOTOR_WHEEL_DIR_*` 块且 `MOTOR_WHEEL_DIR` 指向 `${WHEEL_DIST}`；
- 报告 `source_sha`、`base_image_ref`、wheel 路径、boot.sh 路径。

**部署后**还需 `deploy.py --update_config` + 目标 workload rollout restart，
Pod 启动日志应出现 uninstall 旧 motor + pip install 新 whl；Coordinator
`/readiness` 由 `motor-smoke` 单独验证。

这是共享 `boot.sh` 路径：P/D 下次重启也会装这个包。若只要换
Coordinator/Controller 的包、改该组件 `user_config` / `yaml_template`，且不重启
P/D，走 `scaffold/docs/controller-coordinator-debug-rollout.md`，不要改
`boot.sh`。

## 不保留 workspace build script

`docker run`、清理 dist、`sha256sum`、单 whl 校验、复用探测和 boot.sh 精确
patch 都由 Skill 调用现有原子工具完成。本仓不再提供 `motorws build-wheel`、
`mws_build.py` 或 build 专用 reference script。唯一保留的构建脚本是 Motor 上游
`build.sh`。

## 替换链路

编完 → 步骤 4 前置检查通过（共享存储 + pod 已挂载）→ patch boot.sh →
deploy/restart 把 boot.sh 打进 ConfigMap → Pod 启动 `pip install` 该 wheel。
不依赖 K8s env 注入 `MOTOR_WHEEL_DIR`。

## 边界

- 只构建 wheel + 解决 wheel 可达性前置条件（必要时调整 workload 挂载）+
  更新远端 boot.sh；不创建 namespace、不改动与本次替换无关的 K8s 资源。
- 前置条件未满足（路径非共享、pod 内不可见）时禁止 rollout restart，必须先解决。
- Docker 构建阶段不改源码树；构建成功后**允许且必须**改 boot.sh 标记块。
- 从 wheel 模式切回全镜像模式：parity 覆盖 boot.sh，或按步骤 5 精确删除标记块。

## 构建失败排查

上游 `sources/motor/build.sh` 默认可能带 tuna PyPI 镜像；部分节点 GET 包文件
403。依次尝试去掉 `-i`、换 `pypi.org`、核对 `base_image_ref` 与 cargo 可用性。
详见 upstream build.sh 注释。
