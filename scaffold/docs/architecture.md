# motor-workspace 只保留远端底座、代码 parity 和 wheel 构建执行器

本仓库不再实现第二套部署平台。Agent Skill 负责说明顺序、授权门和通过标准，
实际操作复用 Git、`gh`、`.remote-dev`、`kubectl` 和 Motor upstream deployer。

## 实现边界

| 层 | 职责 |
|---|---|
| `scaffold/.agents/skills/` | 自然语言路由和操作说明；默认不配 Python script |
| `scaffold/.remote-dev/` | 通用远端 read/edit/bash/search/job/artifact 能力 |
| `remote-code-parity` | 唯一保留的代码同步执行器：dirty tree → 远端固定目录 |
| `motor-build-wheel` | 唯一保留的构建执行器：在运行镜像环境中构建 Motor wheel |
| `sources/motor/examples/deployer/` | Motor 配置生成、dry-run、apply、delete 的权威实现 |

不再存在 `workspace-ready`、`machine-ready`、`deploy-environment-ready`、
`deploy-config-ready`、`deploy-complete` 状态交接。部署判断基于当前 endpoint、
原生配置和实时 K8s 状态。

## 两种开发拓扑

```text
local-control
  local workspace → parity_sync.py → /mnt/motor-workspace/*

remote-native
  workspace 已位于固定目录 → parity_identity.py 只证明路径和内容
```

固定目录为：

```text
<mount_root>/motor-workspace/motor
<mount_root>/motor-workspace/vllm
<mount_root>/motor-workspace/vllm-ascend
<mount_root>/motor-workspace/python-overlay
```

这些目录用于构建和内容证明。Pod 运行时使用镜像包，或通过 `boot.sh` 安装显式
构建的 Motor wheel；禁止源码树 `PYTHONPATH`。

## 部署主路径

```text
读取 endpoint 与原生 user_config.json/env.json
→ remote.* + kubectl 做只读检查
→ Motor deploy.py --dry-run
→ 用户授权
→ Motor deploy.py
→ kubectl 检查 Ready/Service/runtime package
```

preflight 不改用户配置。NodePort 冲突只报告，修改必须取得明确授权。parity
覆盖、apply、restart、stop、ConfigMap 修改分别取得目标级授权。
