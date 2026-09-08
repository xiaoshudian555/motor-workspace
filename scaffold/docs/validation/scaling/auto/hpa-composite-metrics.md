# HPA 复合指标配置与自动验证 SOP

适用：MindCluster Infer Operator 自定义资源弹性扩缩容和 MindIE Motor 推理服务。

## 目标

验证 A+B 等复合指标能够通过 Prometheus Adapter 暴露为 Kubernetes External Metrics，
并被 HPA 引用后驱动 Prefill 或 Decode 实例自动扩缩容。

## 原理

```text
推理实例上报指标(coordinator/engine 的 /metrics)
  -> Prometheus 采集
  -> prometheus-adapter externalRules 合成对外指标
  -> HPA 引用 external metric 和 selector
  -> Infer Operator 调整对应 role 的副本数
```

关键结论：

- 复合指标公式写在 adapter ConfigMap 的 `externalRules.metricsQuery`。
- HPA 只通过 `metric.name` 引用 adapter 的 `name.as`。
- 单集群多任务隔离必须同时筛选 `kubernetes_namespace` 和
  `infer_huawei_com_inferservice_name`。

## 前置检查

```bash
kubectl get inferserviceset,inferservice,instanceset -A
kubectl get apiservice v1beta1.external.metrics.k8s.io v1beta1.custom.metrics.k8s.io
```

判据：

- 目标 namespace 下存在 `InferServiceSet -> InferService -> InstanceSet`。
- `v1beta1.external.metrics.k8s.io` 为 `Available=True`。
- 任务能够正常推理。
- 使用隔离任务验证，不使用生产任务。

## 配置复合指标

adapter 规则在集群 ConfigMap 中，以 prometheus-adapter Deployment 实际挂载的
ConfigMap 为准：

```bash
kubectl -n kube-system get cm
kubectl -n kube-system get deploy prometheus-adapter -o yaml | grep -A2 configMap
kubectl -n kube-system get cm <adapter-config-name> -o yaml
```

`externalRules` 示例：

```yaml
externalRules:
- seriesQuery: '<metric-a>'
  resources:
    overrides:
      kubernetes_namespace:
        resource: namespace
      pod:
        resource: pod
  name:
    as: '<external-metric-name>'
    matches: '.*'
  metricsQuery: |
    sum(
      (<metric-a>{<<.LabelMatchers>>}
       + on (namespace, pod) group_left
       <metric-b>{<<.LabelMatchers>>})
    ) by (namespace, pod)
```

改完后重启 adapter：

```bash
kubectl -n kube-system rollout restart deployment prometheus-adapter
```

## 配置 scalingPolicy

在 InferServiceSet 模板中给目标 role 添加 `scalingPolicy`：

```yaml
- name: <role>
  replicas: <initial-replicas>
  workload:
    apiVersion: apps/v1
    kind: StatefulSet
  scalingPolicy:
    type: HPA
    spec:
      minReplicas: 1
      maxReplicas: <max-replicas>
      metrics:
      - type: External
        external:
          metric:
            name: <external-metric-name>
            selector:
              matchLabels:
                kubernetes_namespace: <placeholder>
                infer_huawei_com_inferservice_name: <placeholder>
          target:
            type: AverageValue
            averageValue: '<threshold>'
```

生成器只改写模板中已存在的两个标签：

- `kubernetes_namespace` 改写为 `job_id`。
- `infer_huawei_com_inferservice_name` 改写为 `<InferServiceSet-name>-0`。

缺失标签不会自动补充。非 HPA 策略不改写。

## 自动验证流程

设置环境变量：

```bash
NS=<namespace>
SET=<inferserviceset-name>
MNAME=<external-metric-name>
META_A=<metric-a>
META_B=<metric-b>
```

### 阶段 0：环境自检

```bash
kubectl get iss,is,instanceset -n "$NS" | grep -E "$SET|NAME"
kubectl get apiservice v1beta1.external.metrics.k8s.io | grep -q True && echo "adapter OK"
```

