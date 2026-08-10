# 临时 DaemonSet 镜像分发扫描方案

## 为什么不用 SSH 逐台查

最直接的想法是 `ssh root@<node> "docker images | grep <img>"`。在本仓库目标集群
上不可行，原因有二：

1. **本机到节点没有 host key / 互信**：除 inventory 登记的入口机（如
   master-195）外，其余节点第一次 ssh 会在 `BatchMode=yes` 下直接
   `Host key verification failed`。
2. **节点之间也没有 SSH 互信**：这套 kubeadm 集群只建有「开发机 → master」
   单入口信任，master 上没有到其他 worker 的免密私钥。逐台 ssh 的前提是先给
   所有节点配互信，侵入且扩节点时要重配。

这是 K8s 集群常态——日常操作通过 apiserver，节点不对外开放 ssh。

## 临时方案：DaemonSet + 宿主机运行时 socket

利用 K8s 自身调度能力，让集群把「查询 Pod」放到每个节点上：

```
ssh 到 inventory 入口机
  └─ apply 临时 DaemonSet（tolerations: Exists 通吃 taint，覆盖 master）
       └─ 每节点一个 probe Pod
            ├─ hostPath 挂载运行时 socket（docker.sock / containerd.sock）
            └─ 容器内 python3 调 runtime API 列镜像 → 写入 termination log → exit 0
  └─ 轮询直到每个 Pod 的 lastState.terminated.message 非空
  └─ delete DaemonSet --wait=false
  └─ per-node 覆盖比对 → 报告
```

### 为什么 probe 容器里用 python3 + http.client

- 现成的业务镜像（如 mindie-motor）**不带 docker CLI**，也没有支持
  `--unix-socket` 的 curl/wget；
- 但镜像里**一定有 python3**（PyMotor 运行时）；
- 于是用 `http.client.HTTPConnection` 子类把 `connect()` 换成 `AF_UNIX`
  socket 连 `/var/run/docker.sock`，发标准 `GET /images/json`；
- containerd 运行时则改用 `ctr -n k8s.io images list -q`。

### 结果怎么读：termination log + lastState

probe 脚本把镜像 tag 列表写入 `/dev/termination-log`，容器 exit 0 后 K8s 把
该内容存到 `containerStatuses[].lastState.terminated.message`。

**不要读 `kubectl logs`**——Pod logs 子资源会被快速回收。

**不要读 `state.terminated.message`**——probe 成功退出后 kubelet 会立刻重启
容器（见下节），当前 `state` 往往是 `waiting/CrashLoopBackOff`。

### DaemonSet 的 restartPolicy 陷阱（实测确认）

DaemonSet Pod **只支持 `restartPolicy: Always`**，不能设 `OnFailure`（apply
会直接报错：`Unsupported value: "OnFailure": supported values: "Always"`）。

因此 probe 成功 exit 0 后，kubelet 会**立即重启** probe 容器，Pod STATUS 显示
`CrashLoopBackOff`——**这是预期行为，不是 probe 失败**。

正确等待/读取方式：

| 错误做法 | 正确做法 |
|---------|---------|
| 等 Pod phase == `Completed` | 等 `lastState.terminated.exitCode == 0` 且 message 非空 |
| 看 `kubectl get pods` STATUS 列 | 看 `kubectl get pods -o json` 的 lastState |
| 读 `state.terminated.message` | 读 **`lastState.terminated.message`** |
| `kubectl delete ds` 默认等 Pod 删完 | `kubectl delete ds --wait=false` |

实测（master-195 集群，8 节点）：apply 后 **<2s** 全部节点 lastState 就绪。

### manifest 经 ssh 送达

`build_kubectl_runner` 的 `kubectl(*args)` 不支持 `apply -f -` 的 stdin。
manifest 通过 ssh 通道用 `echo <base64> | base64 -d | kubectl apply -f -` 送达。

## 为什么标记为临时方案

- 需要在集群里**创建并删除 DaemonSet**，属于有副作用的写操作；
- 依赖节点本地存在可用作载体的镜像（默认取某个待检查镜像本身，
  `imagePullPolicy: IfNotPresent`，不触发拉取）；
- 理想替代：
  - 节点本地镜像列表由某个守护进程上报到 apiserver / Node status；
  - 或统一改用带 registry 前缀的镜像 + `imagePullPolicy: Always`，让 K8s
    自行拉取，消除「节点本地必须有镜像」的前置条件；
  - 或把 `crictl` 探测下沉为一个常驻 node agent。

在上述更稳路径落地前，本方案以「用完即删」把副作用控制在最小。

## 局限

| 局限 | 说明 |
|------|------|
| 运行时类型 | 需正确选择 docker/containerd；socket 路径不匹配时该节点探测失败记 warning |
| 权限 | 需要 `create/delete daemonsets`、`get/list pods` 权限 |
| 载体镜像 | probe Pod 载体镜像需在节点本地已有（IfNotPresent），否则 Pod 起不来 |
| 覆盖语义 | 只证明「节点本地 runtime 已有该 tag」，不证明可从 registry 拉取 |
| CrashLoopBackOff 外观 | probe 成功后 Pod 会 CrashLoopBackOff，不影响数据读取，但 delete 前不要 panic |
| 清理 | 用 `--wait=false` delete；极端中断可能残留，需人工 `kubectl delete ds mws-img-scan` |
