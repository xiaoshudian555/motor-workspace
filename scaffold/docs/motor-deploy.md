# Motor 部署直接复用 upstream deployer，不再维护 wrapper 状态机

## 前置输入

- machine inventory 中的 endpoint、`mount_root`、`kube_context`；
- Motor 原生 `user_config.json` 和 `env.json`；
- 已存在的 namespace、镜像、模型和调度资源；
- 本地代码替换时完成 parity；Motor wheel 替换时完成 wheel build。

## 只读检查

在目标 endpoint 上检查 Kubernetes API、权限、节点/NPU、MindCluster/Volcano、
controller/device-plugin Pod、镜像覆盖和 NodePort 占用。检查不得修改用户配置或
Kubernetes 状态。

## 配置验证

从固定 Motor 源码目录执行：

```bash
cd <source_dirs.motor>/examples/deployer
python3 deploy.py --config_dir <remote-config-dir> --dry-run

# namespace 必须已存在；configure 不得创建 namespace
kubectl --context "$CTX" get namespace "$NS"

# 只对 *.yaml/*.yml 做 server-side dry-run（排除 .motor_config_user_config.json 等）
mapfile -t YAML_FILES < <(
  find output_yamls -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) | sort
)
kubectl --context "$CTX" apply --dry-run=server \
  $(printf ' -f %s' "${YAML_FILES[@]}")
```

逐一检查生成 YAML 的 namespace、image、hostPath、volumeMount、resources、
NodePort 和 workload。upstream 模板已经挂载 `/mnt`，workspace 不再二次注入。

## 正式部署

展示目标和命令并取得明确授权后执行：

```bash
python3 deploy.py --config_dir <remote-config-dir>
```

随后检查当前 workload rollout、Pod Ready、Service endpoints、events，以及需要时
的运行包 `__file__`。旧本地 JSON 不能替代实时状态。

## 生命周期

- status：直接 `kubectl get/describe/logs`；
- restart：只 restart 明确发现并经授权的 workload，禁止 `--all`；
- stop：优先 upstream `delete.sh`，否则只删除生成的 manifest；
- 单组件配置更新：只改 ConfigMap 单个 JSON 字段时，按 Skill reference
  `component-config-rollout.md` 只 rollout Controller 或 Coordinator；
- Controller/Coordinator 调试循环（换 `yaml_template` / `user_config` / 组件
  whl，且不重启 P/D）：按
  `docs/controller-coordinator-debug-rollout.md`，禁止全量 `deploy.py`。

Coordinator smoke、functional、benchmark 和 diagnosis 属于部署后的独立目标。
