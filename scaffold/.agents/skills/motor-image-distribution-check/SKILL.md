---
name: motor-image-distribution-check
description: Verify which cluster nodes actually have a given container image on their local runtime. Use when the user asks to check image distribution across nodes, verify an image exists on all nodes, diagnose ErrImagePull/ImagePullBackOff risk before apply, or confirm 镜像分发/镜像覆盖. The agent runs the kubectl/ssh commands below directly; this skill provides the procedure, not a script.
---

# motor-image-distribution-check

验证指定镜像在 K8s 集群**每个可调度节点**的本地容器运行时里是否存在。

> **本 skill 不提供脚本**。下面是一组由 agent 直接执行的命令流程（ssh 到
> inventory 入口机 → kubectl）。**临时方案**：通过一个跑完即删的临时
> DaemonSet 把查询 Pod 调度到每个节点，读宿主机容器运行时 socket 列镜像。
> 原理与限制见 `references/approach.md`。
>
> **副作用**：会在集群里创建并删除一个 DaemonSet。本仓库约定 apply/delete 类
> 操作需明确授权——执行前先和用户确认。

## 边界

**消费**：machine inventory 中的入口机 ssh 与 `kube_context`、一个或多个待检查镜像引用。

**产出**：每个镜像在哪些节点缺失的清单（直接展示给用户）。

**不做**：不拉取/导入/分发镜像（补 load 见 `scaffold/docs/technical-debt.md`
TD-P2-07）；不修改业务负载；不验证 registry 可拉取性，只查节点本地是否已有。

## 前置

- 确定入口机 alias（如 `master-195`），从
  `.motor-workspace-local/machine-inventory.json` 读 `host` 与 `kube_context`。
- 集群容器运行时类型决定挂哪个 socket：`docker` → `/var/run/docker.sock`；
  `containerd` → `/run/containerd/containerd.sock`。可用
  `kubectl get nodes -o wide` 看 `CONTAINER-RUNTIME` 列判断。

## 执行流程

### 1. 列出全部可调度节点

```bash
ssh root@<entry-host> "kubectl get nodes"
```

记录节点名列表，后面逐节点对比覆盖。

### 2. 创建临时 DaemonSet

把下面的 manifest 写到入口机（heredoc 或 `kubectl apply -f -`），将
`<SCAN-IMAGE>` 换成一个**集群节点本地已有**的镜像作载体（可直接用某个待检查
镜像，`imagePullPolicy: IfNotPresent` 不触发拉取）。Docker 运行时挂
`/var/run/docker.sock`；containerd 改挂 `/run/containerd/containerd.sock` 并把
probe 命令换成 `ctr -n k8s.io images list`。

probe 容器把该节点镜像 tag 列表写入 **termination log**
（`/dev/termination-log`），之后从 Pod 对象的
`containerStatuses[].state.terminated.message` 读回——**不要用 `kubectl logs`**，
短生命周期 Pod 的 logs 子资源会被快速回收导致读不到。

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: mws-img-scan
  namespace: default
  labels: { app: mws-img-scan }
spec:
  selector:
    matchLabels: { app: mws-img-scan }
  template:
    metadata:
      labels: { app: mws-img-scan }
    spec:
      restartPolicy: OnFailure   # probe 成功 exit 0 后不再重启；Always 会 CrashLoopBackOff
      tolerations:
      - operator: Exists          # 通吃 taint，覆盖 master
      containers:
      - name: probe
        image: <SCAN-IMAGE>
        imagePullPolicy: IfNotPresent
        command: ["sh", "-c", "echo <BASE64-OF-PROBE> | base64 -d | python3"]
        volumeMounts:
        - { name: sock, mountPath: /var/run/docker.sock, readOnly: true }
      volumes:
      - name: sock
        hostPath: { path: /var/run/docker.sock }
```

probe 脚本（写入前先做 base64）：

```python
import http.client, socket, json
class U(http.client.HTTPConnection):
    def __init__(self, p):
        super().__init__("localhost"); self.p = p
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sock.connect(self.p)
c = U("/var/run/docker.sock")
c.request("GET", "/images/json")
data = json.loads(c.getresponse().read())
tags = set()
for img in data:
    for t in img.get("RepoTags") or []:
        tags.add(t)
open("/dev/termination-log", "w").write("\n".join(sorted(tags)))
```

> 说明：`build_kubectl_runner` 不透传 stdin，manifest 经 ssh 通道用
> `echo <b64> | base64 -d | kubectl apply -f -` 送达。

### 3. 等所有节点的 probe Pod 都终止

```bash
ssh root@<entry-host> "kubectl get pods -n default -l app=mws-img-scan -o wide"
```

**必须等到 Pod 数量 == 可调度节点数，且每个 probe 容器都已终止（`Completed`，
exit 0）** 再往下。DaemonSet 模板须设 `restartPolicy: OnFailure`——若用默认
`Always`，probe 成功退出后会被反复重启，表现为 `CrashLoopBackOff`（数据仍在
`lastState.terminated.message` 里，但等待逻辑会卡住）。

### 4. 逐 Pod 读 termination message

```bash
ssh root@<entry-host> "kubectl get pods -n default -l app=mws-img-scan -o json"
```

从每个 Pod 的 `status.containerStatuses[0].state.terminated.message` 取该节点
的镜像 tag 列表，结合 `spec.nodeName` 得到「节点 → 本地镜像集合」。

### 5. 删除临时 DaemonSet

```bash
ssh root@<entry-host> "kubectl delete daemonset mws-img-scan -n default"
```

### 6. 覆盖比对并报告

对每个待检查镜像：去掉 registry 前缀按 `name:tag` 归一化后，逐节点判断是否在
该节点的本地镜像集合里。输出：

- 每个镜像缺失的节点列表（缺失即 ErrImagePull 风险）；
- 全覆盖的镜像标记 ok；
- 任何 probe Pod 非 0 退出或读不到 message 的节点单独标注。

## 下游

`motor-deploy-preflight` 的 `image_node_coverage` 默认走「从当前已有 Pod 反推
节点镜像」的**回退探测**（自动跑时没有本 skill 可用）。需要准确覆盖结论时，
用本 skill 手动跑一遍为准。

## 参考

- `references/approach.md`：临时 DaemonSet 方案原理、为何不用 ssh 逐台查、局限。
- `scaffold/docs/technical-debt.md` TD-P2-07：补 load / 自动分发后续工作。
