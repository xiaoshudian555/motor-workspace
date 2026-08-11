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

命中则跳到步骤 4；否则继续。

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

### 4. 直接修改 boot.sh 标记块

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

### 5. 验收（必须全部满足）

- `${WHEEL_DIST}` 下恰好一个 `motor-*.whl`；
- `${BUILD_DIR}/wheel.sha256` 存在且与 whl 一致；
- boot.sh 含 `MWS_MOTOR_WHEEL_DIR_*` 块且 `MOTOR_WHEEL_DIR` 指向 `${WHEEL_DIST}`；
- 报告 `source_sha`、`base_image_ref`、wheel 路径、boot.sh 路径。

**部署后**还需 `deploy.py --update_config` + 目标 workload rollout restart，
Pod 启动日志应出现 uninstall 旧 motor + pip install 新 whl；Coordinator
`/readiness` 由 `motor-smoke` 单独验证。

## 不保留 workspace build script

`docker run`、清理 dist、`sha256sum`、单 whl 校验、复用探测和 boot.sh 精确
patch 都由 Skill 调用现有原子工具完成。本仓不再提供 `motorws build-wheel`、
`mws_build.py` 或 build 专用 reference script。唯一保留的构建脚本是 Motor 上游
`build.sh`。

## 替换链路

编完 → patch boot.sh → deploy/restart 把 boot.sh 打进 ConfigMap → Pod 启动
`pip install` 该 wheel。不依赖 K8s env 注入 `MOTOR_WHEEL_DIR`。

## 边界

- 只构建 wheel + 更新远端 boot.sh；不 apply、不改 K8s、不创建 namespace。
- Docker 构建阶段不改源码树；构建成功后**允许且必须**改 boot.sh 标记块。
- 从 wheel 模式切回全镜像模式：parity 覆盖 boot.sh，或按步骤 4 精确删除标记块。

## 构建失败排查

上游 `sources/motor/build.sh` 默认可能带 tuna PyPI 镜像；部分节点 GET 包文件
403。依次尝试去掉 `-i`、换 `pypi.org`、核对 `base_image_ref` 与 cargo 可用性。
详见 upstream build.sh 注释。
