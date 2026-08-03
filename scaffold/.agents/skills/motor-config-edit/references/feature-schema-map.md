# Feature → Schema 映射表

> **全量字段权威源**（先读这个，别再维护第二份全量清单）：
>
> 1. `sources/motor/docs/zh/user_guide/configuration/config_reference.md` —
>    **官方全量参数说明**：每一节的每个字段都有 类型 / 默认值 / 取值范围 / 说明。
>    覆盖 `motor_deploy_config`、`motor_controller_config`、
>    `motor_coordinator_config`、`motor_engine_union_config`、
>    `motor_engine_prefill_config`/`motor_engine_decode_config` 及 env。
> 2. `sources/motor/examples/features/config_sample.json` — 全量配置样例
>    （纯 JSON 快照，与 config_reference.md 结构一一对应）。
>
> **用法**：agent 要改任何字段，先在本表查「常用 feature 索引」；命中就直接用。
> 索引没有的字段，读 config_reference.md 对应节确认完整路径、类型和默认值；
> 仍未覆盖的在 `sources/motor/motor/config/` 源码里搜（dataclass 定义 /
> `from_json` / tests）。
>
> **维护规则**：`sources/motor/` 是权威源，本表只是常用 feature 的快捷索引。
> 新增 feature 必须有源码出处（`文件:行号`）。motor 版本变化时，索引里过期的
> 路径以 config_reference.md / 源码为准，并及时更新本表。

---

## 常用 feature 索引

### A. 部署形态与模型（motor_deploy_config / motor_engine_*_config）

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 起多少个 P/D 实例 | `motor_deploy_config.p_instances_num` / `d_instances_num` | int，[1,16]，默认 1 | config_reference.md:31-32 |
| 每个 P/D 实例几个 Pod | `motor_deploy_config.single_p_instance_pod_num` / `single_d_instance_pod_num` | int ≥1，默认 1 | config_reference.md:33-34 |
| 每个 P/D Pod 多少张卡 | `motor_deploy_config.p_pod_npu_num` / `d_pod_npu_num` | int ≤16 | config_reference.md:35-36 |
| 镜像 | `motor_deploy_config.image_name` | str（**首次必问**，无默认） | config_reference.md:37 |
| 命名空间/job | `motor_deploy_config.job_id` | str，默认 `mindie-motor`（**首次必问**） | config_reference.md:38 |
| 硬件类型 | `motor_deploy_config.hardware_type` | `800I_A2`/`800I_A3`/`850-Atlas-8p-8`/`850-SuperPod-Atlas-8`（**首次必问**） | config_reference.md:39 |
| 权重挂载根 | `motor_deploy_config.weight_mount_path` | str，默认 `/mnt/weight/` | config_reference.md:40 |
| 模型路径 | `motor_engine_prefill_config.engine_config.model` / `motor_engine_decode_config.engine_config.model` | 绝对路径（**首次必问**）；P/D 一致 | config_reference.md:594+ |
| served model 名 | `...engine_config.served_model_name` | str（**首次必问**）；P/D 一致 | config_reference.md:594+ |
| 引擎类型 | `motor_engine_*_config.engine_type` | `vllm` / `sglang` | config_reference.md:594+ |
| 张量并行 | `...engine_config.tensor_parallel_size` | int ≤ 对应 Pod NPU 数 | config_reference.md:594+ |
| KV 角色 | `...engine_config.kv_transfer_config.kv_role` | prefill=`kv_producer`；decode=`kv_consumer` | config_reference.md:594+ |
| KV 端口 | `...engine_config.kv_transfer_config.kv_port` | P/D 一致 | config_reference.md:594+ |

### B. 精度异常检测（precision detection）

