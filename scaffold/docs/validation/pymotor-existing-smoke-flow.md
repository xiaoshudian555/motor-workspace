# Pymotor 现有“冒烟测试”流程基线

> 记录日期：2026-08-20  
> 状态：现状记录（as-is），供后续全 Agent 化设计使用；不是新的执行规范。

## 1. 范围和术语

现场把这一组 Pymotor 自动化用例统称为“冒烟测试”。它实际覆盖：

- 部署和在线扩缩容；
- RAS 高可用、故障隔离和实例恢复；
- 推理功能探测；
- AISBench 精度与性能测试。

这与当前 workspace 的场景分类不同：这里的 `motor-smoke` 只表示 Coordinator
management `/readiness` 返回 `ready=true`。后续 Agent 化时，应把现场“冒烟测试”
作为一条验收流水线，分别路由到 deploy、smoke、functional、reliability、correctness
和 benchmark，而不是把所有动作塞进 `motor-smoke`。

## 2. 现有用例框架

所有用例通过多层基类复用“配置 → 拉服务 → 验证 → 清理”的通用逻辑。

| 基类 | 职责 |
|---|---|
| `pymotor_script_from_images` | 从镜像 `docker cp` 部署脚本到宿主机，SSH 登录主机，创建 `job_id=pymotor-vllm` 的 K8s namespace |
| `pymotor_start_service` | 执行 `deploy.py`，轮询 Coordinator 日志等待实例就绪，curl 验证推理，使用 `delete.sh` 停止服务 |
| `pymotor_set_config_ep` | 提供 EP 模式默认参数，修改 `user_config.json` 和 `env.json` 模板 |
| `pymotor_set_config_dense` | 提供 Dense/Qwen 模式默认参数，修改配置模板 |

用例通常只重写以下生命周期方法：

| 方法 | 用途 |
|---|---|
| `createMetaData()` | 修改实例数、主备开关等默认参数 |
| `preTestCase()` | 注入虚推、性能参数等额外配置 |
| `procedure()` | 扩容、故障注入或 AISBench 等核心步骤 |
| `postTestCase()` | 恢复故障并清理环境 |

## 3. 通用执行流程

1. **准备环境**：SSH 登录宿主机，从镜像拷贝 `examples/`，创建 namespace。
2. **修改配置**：使用 `set_json_field`、`insert_row` 等方法修改远端
   `user_config.json`、`env.json`，包括镜像、job ID、P/D 实例数、TP/DP、EP、
   Mooncake KV connector、`HCCL_BUFFSIZE` 和其他环境变量。
3. **拉起服务**：后台执行：

   ```bash
   python3 deploy.py --user_config_path <path> --env_config_path <path>
   ```

4. **等待实例拓扑就绪**：找到 Coordinator Pod 并轮询日志，等待：

   ```text
   Refresh instances done: E=X, P=Y, D=Z, U=0
   ```

5. **验证推理**：请求 `/v1/completions`，响应包含 `text` 视为成功。
6. **清理服务**：执行 `bash delete.sh <job_id>`。

当前用例把 Coordinator 日志中的实例计数同时用于初次就绪、扩缩容完成、故障隔离
和实例恢复判断；把 completions 响应中的 `text` 作为服务可用性的最终探针。

## 4. 已覆盖的代表性场景

### 4.1 在线扩容：`pymotor_ras_up_down_scale_0001`

目标：验证 1P1D 在线扩容为 2P1D 后仍可推理。

1. 基类拉起 1P1D。
2. 将 `user_config.json` 中 `p_instances_num` 从 1 改为 2。
3. 执行 `python deploy.py --config_dir ../infer_engines/vllm --update_instance_num`。
4. 等待 Coordinator 日志出现 `P=2`。
5. 发送推理请求确认服务可用。

### 4.2 Coordinator 主备切换：`ras_coordinator_active_passive_0004`

目标：验证 Coordinator 主进程被强杀后自动切换且服务不中断。

