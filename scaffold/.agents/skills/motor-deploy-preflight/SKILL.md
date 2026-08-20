---
name: motor-deploy-preflight
description: Read-only K8s and MindCluster checks before Motor deploy. Use for environment preflight, 部署前检查, or 检查部署环境.
---

# motor-deploy-preflight

在选中 endpoint 上用 `remote.bash` 做**只读**现状检查。`kube_context` 取 inventory，禁止回退到无关本机 kubeconfig。

本 skill **没有脚本**。禁止调用已删除的 `environment_preflight.py`、`mws_environment.py`、`mws_deploy.py`，禁止写 run-gate / run ID。Agent 自己跑 kubectl。

## 必读契约

检查 CRD / controller / NodePort **之前**，必须先读：

`scaffold/.agents/skills/motor-deploy-preflight/references/environment-contract.yaml`

按 `user_config.json` → `motor_deploy_config.deploy_mode` 查表，**禁止猜 CRD / operator 名**。契约里有的名字才是检查对象。

| 契约字段 | 用途 |
|---|---|
| `required_api_resources` | 所有模式硬性 CRD/API |
| `deploy_mode_api_resources[<mode>]` | 该模式额外硬性 API |
| `deploy_mode_api_resource_groups[<mode>]` | 该模式 one-of API（组内命中任一即过，记录命中项） |
| `component_patterns` | 所有模式组件名子串；待确认项（如 `noded`）见下文，其余硬性 |
| `deploy_mode_components[<mode>]` | 该模式额外硬性组件 |
| `deploy_mode_component_groups[<mode>]` | 该模式 one-of 组件（组内命中任一即过，记录命中项） |
| `npu_resource_name` | 节点应公布的 NPU resource |
| `scheduler_name` | 期望调度器 |
| `node_port_range` | NodePort 合法区间 |
| `default_node_ports[<mode>]` | 该模式模板默认 NodePort |

合法 `deploy_mode`：`infer_service_set` / `multi_deployment` / `single_container`（与 Motor `VALID_DEPLOY_MODES` 一致）。配置缺省 `deploy_mode` 时按 Motor 默认视为 `infer_service_set`。没有 `user_config.json` 时只跑全模式基础项（`required_api_resources` + `component_patterns`），并在结果里标注 `deploy_mode: null`。非法 `deploy_mode` **fail-closed**。

## kubectl（只读，fail-closed）

```bash
kubectl --context "$CTX" version
kubectl --context "$CTX" auth can-i get pods -A
kubectl --context "$CTX" get nodes
kubectl --context "$CTX" api-resources
kubectl --context "$CTX" get pods -A
kubectl --context "$CTX" get services -A
```

对契约查出的每一项，用真实输出判定：

1. API 可达、有读权限。不可达 / 权限不足 → **error，立即停**。
2. 可调度节点公布了 `npu_resource_name`。容量未逐卡核验时在报告里标 **待确认**，不得当通过。
3. `required_api_resources` + 当前 mode 的硬性 API：`api-resources` 里必须出现 `name` + `api_group`。缺一项 → **error**。
4. 当前 mode 的 one-of API 组：组内**至少一个** alternative 存在；全部缺失 → **error**。记录命中的 `name.api_group`。
5. `component_patterns` + 当前 mode 的硬性/one-of 组件：`get pods -A` 中名称含子串的 Pod 必须 `Running` 且 `Ready`。已核实硬性项缺失或非 Ready → **error**。one-of 组全部未命中 → **error**。标了待确认的 pattern 缺失 → warning，不改猜名字。
6. NodePort：取契约 `default_node_ports[<mode>]`（若配置里另有显式端口则一并检查）。须在 `node_port_range` 内、本批唯一、且 `get services -A` 未被占用。越界/重复 → **error**。集群已占用 → 报告占用方 + 范围内空闲候选，**等授权再改配置**；范围内无空闲 → **error**。禁止自动写回 `user_config.json`。
7. 若请求了配置可行性：检查 `image_name` 语法；节点镜像覆盖只做当前已有 Pod 的回退观察，可能漏报。准确覆盖走 `motor-image-distribution-check`。缺失记 warning，不在本 skill 里创建 DaemonSet。

## 待确认（本仓可核实范围）

契约字段保持原值。下列项当前 Motor 文档/deployer **未能完全核实**，缺失时按下面处理，**仍禁止改猜名字**：

- `noded`：Motor `environment_preparation.md` 列出的是 Device Plugin / ClusterD / Volcano / Infer Operator，未列 NodeD。无匹配 Pod → **warning + 待确认**，不因此 fail-closed。
- `ascendjobs` (`mindxdl.gitee.com`) / `ascend-operator`：契约保留为 one-of 备选。当前 Motor CRD 路径是 `InferServiceSet` (`mindcluster.huawei.com`) + `infer-operator`。已命中 infer 链路则备选可不存在；两者都缺才 **error**。
- 已核实、必须硬性：`podgroups.scheduling.volcano.sh`、`clusterd`、`ascend-device-plugin`、`volcano`、`huawei.com/Ascend910`、`scheduler_name: volcano`、契约 `default_node_ports`；`infer_service_set` 还需命中 `inferservicesets` 或契约备选之一、以及 `infer-operator` 或契约备选之一。`multi_deployment` / `single_container` 不要求 InferServiceSet / infer-operator。

## 不做

不创建资源、不改 namespace/RBAC、不拉业务探测、不覆盖远端源码、不声称「基础环境健康 = 服务能起来」。Preflight 探针本身只读，**不需要**额外授权。

返回短表：检查项 / 命令或证据 / 通过或失败。不写 run 记录。
