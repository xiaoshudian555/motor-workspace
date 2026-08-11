# 远端开发只保留 endpoint、固定目录 parity 和 wheel 构建

## Endpoint

machine inventory 只保存 `host`、`port`、`user`、`mount_root`、
`remote_workspace_root`、`kube_context` 和 executor。Agent 用 `remote.*` 验证
当前连接与路径，不生成 `machine-ready` 记录。

## local-control

用户授权覆盖固定目录后运行：

```bash
python3 scaffold/.agents/skills/remote-code-parity/scripts/parity_sync.py \
  --machine <alias> --approved-overwrite
```

同步包含 committed、staged、unstaged 和未忽略 untracked 内容。Git synthetic
snapshot/bundle 只用于传输，不要求本地 commit，也不创建 session 目录。

## remote-native

当 Agent workspace 本身就是固定远端目录时运行：

```bash
python3 scaffold/.agents/skills/remote-code-parity/scripts/parity_identity.py \
  --machine <alias>
```

identity 只证明三个源码仓与固定目录相同并记录 digest，不复制文件。

## Runtime

固定源码目录用于内容证明和 wheel 构建。运行 Pod 不从源码树 import：

- 默认使用 image package；
- Motor 替换使用与目标镜像 ABI 一致的 wheel，并由 `boot.sh` 安装；
- vLLM/vllm-ascend 的 ABI 变化走明确的 release/image 路径。

禁止 editable install、源码 `PYTHONPATH`、per-session fanout 和 node-local copy。
