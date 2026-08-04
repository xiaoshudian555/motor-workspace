---
name: motor-build-wheel
description: Build a release-grade Motor wheel (protobuf + Rust kv-conductor) inside Docker on the machine host, then replace the runtime code with the wheel build. Use for motor wheel build, release code replace, build path, motor-build-wheel, TD-P2-07.
---

# motor-build-wheel

发布级代码替换（TD-P2-07）：把 Motor 源码构建成完整 wheel（protobuf 生成 +
Rust kv-conductor + `pip wheel`），并替换进运行环境。

## 为什么必须在 Docker 里构建

本地开发机（Windows/WSL）缺 CANN、grpcio-tools、Rust 工具链；直接 `pip wheel`
产出的是与运行环境 ABI 不一致的包，Pods 加载会失败。因此：

- **wheel 构建永远在 Docker 容器内进行**，容器基础镜像 = 目标运行时
  `base_image_ref`，保证与 Pods 的 Python/CANN/libc 环境一致；
- 容器挂载共享盘上已同步的固定 `motor` 源码树（只读）与共享 build 输出目录；
- 构建只产出 wheel 到共享盘，不修改固定源码树。

## Entry point

```bash
python3 scaffold/.agents/skills/motor-build-wheel/scripts/build_wheel.py \
  --machine dev1 \
  --source-sha <git-commit-sha> \
  --base-image-ref <runtime-image> \
  [--no-reuse]
```

参数：

- `--machine`：machine inventory 别名（必填）。
- `--source-sha`：motor 源码 git commit sha，用于幂等缓存（必填，≥8 hex）。
- `--base-image-ref`：构建容器使用的运行时镜像；缺省从 workspace.lock /
  deploy config 解析。
- `--reuse`（默认开启）：同 source-sha 已有 wheel 时跳过构建。
- `--no-reuse`：强制重建。

## 流程

1. `detect_build_gaps`：检查源码树是否缺 `*_pb2.py` / kv-conductor 二进制；
   若快路径（hostPath/PYTHONPATH）已足够则直接 ready。
2. 远端机器 `docker run`（基于 `base_image_ref`）挂载 motor 源码 + build 输出
   目录，容器内执行上游 `bash build.sh`（含 `generate_proto.sh` 与 cargo build）。
3. 产出 `motor-*.whl` 到共享盘
   `<remote_workspace_root>/motor-wheel-builds/<source_sha>/dist/`，写
   `wheel.sha256` marker。
4. 产出 `motor-wheel-build` run 证据（wheel_dir、source_sha、base_image_ref）。

## 替换

wheel 构建完成后，`render_wheel_replace_manifest` 生成一个 namespaced Job，
用运行时镜像 `pip install --force-reinstall <shared-wheel>`，把 Pod 运行时代码
换成 wheel 构建。部署侧在 overlay 阶段消费该 Job 即可。

## 边界

- 只构建 + 产出替换 Job manifest，不 apply Job、不改 K8s 状态。
- 不修改 `sources/motor` 固定源码树。
- `apply` / 删除 / 覆盖固定目录均需显式授权。
