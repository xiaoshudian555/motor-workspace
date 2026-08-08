# Controller / Coordinator 单组件配置更新与重启

本手册用于已经运行的 Motor Kubernetes 部署。适用场景是用户明确要求修改
Controller 或 Coordinator 配置，并且只重启对应组件，不重启 P/D、KV Store 或
其他 workload。

这是一条有状态的线上维护路径。修改 ConfigMap 和执行 rollout 前必须获得用户
明确同意。

## 核心原则

1. 同时维护期望配置源和运行时 ConfigMap。修改宿主机或 bundle 中的
   `user_config.json` 只保证未来重新部署使用新值，不会自动更新现有
   `motor-config` ConfigMap。
2. 运行中 Pod 的配置来源以 Deployment 实际挂载的 ConfigMap 为准。先从
   Deployment 的 `volumes[].configMap.name` 发现名称，不要仅凭经验假设。
3. ConfigMap 中的 `user_config.json` 可能与宿主机文件存在合法运行时差异。必须
   以当前 ConfigMap 内容为基线，只修改用户指定的 JSON 字段；禁止用宿主机整份
   文件覆盖 ConfigMap。
4. `motor_controller_config` 变更只 rollout Controller；
   `motor_coordinator_config` 变更只 rollout Coordinator。
5. `deploy_restart.py` 会处理 deploy run 的完整 workload 集，不用于本手册的
   单组件重启。

## 1. 发现目标资源

先设置 namespace，并发现真实 Deployment 名：

```bash
NS=<namespace>
kubectl get deployment -n "$NS" | grep -E 'controller|coordinator'
```

典型名称为：

```text
mindie-motor-controller
mindie-motor-coordinator
```

从目标 Deployment 发现 ConfigMap 和挂载路径：

```bash
TARGET=mindie-motor-coordinator  # 或 mindie-motor-controller

kubectl get deployment "$TARGET" -n "$NS" \
  -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}{"\t"}{.configMap.name}{"\n"}{end}'

kubectl get deployment "$TARGET" -n "$NS" \
  -o jsonpath='{range .spec.template.spec.containers[*]}{range .volumeMounts[*]}{.name}{"\t"}{.mountPath}{"\n"}{end}{end}'
```

主路径通常使用 `motor-config`，其中 `data.user_config.json` 是运行配置。

## 2. 备份并只改目标字段

先保存当前运行配置作为回滚和差异基线：

```bash
CONFIG_MAP=motor-config
kubectl get configmap "$CONFIG_MAP" -n "$NS" -o yaml \
  > "/tmp/${NS}-${CONFIG_MAP}-before.yaml"

kubectl get configmap "$CONFIG_MAP" -n "$NS" \
  -o jsonpath='{.data.user_config\.json}' \
  > "/tmp/${NS}-${CONFIG_MAP}-user-config-before.json"
```

交互修改：

```bash
kubectl edit configmap "$CONFIG_MAP" -n "$NS"
```

在 `data.user_config.json: |` 下找到目标字段，只修改该字段。不要改其他 JSON、
启动脚本或 ConfigMap metadata。

保存后回读并确认目标值：

```bash
kubectl get configmap "$CONFIG_MAP" -n "$NS" \
  -o jsonpath='{.data.user_config\.json}' \
  | grep '<target-json-key>'
```

自动化修改时也必须从当前 ConfigMap 的 `data.user_config.json` 生成 merge patch，
不得从宿主机整文件生成 patch。应用前后应做 diff，结果只能包含目标字段。

同时修改持久化的期望配置源中同一字段，防止下一次完整部署回退；但不要因此把
期望配置源的其他差异复制到运行 ConfigMap。

## 3. 只 rollout 目标组件

先记录当前 Pod，便于证明其他组件未重启：

```bash
kubectl get pods -n "$NS" \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp' \
  --no-headers | sort
```

只重启目标 Deployment：

```bash
kubectl rollout restart deployment/"$TARGET" -n "$NS"
kubectl rollout status deployment/"$TARGET" -n "$NS" --timeout=300s
```

禁止为这类操作调用重启完整 workload 集的 `deploy_restart.py`，也不要删除
Controller、Coordinator 之外的 Pod。

## 4. 验证实际生效

获取新 Pod 并检查 Pod 内实际配置。不要只检查宿主机文件或 ConfigMap：

```bash
POD=$(kubectl get pod -n "$NS" \
  -l "app=${TARGET}" \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n "$NS" "$POD" -- sh -c \
  'env | grep -E "^(USER_CONFIG_PATH|CONFIG_PATH)=" || true'
```

主路径常见运行配置为
`/usr/local/Ascend/pyMotor/conf/user_config.json`。以容器实际环境和启动命令为准，
回读目标字段：

```bash
kubectl exec -n "$NS" "$POD" -- \
  grep '<target-json-key>' /usr/local/Ascend/pyMotor/conf/user_config.json
```

再次列出所有 Pod，确认只有目标组件的 Pod 名称或创建时间发生变化。

Coordinator 还必须通过管理面 readiness。先发现 mgmt Service，随后要求 HTTP 200
且响应 JSON 中 `ready=true`：

```bash
kubectl get service -n "$NS" | grep 'coordinator-mgmt'
MGMT_IP=$(kubectl get service -n "$NS" mindie-motor-coordinator-mgmt \
  -o jsonpath='{.spec.clusterIP}')
curl -sS --max-time 10 -w '\nHTTP_STATUS=%{http_code}\n' \
  "http://${MGMT_IP}:1026/readiness"
```

Controller 至少要求 rollout 成功、Pod `Ready`、目标配置已在 Pod 内回读，并结合
所改功能检查 Controller 启动日志或对应管理接口。Coordinator 的 readiness 不能
替代 Controller 验证。

## 5. 失败与回滚

- rollout 成功但 Pod 内仍是旧值：说明修改的不是实际挂载 ConfigMap，或启动阶段
  又把其他配置复制到了运行目录。重新检查 Deployment volume、volumeMount、环境
  变量和启动脚本，不要反复盲目重启。
- ConfigMap diff 出现非目标字段：立即停止，使用备份恢复非目标内容，再重新生成
  仅含目标字段的修改。
- 新 Pod 不 Ready：保留 Pod、事件和日志现场；回滚 ConfigMap 目标字段后再次只
  rollout 目标 Deployment。
- 配置验证和 readiness 都通过后，才可以声明单组件配置更新完成。
