# user_config.json 配置结构总览

motor 的 `user_config.json` 顶层分节 + `version`。每节对应一个进程/引擎的配置。
**全量字段、类型、默认值、取值范围一律以 motor 官方文档为准**：

- 权威全量参数说明：`sources/motor/docs/zh/user_guide/configuration/config_reference.md`
- 全量配置样例（纯 JSON）：`sources/motor/examples/features/config_sample.json`

本文件只给结构速查和加载机制，不重复维护字段清单。

## 顶层结构

```text
version
motor_deploy_config           # deploy.py 读取：P/D 实例、卡数、镜像、job、硬件、权重挂载、TLS
motor_controller_config       # Controller 进程配置
motor_coordinator_config      # Coordinator 进程配置
motor_engine_union_config     # 单容器/融合形态的引擎配置
motor_engine_prefill_config   # P 引擎配置（vllm/sglang）
motor_engine_decode_config    # D 引擎配置（vllm/sglang）
motor_nodemanger_config       # NodeManager 进程配置（部署形态常用，含 basic_config 等）
```

## 加载机制（权威源）

- `sources/motor/motor/config/coordinator.py:496` `CoordinatorConfig.from_json`：
  若文件含 `motor_coordinator_config` 键则取该节，否则整份当 coordinator 配置。
  递归合并 `:529-541`：`hasattr(config_obj, key)` 且值为 dict 且已有同名字段是
  dataclass 时递归下钻；否则直接 `setattr`。
- `sources/motor/motor/config/controller.py:173`：同样处理 `motor_controller_config`。
- `sources/motor/motor/config/config_utils.py:70-71`：节名常量。

因此：**user_config.json 里写任意 `motor_coordinator_config.X`，只要 `X` 是
`CoordinatorConfig` 的字段（或嵌套 dataclass 字段），就会被加载。未配置的字段
使用代码默认值**——这正是不配就是默认的原理。

## 各节常见结构速查

### motor_deploy_config

| 字段 | 说明 |
|---|---|
| `p_instances_num` / `d_instances_num` | P/D 实例数，[1,16] |
| `single_p_instance_pod_num` / `single_d_instance_pod_num` | 每实例 Pod 数 |
| `p_pod_npu_num` / `d_pod_npu_num` | 每 Pod NPU 数（TP ≤ 它） |
| `image_name` | 业务镜像（必填） |
| `job_id` | namespace / 作业名（必填） |
| `hardware_type` | 硬件型号（必填） |
| `weight_mount_path` | 权重挂载根路径 |
| `tls_config.*` | mgmt/infer/etcd/grpc/observability 五组 TLS |

### motor_engine_prefill_config / motor_engine_decode_config

| 字段 | 说明 |
|---|---|
| `engine_type` | `vllm` / `sglang` |
| `engine_config.model` | 模型路径（P/D 一致） |
| `engine_config.served_model_name` | 对外模型名（P/D 一致） |
| `engine_config.tensor_parallel_size` | TP ≤ 对应 Pod NPU 数 |
| `engine_config.data_parallel_size` / `pipeline_parallel_size` | DP/PP |
| `engine_config.kv_transfer_config.kv_role` | prefill=`kv_producer`，decode=`kv_consumer` |
| `engine_config.kv_transfer_config.kv_port` | P/D 一致 |

### motor_coordinator_config（常用节）

| 节 | 用途 |
|---|---|
| `precision_detection_config` | 精度异常采样/检测/告警（新版名） |
| `token_sampling_config` | 旧名（deprecated，`precision_detection_config` 优先） |
| `scheduler_config` | 调度器、KV affinity |
| `api_key_config` | API Key 鉴权 |
| `rate_limit_config` | 限流 |
| `tracer_config` | Tracing（endpoint + 采样率） |
| `prometheus_metrics_config` | metrics 复用、KV store metrics |
| `exception_config` | 重试/超时 |
| `standby_config` / `etcd_config` | 主备与 etcd |
| `kv_event_registration` | KV conductor 注册 |

### motor_controller_config（常用节）

| 节 | 用途 |
|---|---|
| `precision_auto_recovery_enabled` | 精度告警自动恢复（bool，顶层字段） |
| `fault_tolerance_config` | 容错总开关、Scale P2D、token reinference |
| `observability_config` | observability 开关、metrics TTL |
| `api_config` | controller 端口/DNS |
| `standby_config` / `etcd_config` | 主备与 etcd |

> 注：官方文档 `config_reference.md` 中该字段写作 `precision_auto_recovery_enable`
> （单数，见 config_reference.md:122），源码 dataclass 为
> `precision_auto_recovery_enabled`（controller.py:151）。两者以源码为准，回写映射表时
> 记 `enabled`。
