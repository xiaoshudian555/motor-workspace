---
name: motor-deploy-configure
description: Generate or reuse immutable Motor deploy config bundles with upstream dry-run and server-side validation. Use for deploy configure, config bundle, deploy-config-ready.
---

# motor-deploy-configure

3+3 **第二部分第三步**：复制 Motor 原生配置、upstream dry-run、注入固定
hostPath、校验 namespace 与 RBAC、server-side dry-run，产出不可变 bundle 和
`deploy-config-ready`。不向 workload 注入源码 `PYTHONPATH` 或
`MOTOR_WHEEL_DIR`。

## 边界

**消费**：当前 workflow 的 `deploy-environment-ready`、`machine-ready`、
`parity-complete`（仅 machine 固定路径映射）、Motor 原生 `user_config.json` +
`env.json`。

**不做**：自动 parity、创建 namespace、apply、诊断 Pod、字段级 CLI override。

所有 Kubernetes 校验都在 machine inventory 指向的远端机器上执行
`kubectl`。本地生成的 manifest 会上传到远端临时目录做 server-side
dry-run，完成后清理；不使用开发机的 `kubectl` 或 kubeconfig。

## Entry point

```bash
python3 scaffold/.agents/skills/motor-deploy-configure/scripts/deploy_configure.py \
  --machine dev1 \
  --environment-run-id <environment-run-id> \
  --parity-run-id <parity-run-id> \
  --config-dir sources/motor/examples/infer_engines/vllm
```

Motor-only wheel override：在 parity + `motor-build-wheel` 之后追加
`--motor-wheel-build-run-id <motor-wheel-build-run-id>`。该 run 证明 wheel 已构建，
且远端固定 Motor 树的 `boot.sh` 已写入对应 dist 路径；configure 只记录该证据和
`motor-wheel` package policy，不再向 manifest 注入同名 env。

运行包策略只有两种：

- 不传 wheel build run：Motor、vLLM、vllm-ascend 全部使用镜像包；
- 传 wheel build run：`boot.sh` 启动时只安装 Motor wheel，vLLM、vllm-ascend
  继续使用镜像包。

两种模式都禁止源码 `PYTHONPATH`。从 wheel 模式切回全镜像模式时，必须先重新
执行 parity，以未写死 wheel 路径的 workspace `boot.sh` 覆盖远端版本。

Progress 在 stderr，JSON 结果在 stdout。
