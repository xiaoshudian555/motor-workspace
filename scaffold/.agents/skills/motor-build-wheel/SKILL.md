---
name: motor-build-wheel
description: Build a Motor wheel (protobuf + Rust kv-conductor) inside Docker on the machine host for boot.sh installation during deployment. Use for motor wheel build, release code replace, build path, motor-build-wheel, TD-P2-07.
---

# motor-build-wheel

Motor 代码替换（TD-P2-07）：把 Motor 源码构建成完整 wheel（protobuf 生成 +
Rust kv-conductor + `pip wheel`），供运行 Pod 的 `boot.sh` 安装。

## 为什么必须在 Docker 里构建

本地开发机（Windows/WSL）缺 CANN、grpcio-tools、Rust 工具链；直接 `pip wheel`
产出的是与运行环境 ABI 不一致的包，Pods 加载会失败。因此：

- **wheel 构建永远在 Docker 容器内进行**，容器基础镜像 = 目标运行时
  `base_image_ref`，保证与 Pods 的 Python/CANN/libc 环境一致；
- 容器挂载共享盘上已同步的固定 `motor` 源码树（构建阶段只读）与共享 build
  输出目录；
- 构建产出 wheel 到共享盘后，**立刻把该 dist 路径硬编码进远端固定 motor 树的
  `boot.sh`**（见下方替换链路）。

## Entry point

```bash
python3 scaffold/.agents/skills/motor-build-wheel/scripts/build_wheel.py \
  --machine dev1 \
  --source-sha <git-commit-sha> \
  --base-image-ref <runtime-image> \
  [--reuse]
```

参数：

- `--machine`：machine inventory 别名（必填）。
- `--source-sha`：motor 源码 git commit sha，用于幂等缓存（必填，≥8 hex）。
- `--base-image-ref`：构建容器使用的运行时镜像；缺省从 workspace.lock /
  deploy config 解析。
- 默认强制重建，确保同一 Git SHA 下的本地未提交改动也进入新 wheel。
- `--reuse`：明确接受按 source-sha 复用已有 wheel 时才跳过构建（仍会刷新
  `boot.sh` 硬编码路径）。

## 流程

1. 远端机器 `docker run`（基于 `base_image_ref`）挂载 motor 源码 + build 输出
   目录，容器内执行上游 `bash build.sh`（含 `generate_proto.sh` 与 cargo build）。
2. 强制产出且只保留一个 `motor-*.whl` 到共享盘
   `<remote_workspace_root>/motor-wheel-builds/<source_sha>/dist/`，写
   `wheel.sha256` marker。
3. **写死路径**：把
   `MOTOR_WHEEL_DIR=<该 dist 绝对路径>` 写入远端固定 motor 树的
   `examples/deployer/startup/boot.sh`（`MWS_MOTOR_WHEEL_DIR_*` 标记块，可重复
   覆盖）。谁在哪编、编到哪个 sha，就写成谁的路径——下次重编再换。
4. 产出 `motor-wheel-build` run 证据（wheel_dir、source_sha、base_image_ref、
   boot_sh_path）。

## 替换链路

**主路径（刻意简单）**：编完 → 改远端 `boot.sh` 硬编码本次 `dist` → 后续
deploy/restart 把这份 `boot.sh` 打进 ConfigMap → Pod 启动直接
`pip install` 该 wheel。

不依赖 K8s `MOTOR_WHEEL_DIR` env 注入；configure 只消费 build run 作为证据并
记录 package policy，不再改 manifest env。

## 边界

- 只构建 wheel + 更新远端固定 motor 树里的 `boot.sh` 硬编码块；不 apply、不改
  K8s 工作负载。
- Docker 构建阶段不改源码树；构建成功后**允许且必须**改远端 `boot.sh` 中的
  wheel 路径标记块。
- `apply` / 删除 / 覆盖固定目录中的其它内容仍需显式授权。
- 从 wheel 模式切回全镜像模式时，重新执行 parity，用 workspace 原始
  `boot.sh` 覆盖远端硬编码版本。

## 构建失败排查

上游 `sources/motor/build.sh` 默认可能带
`-i https://pypi.tuna.tsinghua.edu.cn/simple`。部分机房/NPU 节点上 tuna
对 **GET 下包返回 403**（HEAD/索引页仍可能 200），表现为：

`HTTP error 403 while getting .../setuptools-*.whl` /
`Failed to build ... when installing build dependencies`。

此时可依次尝试（改的是本次参与构建的 motor 工作树 / 容器内拷贝，勿假装源本身没问题）：

1. **去掉 `-i ...tuna...`**，让 `pip wheel` 走镜像默认 index（常见为
   `pypi.org`），再重跑本 skill；
2. 显式改用可用源，例如 `-i https://pypi.org/simple`，或镜像/站点已验证可
   GET 的内部 PyPI 镜像；
3. 确认失败是否只发生在 **GET 包文件**（`curl -I` 200 但 `curl`/`pip
   download` GET 403）——若是，换源，不要空等 tuna；
4. 若仍失败：保留构建容器/`docker` 日志，核对 `base_image_ref`、网络、以及
   kv-conductor 所需的 `cargo` 是否在镜像内可用（缺 cargo 时 wheel 可能仍产出，
   但无 kv-conductor 二进制）。