> **命名注意**：官方文档 `config_reference.md:335,464` 仍用旧名
> `token_sampling_config`；源码已改为 `precision_detection_config`
>（`coordinator.py:295`），且 `test_config.py:323` 证明新旧名同时存在时
> **新名优先**。映射表以源码为准用新名。

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 开启精度检测（采样+检测+告警） | `motor_coordinator_config.precision_detection_config.precision_check_enabled` | `true`（默认 false） | `sources/motor/motor/config/coordinator.py:309` |
| 采样间隔 | `...precision_detection_config.interval_seconds` | float，默认 30.0 | coordinator.py:305 |
| 注入 logprobs 数 | `...precision_detection_config.logprobs_count` | int；1→repetition；≥3→+garbled；≥5→+rare chars | coordinator.py:306-308 |
| 告警触发阈值 | `...precision_detection_config.precision_issue_threshold` | int，默认 10 | coordinator.py:310 |
| 告警清除阈值 | `...precision_detection_config.precision_clear_threshold` | int，默认 10 | coordinator.py:311 |
| 探针次数/超时 | `...precision_detection_config.probe_max_attempts` / `probe_timeout_seconds` | 3 / 600.0 | coordinator.py:312-315 |
| 精度告警自动恢复（Controller） | `motor_controller_config.precision_auto_recovery_enabled` | `true`（默认 false） | `sources/motor/motor/config/controller.py:151` |

### C. 可观测性（observability / tracing）

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 开启 observability | `motor_controller_config.observability_config.observability_enable` | `true`（默认 false） | `sources/motor/motor/config/controller.py:62` |
| metrics TTL | `motor_controller_config.observability_config.metrics_ttl` | int，默认 5 | controller.py:64 |
| 开启 Tracing | `motor_coordinator_config.tracer_config.endpoint` | 非空 OTLP 端点 | config_reference.md:193+ |
| Tracing 采样率 | `motor_coordinator_config.tracer_config.root_sampling_rate` | float 0-1 | config_reference.md:193+ |

### D. 安全（security）

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 开启 API Key 鉴权 | `motor_coordinator_config.api_key_config.enable_api_key` | `true`（默认 false） | config_reference.md:193+ |
| 合法 key 列表 | `motor_coordinator_config.api_key_config.valid_keys` | string[] | config_reference.md:193+ |
| Header 名/前缀 | `...api_key_config.header_name` / `key_prefix` | `Authorization` / `Bearer ` | config_reference.md:193+ |
| 开启限流 | `motor_coordinator_config.rate_limit_config.enable_rate_limit` | `true`（默认 false） | config_reference.md:193+ |
| 限流窗口/上限 | `...rate_limit_config.window_size` / `max_requests` | int | config_reference.md:193+ |
| 开启 TLS（五类） | `motor_deploy_config.tls_config.<mgmt\|infer\|etcd\|grpc\|observability>_tls_config.enable_tls` | `true` | config_reference.md:41 |

### E. 容错与高可用

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 开启容错 | `motor_controller_config.fault_tolerance_config.enable_fault_tolerance` | bool，默认 true | `sources/motor/motor/config/controller.py:110` |
| Scale P2D 策略 | `...fault_tolerance_config.enable_scale_p2d` | bool | controller.py:127 |
| Token 重新推理 | `...fault_tolerance_config.enable_token_reinference` | bool | controller.py:128 |
| 开启主备 | `motor_controller_config.standby_config.enable_master_standby`（或 coordinator 同名） | `true` | config_reference.md:100-107 |

### F. 调度与 KV 亲和

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 调度器类型 | `motor_coordinator_config.scheduler_config.scheduler_type` | `load_balance` | config_reference.md:193+ |
| KV affinity 模式 | `...scheduler_config.kv_affinity_mode` | `unified` / `load_gated` | `sources/motor/motor/config/coordinator.py:231` |
| PD 分离回退 hybrid | `...scheduler_config.enable_pd_separation_fallback_to_hybrid` | bool，默认 true | coordinator.py:223 |

### G. 其它常用

| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| 异常重试次数 | `motor_coordinator_config.exception_config.max_retry` | int | config_reference.md:193+ |
| 首 token 超时 | `motor_coordinator_config.exception_config.first_token_timeout` | int | config_reference.md:193+ |
| 推理超时 | `motor_coordinator_config.exception_config.infer_timeout` | int | config_reference.md:193+ |

---

## 回写规则

通过 config_reference.md / 源码确认的新 feature，按上面 A–G 的分类追加表格行：

```markdown
| 意图示例 | 字段路径 | 取值 / 默认 | 出处 |
|---|---|---|---|
| <用户怎么说> | `<点分路径>` | <取值/约束> | `<文件:行号>` |
```

同一 feature 多字段时「开关在前、辅助在后」。出处优先写 `config_reference.md:行号`
（带类型和说明），其次写源码 `文件:行号`。
