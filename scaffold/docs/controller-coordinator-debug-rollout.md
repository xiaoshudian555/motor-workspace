# Controller/Coordinator 单组件调试重启：换配置、换 whl、换 YAML，且不重启 P/D

调试 Controller 或 Coordinator 时，P/D 已经把大模型加载好了。本手册把目标组件的
代码、配置、Pod spec 单独推上去并只重启该组件。禁止全量 `deploy.py`，否则
engine YAML 里的 `generate_unique_id()` 会变，P/D 会被一起滚掉。

## 目录

- [适用与不适用](#适用与不适用)
- [三条管道](#三条管道)
- [禁止事项](#禁止事项)
- [统一流程](#统一流程)
  - [0. 变量](#0-变量)
  - [1. 改源文件](#1-改源文件)
  - [2. 先刷新 ConfigMap](#2-先刷新-configmap)
  - [3. 只生成 YAML，只 apply 目标组件](#3-只生成-yaml只-apply-目标组件)
  - [4. 确认只有目标组件在滚](#4-确认只有目标组件在滚)
  - [5. 验收](#5-验收)
- [失败与回滚](#失败与回滚)
- [和其它手册的分工](#和其它手册的分工)

## 适用与不适用

适用（可同时发生）：

- 改了 `user_config.json` 里目标组件自己的段；
- 换了只给该组件用的 Motor whl；
- 改了 `yaml_template/coordinator_template.yaml` 或 `controller_template.yaml`
  （挂载、`nodeSelector`、资源、probe 等）。

验收就一条：只有目标 Deployment 的 Pod 创建时间变了；P/D、KV Store、
Controller/Coordinator 中未被选中的那一个，创建时间不变。

不适用：

- 只改运行中 ConfigMap 的单个 JSON 字段，不改模板、不换包 →
  `../.agents/skills/motor-k8s-deploy/references/component-config-rollout.md`；
- 要让 P/D 也换同一个 whl → `motor-build-wheel` 的共享 `boot.sh` 路径，不是本手册；
- 改 instance 数、首次部署、删服务。

改 ConfigMap、`kubectl apply`、restart 前必须得到用户明确同意。

## 三条管道

| 你改的 | 真正生效点 | 只 `rollout restart` | `--update_config` | 全量 `deploy.py` |
|---|---|---|---|---|
| `yaml_template/*.yaml` | 已 apply 的目标 Deployment spec | 无效 | 无效 | 模板会生效，但 P/D 也会重启 |
| 宿主机 `user_config.json` | 共享 ConfigMap `motor-config` 的 `user_config.json` | 无效 | 只推白名单字段，调试字段大多被丢掉 | 会推全量，但 P/D 也会重启 |
| 组件 whl | 节点上的包 + ConfigMap 里的 `coordinator.sh` / `controller.sh` + Pod 已挂载该路径 | 包放了也不装 | 脚本能进 ConfigMap，新挂载不会 apply | 同上，会误伤 P/D |

`kubectl rollout restart` 只是把**当前已经 apply 进去的 Pod 模板**再滚一遍。它不读
宿主机模板，也不把新配置/脚本打进集群。

deployer **没有**「刷新 ConfigMap + 只 apply 某一个 YAML」的 CLI。本手册把这两步
拆开做。不要用「执行当前配置的部署命令」代替。

## 禁止事项

- `python3 deploy.py --config_dir ...`（无 `--dry-run`）：会 apply 全部 YAML。
- `python3 deploy.py --update_config`：不 apply Deployment；`user_config` 还受
  `UPDATE_CONFIG_WHITELIST` 限制（Coordinator 只放行 log_level / exception /
  timeout）。调试用字段会被静默丢掉。
- `kubectl apply -f output_yamls/` 或 apply engine YAML。
- 把 `pip install` 写进共享 `boot.sh` / `MOTOR_WHEEL_DIR`。P/D 下次重启也会换包。
- 用一整段新的 `volumeMounts:` / `volumes:` 覆盖模板里已有的同名 key。YAML 重复
  key 是后者覆盖前者，会丢掉 `motor-config`、`/mnt` 等现有挂载。
- `kubectl rollout restart --all`，或删除非目标 Pod。

## 统一流程

无论三件套改了几件，都按这个顺序。先 ConfigMap，再 apply 目标 YAML，这样新 Pod
启动时才能读到新脚本、新配置、新挂载。

生成目标 YAML 时 `job-name` 会换新的 unique id，`kubectl apply` 目标文件本身就会
滚动重启该组件，不必再对同一 Deployment 做一次 `rollout restart`。若跳过了第 3
步（模板确实没改、只想刷新 ConfigMap），再单独 `rollout restart` 目标 Deployment。

### 0. 变量

在部署机、固定 Motor 源码树的 `examples/deployer` 下执行。namespace 就是
`motor_deploy_config.job_id`，不要写死。

```bash
DEPLOYER=<fixed-motor-tree>/examples/deployer
CONFIG_DIR=<remote-config-dir>          # 内含正在用的 user_config.json
CTX=<kube-context>
NS=<job_id>

# 二选一
TARGET=mindie-motor-coordinator
TARGET_YAML=output_yamls/mindie_motor_coordinator.yaml
ROLE_SCRIPT=startup/roles/coordinator.sh

# TARGET=mindie-motor-controller
# TARGET_YAML=output_yamls/mindie_motor_controller.yaml
# ROLE_SCRIPT=startup/roles/controller.sh
```

先拍一份 Pod 创建时间，后面用来证明 P/D 没被重启：

```bash
cd "$DEPLOYER"
kubectl --context "$CTX" get pods -n "$NS" \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp' \
  --no-headers | sort | tee "/tmp/${NS}-pods-before.txt"
```

### 1. 改源文件

只改目标组件需要的文件。`user_config.json` 只动
`motor_coordinator_config` 或 `motor_controller_config`。不要顺手改 engine 段：
ConfigMap 是共享的，P/D 下次重启会读到。

#### 1.1 换 whl

1. 把新包装到目标 Pod **已经能看到** 的路径。模板默认已有 hostPath `/mnt`。包放
   `/mnt/...` 时不必改 YAML。
2. 路径不在现有 mount 下（例如 `/zpdata/...`）才改模板，**往现有 list 追加**
   一项，不要另起一块同名 key：

```yaml
          volumeMounts:
          - name: motor-config
            mountPath: /mnt/configmap
          # ... 保留原有项 ...
          - name: zpdata
            mountPath: /zpdata
      volumes:
      - name: motor-config
        configMap:
          name: motor-config
          defaultMode: 0550
      # ... 保留原有项 ...
      - name: zpdata
        hostPath:
          path: /zpdata
          type: DirectoryOrCreate
```

3. 包只在某一节点本地盘上时，才给该组件加 `nodeSelector`（同样是追加，不是覆盖
   整个 `spec`）。共享存储则不要钉死节点。
4. 在 `$ROLE_SCRIPT` 里、`python3 -m motor.coordinator.main` 或
   `python3 -m motor.controller.main` **之前**安装，写绝对路径：

```bash
pip install /mnt/ucm/pkgs/coordinator/motor-3.1.0-py3-none-any.whl --force-reinstall -v
```

不要写进 `startup/boot.sh`。

#### 1.2 换 user_config

改 `$CONFIG_DIR/user_config.json` 的目标段。这一步只改宿主机文件，集群还没吃到。

#### 1.3 换 yaml_template

改对应模板。生成器会保留模板里追加的 volume / `nodeSelector`。改完仍须走第 3 步
apply，集群里的 Deployment 不会自己读模板。

### 2. 先刷新 ConfigMap

从当前磁盘文件重建整个 `motor-config`。必须在 `$DEPLOYER` 目录执行。`--from-file`
列表必须与 `lib/generator/k8s_utils.py` 的 `create_motor_config_configmap()`
一致；少一个 key，apply 后 ConfigMap 里对应文件就会丢。

```bash
cd "$DEPLOYER"
USER_CONFIG="$CONFIG_DIR/user_config.json"

kubectl --context "$CTX" create configmap motor-config \
  --from-file=./startup/boot.sh \
  --from-file=./startup/common.sh \
  --from-file=./startup/hccl_tools.py \
  --from-file=./startup/roles/kv_store_backends/mooncake/mooncake_config.py \
  --from-file=./startup/roles/controller.sh \
  --from-file=./startup/roles/coordinator.sh \
  --from-file=./startup/roles/engine.sh \
  --from-file=./startup/roles/kv_cache_store.sh \
  --from-file=kv_store_backends.mooncake.mooncake.sh=./startup/roles/kv_store_backends/mooncake/mooncake.sh \
  --from-file=kv_store_backends.memcache.memcache.sh=./startup/roles/kv_store_backends/memcache/memcache.sh \
  --from-file=kv_store_backends.memcache.memcache_meta_service.py=./startup/roles/kv_store_backends/memcache/memcache_meta_service.py \
  --from-file=kv_store_backends.memcache.mmc-local-inprocess.conf=./startup/roles/kv_store_backends/memcache/mmc-local-inprocess.conf \
  --from-file=kv_store_backends.memcache.mmc-local-standalone.conf=./startup/roles/kv_store_backends/memcache/mmc-local-standalone.conf \
  --from-file=./startup/roles/kv_conductor.sh \
  --from-file=./startup/roles/mf_store.sh \
  --from-file=./startup/roles/all_combine_in_single_container.sh \
  --from-file=./probe/probe.sh \
  --from-file=./probe/probe.py \
  --from-file=./prestop/prestop.sh \
  --from-file=./prestop/prestop.py \
  --from-file=user_config.json="$USER_CONFIG" \
  -n "$NS" \
  --dry-run=client -o yaml \
  | kubectl --context "$CTX" apply -f -
```

这会更新共享 ConfigMap，但**不会**重启任何 Pod。P/D 继续用进程里已加载的旧值。

回读确认目标字段和角色脚本已进 ConfigMap：

```bash
kubectl --context "$CTX" get configmap motor-config -n "$NS" \
  -o jsonpath='{.data.user_config\.json}' | grep '<target-json-key>'

kubectl --context "$CTX" get configmap motor-config -n "$NS" \
  -o jsonpath='{.data.coordinator\.sh}' | grep 'pip install'   # Controller 则看 controller.sh
```

### 3. 只生成 YAML，只 apply 目标组件

`--dry-run` 会在本地生成全部 YAML（含 engine），但跳过 `set_env_to_shell` 和
`kubectl apply`。生成完只 apply 目标那一个文件：

```bash
cd "$DEPLOYER"
python3 deploy.py --config_dir "$CONFIG_DIR" --dry-run

kubectl --context "$CTX" apply -f "$TARGET_YAML" -n "$NS"
kubectl --context "$CTX" rollout status deployment/"$TARGET" -n "$NS" --timeout=300s
```

不要 apply `output_yamls/` 下其它文件。engine / InferServiceSet YAML 即使刚刚
生成了新的 unique id，只要不 apply，P/D 就不会动。

若第 1 步完全没改 `yaml_template`，可以跳过本步，改为：

```bash
kubectl --context "$CTX" rollout restart deployment/"$TARGET" -n "$NS"
kubectl --context "$CTX" rollout status deployment/"$TARGET" -n "$NS" --timeout=300s
```

### 4. 确认只有目标组件在滚

```bash
kubectl --context "$CTX" get pods -n "$NS" \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp' \
  --no-headers | sort | tee "/tmp/${NS}-pods-after.txt"
diff "/tmp/${NS}-pods-before.txt" "/tmp/${NS}-pods-after.txt"
```

diff 里只应出现 `$TARGET` 的 Pod。出现 `vllm-p*` / `vllm-d*` / `prefill` /
`decode` 或另一个管理面组件，说明误 apply 了其它 YAML 或跑了全量 deploy。停下，
不要继续验收。

### 5. 验收

不要只看宿主机文件或 ConfigMap，要看新 Pod 内实际值：

```bash
POD=$(kubectl --context "$CTX" get pod -n "$NS" \
  -l "app=${TARGET}" \
  -o jsonpath='{.items[0].metadata.name}')

kubectl --context "$CTX" exec -n "$NS" "$POD" -- \
  grep '<target-json-key>' /usr/local/Ascend/pyMotor/conf/user_config.json
```

换了 whl 时，启动日志须有 `Successfully installed motor`（或等价 pip 成功输出），
并且不要出现「No such file」这类找不到包路径的错误。可在 Pod 内确认包路径存在：

```bash
kubectl --context "$CTX" exec -n "$NS" "$POD" -- ls '<wheel-absolute-path>'
```

Coordinator 还必须过管理面 readiness（HTTP 200 且 JSON `ready=true`）：

```bash
kubectl --context "$CTX" get service -n "$NS" | grep 'coordinator-mgmt'
MGMT_IP=$(kubectl --context "$CTX" get service -n "$NS" mindie-motor-coordinator-mgmt \
  -o jsonpath='{.spec.clusterIP}')
curl -sS --max-time 10 -w '\nHTTP_STATUS=%{http_code}\n' \
  "http://${MGMT_IP}:1026/readiness"
```

Controller 至少要求 rollout 成功、Pod `Ready`、目标配置已在 Pod 内回读，并结合
所改功能看启动日志。Coordinator 的 readiness 不能替代 Controller 验证。

## 失败与回滚

- apply 目标 YAML 后 P/D 也在滚：立刻停。对照第 4 步 diff，确认是不是误 apply
  了 engine YAML 或跑了无 `--dry-run` 的 `deploy.py`。
- 新 Pod 起不来 / 找不到 whl：先看 volumeMount 是否覆盖了目标路径，再看角色脚本
  里的绝对路径是否和包放置路径一致。不要反复盲目 restart。
- ConfigMap 回读仍是旧值：刷新命令没在 `$DEPLOYER` 执行，或 `--from-file` 指向了
  另一份 `user_config.json`。
- Pod 内配置仍是旧值但 ConfigMap 已新：启动阶段又把别的文件拷到了
  `CONFIG_PATH`。查 `common.sh` 的 `sync_user_config`，不要只看宿主机。
- 回滚配置：用刷新前备份的 ConfigMap / 旧 whl 路径 / 旧模板，再走同一套「先
  ConfigMap，再只 apply 目标 YAML」，仍然不要全量 deploy。

## 和其它手册的分工

| 场景 | 走哪份 |
|---|---|
| 换 yaml_template + user_config + 组件 whl，只重启 Controller 或 Coordinator | 本手册 |
| 只改运行中 ConfigMap 的一个 JSON 字段 | `component-config-rollout.md` |
| P/D 也要装同一个 Motor whl | `motor-build-wheel`（共享 `boot.sh`） |
| 首次部署或整体重建 | 全量 `deploy.py`（会重启 P/D） |
