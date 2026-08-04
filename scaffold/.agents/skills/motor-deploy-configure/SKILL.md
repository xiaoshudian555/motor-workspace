---
name: motor-deploy-configure
description: Generate or reuse immutable Motor deploy config bundles with upstream dry-run and server-side validation. Use for deploy configure, config bundle, deploy-config-ready.
---

# motor-deploy-configure

3+3 **第二部分第三步**：复制 Motor 原生配置、upstream dry-run、注入固定
hostPath / `PYTHONPATH`、校验 namespace 与 RBAC、server-side dry-run，产出
不可变 bundle 和 `deploy-config-ready`。

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

Motor-only wheel override（ModelArts 风格，`boot.sh` 在 Pod 启动时
`pip install`）：在 parity + `motor-build-wheel` 之后追加
`--motor-wheel-build-run-id <motor-wheel-build-run-id>`（或
`--motor-wheel-dir /mnt/.../motor-wheel-builds/<sha>/dist`）。此时 manifest
注入 `MOTOR_WHEEL_DIR`，**不**注入 vLLM/vllm-ascend 源码 `PYTHONPATH`。

Progress 在 stderr，JSON 结果在 stdout。