判据：目标对象齐全，External Metrics API 可用。

### 阶段 1：数据链验证

```bash
curl -G "<prometheus-url>/api/v1/query" --data-urlencode "query=$META_A" | jq '.data.result[0].metric'
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/$NS/$MNAME" | jq .
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/$NS/$MNAME?labelSelector=kubernetes_namespace%3D$NS,infer_huawei_com_inferservice_name%3D${SET}-0" | jq .
```

判据：

- Prometheus 中 A、B 原始指标非空。
- adapter 暴露的复合指标非空。
- 带两个 selector 标签时只命中本任务数据。

### 阶段 2：生成链 dry-run

```bash
cd <deployer-dir>
python3 deploy.py --config_dir <config-dir> --dry-run
```

检查生成的 `infer_service.yaml`：

```bash
python3 - <<'PY'
import yaml

with open('<output-dir>/infer_service.yaml', encoding='utf-8') as f:
    docs = yaml.safe_load_all(f)
    d = next(x for x in docs if x and x.get('kind') == 'InferServiceSet')

roles = {r['name']: r for r in d['spec']['template']['roles']}
for role in ('<role>',):
    metric = roles[role]['scalingPolicy']['spec']['metrics'][0]['external']['metric']
    print(role, metric['name'], metric['selector']['matchLabels'])
PY
```

判据：

- `metric.name` 等于 adapter 的 `name.as`。
- `kubernetes_namespace == job_id`。
- `infer_huawei_com_inferservice_name == <SET>-0`。

### 阶段 3：下发与生效

```bash
cd <deployer-dir>
python3 deploy.py --config_dir <config-dir>
kubectl get hpa -n "$NS" -o yaml | grep -B2 -A8 "metrics:"
```

判据：

- operator 已按 `scalingPolicy` 生成 HPA。
- `status.currentMetrics` 有值。
- HPA event 中没有 `FailedGetExternalMetric`。

### 阶段 4：行为验证

记录基线：

```bash
kubectl get instanceset -n "$NS" | grep -E "prefill|decode"
```

打流触发扩容：

```bash
for i in $(seq 1 200); do
  curl -s -X POST <service-url>/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"<model-name>","messages":[{"role":"user","content":"hi"}],"max_tokens":36}' &
  sleep 0.02
done
wait
```

观察：

```bash
kubectl get hpa -A -w
kubectl get pod -n "$NS" -w
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/$NS/$MNAME" | jq .
```

判据：

- 负载升高后 external metric 超过阈值。
- HPA 目标副本数上升。
- Prefill 或 Decode Pod 数增长。
- 打流停止、指标回落后，副本数按 HPA 缩容窗口回落。

### 阶段 5：清理与归档

```bash
kubectl get hpa -n "$NS" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.currentMetrics}{"\n"}{end}'
```

记录验证结果，然后按授权范围移除 `scalingPolicy` 或恢复基线配置。

## 常见失败倒查

| 现象 | 直接原因 | 处理 |
|---|---|---|
| HPA 一直 `FailedGetExternalMetric` | `metric.name` 与 adapter `name.as` 不一致；selector 标签不匹配；ConfigMap 改完未重启 adapter | 跑阶段 1b/1c，重启 prometheus-adapter |
| 复合指标查不到 | adapter 规则未命中；A、B 原始指标未进 Prometheus | 查原始指标、Service scrape 注解和 `seriesQuery` |
| 多任务互相触发扩缩容 | 只筛了实例名，缺少 namespace 标签 | selector 同时配置两个标签 |
| HPA 有目标值但 Pod 不变 | `scaleTargetRef` 指向错误 role/InstanceSet，或 operator 不支持该路径 | 查看 HPA YAML 和 operator event |
| A+B join 结果为空 | A、B 标签集合不一致或 join key 不对 | 用 `on (namespace, pod) group_left` 并统一聚合维度 |

失败后进入 `motor-diagnosis`，不要在同一次验证里直接扩展为配置修复、重启或重部署。