1. 开启 Coordinator 主备并拉起服务。
2. 在 Coordinator Pod 内定位 `motor.coordinator.main` 主进程并执行 `kill -9`。
3. 等待 30 秒，再确认 Coordinator Pod 仍为 `1/1` Running。
4. 发送推理请求确认服务可用。

### 4.3 P 实例掉卡隔离：`ras_p_redundant_0001`

目标：验证 P 节点单卡参数面 linkdown 后，故障实例被隔离且冗余 P 自动补齐。

1. 拉起 1P1D，开启 `enable_virtual_inference` 供健康检查使用。
2. SSH 登录 P 节点，执行 `hccn_tool -i 0 -link -s down` 注入 NPU 参数面故障。
3. 等待 Coordinator 日志从 `P=1` 变为 `P=0`。
4. 此时推理应失败，响应中不应包含 `text`。
5. 等待冗余 P 拉起、日志恢复 `P=1`，再次验证推理成功。
6. 恢复被注入的链路并清理环境。

### 4.4 D 实例进程重拉：`ras_pd_restart_0002`

目标：验证 Decode Pod 中 EngineServer 被强杀后，D 实例能够整体重拉恢复。

1. 拉起服务。
2. 在 Decode Pod 内定位 EngineServer 并执行 `kill -9`。
3. 等待 30 秒，再轮询 Coordinator 日志直至恢复 `D=1`。
4. 发送推理请求确认服务恢复。

### 4.5 GPQA 精度：`pymotor_acc_0008`

目标：使用 AISBench 和 `GPQA_diamond` 验证答题准确率。

1. 进入 AISBench 容器，定位 `ais-bench-benchmark`。
2. 修改 `vllm_api_general_chat.py`：模型、权重、服务地址、端口 31015、
   `max_out_len=32768`、`batch_size=32` 和 `trust_remote_code`。
3. 设置 `temperature=1.0`、`top_p=0.95`、
   `chat_template_kwargs={"thinking": true}`。
4. 执行：

   ```bash
   ais_bench --models vllm_api_general_chat \
     --datasets gpqa_gen_0_shot_cot_chat_prompt --debug
   ```

5. 从 `results/.../GPQA_diamond.json` 读取 accuracy 百分比并转换为小数。
6. 以 0.807 为基线；满足 `abs(actual - 0.807) <= 0.01` 或
   `actual > 0.807` 时通过，即低于 0.797 判失败。

### 4.6 GSM8K 性能：`pymotor_performance_0009`

目标：用 GSM8K prompt 做并发打流，验证输出吞吐和失败请求数。

1. 配置 cudagraph、KV transfer、flashcomm、fused MC2 和超时环境变量。
2. 进入 AISBench 容器，将 `GSM8K-in3500-bs1800.jsonl` 复制为 `test.jsonl`，
   并更新数据集 path。
3. 修改 `vllm_api_stream_chat.py`：模型、权重、端口 31015、
   `max_out_len=1500`、`batch_size=450`、`request_rate=8` 和
   `trust_remote_code`。
4. 先用 `--num-prompt 200` 预热。
5. 正式执行：

   ```bash
   ais_bench --models vllm_api_stream_chat \
     --datasets gsm8k_gen_0_shot_cot_str_perf \
     --debug -m perf --num-prompt 1800
   ```

6. 从 `performance.txt` 解析 `Output Token Throughput`、`Failed Requests`、
   TTFT 和 TPOT。
7. 通过条件为 `Failed Requests == 0` 且
   `Output Token Throughput / 7400 > 0.97`。`tp_size=64` 时，另记录总吞吐除以
   64 的单卡参考值。

## 5. Agent 化的建议拆分

后续可把整条验收流水线拆成以下可组合阶段，每个阶段都输出结构化结果和原始证据：

| 阶段 | 输入 | 输出/判据 | workspace 路由 |
|---|---|---|---|
| 环境预检 | cluster、namespace、镜像、配置路径 | context、资源、路径、dry-run 证据 | deploy preflight |
| 配置计划 | 目标拓扑、模型和镜像参数 | 配置 diff；不直接落盘 | deploy configure/plan |
| 部署 | 经确认的配置 | workload Ready、Service endpoint | deploy apply |
| 管理面就绪 | Coordinator endpoint | HTTP 200 且 `ready=true` | `motor-smoke` |
| 推理功能 | inference endpoint、请求样本 | 协议、状态码、有效响应 | functional |
| 故障实验 | 故障类型、目标、恢复条件 | 注入前/中/后拓扑和请求证据 | `motor-reliability`（当前支持三个场景） |
| 精度 | 数据集、生成参数、阈值 | 原始结果、accuracy、阈值结论 | correctness/benchmark |
| 性能 | workload、并发、速率、阈值 | 原始 AISBench 产物和指标结论 | `motor-benchmark` |
| 清理/恢复 | 本次创建或修改的资源清单 | 恢复证据和残留检查 | deploy stop/recovery |

建议统一保存一份 run manifest，至少包含：

- run ID、操作者、时间、目标集群和 namespace；
- Motor、vLLM、vLLM Ascend、镜像和 AISBench 版本/哈希；
- 部署前后配置摘要与目标拓扑；
- 每个动作的命令、开始/结束时间、退出码和原始输出路径；
- 每个断言的期望值、实际值、证据路径和最终状态；
- 故障注入目标、恢复动作，以及清理是否完整。

## 6. Agent 执行边界

以下边界应在编排层显式建模：

- 环境预检和 deploy dry-run 保持只读。
- 修改远端配置、部署、扩缩容、停止服务、重启、故障注入均需逐项明确授权。
- `kill -9` 和 `hccn_tool ... link down` 必须绑定精确目标、超时和恢复动作；恢复动作
  不能只放在成功路径，失败和超时也必须执行或升级为人工接管。
- Agent 不应直接修改共享 AISBench 安装目录；应使用 run-scoped 的可变工作目录，并保存
  完整原始产物。
- 不把 Pod Ready、Coordinator 实例计数、management readiness 和推理成功互相替代；
  它们是不同层次的独立断言。
- 性能正式运行的参数和命令需要按当前 `motor-benchmark` contract 再确认；本记录中的
  `--debug` 是旧用例现状，不代表后续正式 benchmark 的推荐配置。

## 7. 当前自动化缺口

- 现有“冒烟”名称混合了部署、功能、可靠性、精度和性能，失败归因不够清晰。
- 日志字符串和响应是否含 `text` 的断言较弱，缺少状态码、JSON schema、请求 ID 和
  时间窗口等结构化证据。
- 固定等待 30 秒应改为有上限、有采样记录的状态轮询。
- 故障注入后的恢复与清理需要做成强制补偿步骤。
- 精度/性能用例会就地修改 AISBench 接口文件和数据集文件，不利于并发运行和复现。
- 写死端口、基线、并发和请求速率应进入带版本的测试 profile。
- `motor-reliability` 首版已有三个场景契约，但尚未完成真实集群纵向验收和各失败路径的
  专项 diagnosis；其他 RAS 场景仍不能由 generic shell 或 `motor-smoke` 隐式承担。

## 8. 已落地的编排入口

`scaffold/.agents/skills/motor-smoke-suite/` 已提供第一版冒烟验收编排：

```text
当前 K8s 状态
→ Coordinator readiness
→ non-stream inference
→ stream inference
→ 任一失败时自动进入只读 diagnosis
```

该 Skill 复用 `motor-k8s-deploy`、`motor-smoke`、`motor-functional`、
`motor-diagnosis`、`motor-benchmark` 和 `motor-performance-analysis`，不复制原子能力。
RAS 场景现可显式路由到 `motor-reliability` 支持的 Coordinator 主备切换、Decode
EngineServer 重拉和 Prefill NPU link 故障隔离/冗余恢复，但不属于默认冒烟，且注入前
需要独立授权。GPQA correctness 仍未实现；请求包含未支持阶段时必须报告
`CAPABILITY GAP`，不能以基础冒烟通过代替。
