# aisbench 在线推理压测必须先通过上下文与服务 Gate

## 目录

- [运行前输入](#运行前输入)
- [Preflight](#preflight)
- [工具配置](#工具配置)
- [Workload](#workload)
- [结果与证据](#结果与证据)
- [停止条件](#停止条件)
- [常见故障](#常见故障)

## 运行前输入

优先从 `deploy-complete`、对应 config bundle 和机器 inventory 推导值。只有无法从
证据中确定时才询问用户。

| 变量 | 来源或要求 |
|---|---|
| `MACHINE` | `deploy-complete.machine` |
| `USER_CONFIG_JSON` | deploy run 引用的 immutable bundle |
| `MODEL_NAME` | 各 active engine section 的 `served_model_name`，必须一致 |
| `HOST_IP` / `HOST_PORT` | 压测执行环境可达的 Coordinator inference Service |
| `BENCH_CONTAINER` | 用户指定的已有压测容器；不得猜测 |
| `BENCH_ROOT` | aisbench wrapper 所在目录；不得猜测 |
| `DATASET_DIR` | 数据集目录；不得猜测或下载 |
| workload 参数 | 用户目标或已确认的 benchmark profile |

## Preflight

### 1. 上下文长度 Gate

从 immutable `user_config.json` 读取所有 active engine section 的
`max_model_len`：

```bash
python3 - '<USER_CONFIG_JSON>' <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)

values = {}
for section in (
    "motor_engine_prefill_config",
    "motor_engine_decode_config",
    "motor_engine_union_config",
    "motor_engine_encode_config",
):
    engine_config = config.get(section, {}).get("engine_config", {})
    if "max_model_len" in engine_config:
        values[section] = int(engine_config["max_model_len"])

if not values:
    raise SystemExit("max_model_len is missing from all active engine sections")
if len(set(values.values())) != 1:
    raise SystemExit(f"max_model_len mismatch: {values}")
print(next(iter(values.values())))
PY
```

必须满足：

```text
input_len + output_len <= max_model_len
```

不满足时禁止开跑。调整 workload；如果要增大 `max_model_len`，返回 deploy 配置
流程并按其授权规则重新部署，Benchmark 不直接修改部署。

### 2. 服务 Gate

确认：

- deploy evidence chain 为 `status=ready`；
- Coordinator management `/readiness` 返回 HTTP 200 且 JSON `ready=true`；
- inference Service 在压测执行环境内可达；
- `MODEL_NAME` 与部署配置中的 served model name 完全一致；
- 模型加载完成，并在正式测量前完成已记录的 warmup；
- TLS / API key 已启用时，使用引用式 secret 输入，不把明文写入命令、Skill、
  tracked file 或结果文件。

## 工具配置

仅探测已有环境，不自动安装或升级：

```bash
docker exec <BENCH_CONTAINER> bash -lc '
set -euo pipefail
cd <BENCH_ROOT>
python3 -m pip show ais-bench-benchmark
test -d <DATASET_DIR>
'
```

确认 wrapper `config.py` 的这些字段与当前 run 一致：

```python
DATASET_PATH = "<DATASET_DIR>"
WORK_PATH = "<AIS_BENCH_LOCATION>"
MODEL_NAME = "<SERVED_MODEL_NAME>"
MODEL_PATH = "<MODEL_PATH>"
HOST_IP = "<TARGET_IP>"
HOST_PORT = "<TARGET_PORT>"
DEFAULT_PERFORMANCE_TEST = "default_perf"
OUTPUT_DIR = "./outputs/default"
POD_INFO = []
```

不要为了方便把临时值写回 tracked `config.py`。如果 wrapper 只能读取文件，先读
清现有格式，再在用户批准的 untracked/runtime 位置生成本次 run 的副本。

## Workload

每次运行前保存完整命令。先执行小流量 smoke；smoke 成功后再运行正式 workload。

### 基础性能

```bash
cd <BENCH_ROOT>
rm -f picked_ids.txt
python3 aisbench_test.py \
  --input_len <INPUT_LEN> \
  --output_len <OUTPUT_LEN> \
  --data_num <DATA_NUM> \
  --concurrency <CONCURRENCY> \
  --request_rate <REQUEST_RATE>
```

`picked_ids.txt` 是 aisbench wrapper 的 run-local 临时状态。只在已确认的
`BENCH_ROOT` 中删除这个确切文件，不使用宽泛 glob。

### Prefix Cache

```bash
python3 aisbench_test.py \
  --input_len <INPUT_LEN> \
  --output_len <OUTPUT_LEN> \
  --data_num <DATA_NUM> \
  --concurrency <CONCURRENCY> \
  --request_rate <REQUEST_RATE> \
  --dataset_type prefix_cache \
  --repeat_rate 0.5 \
  --prefix_test \
  --dp <DP_SIZE>
```

- 需要命中率证据时必须加 `--prefix_test`。
- P/D 分离场景必须按实际 DP 域配置 `POD_INFO`，否则不得声称 metrics 覆盖完整。
- 保存 `repeat_rate`、prefix 生成方式和 metrics 采集范围。

### 指定数据集

```bash
python3 aisbench_test.py \
  --dataset <DATASET_JSONL> \
  --output_len <OUTPUT_LEN> \
  --concurrency <CONCURRENCY> \
  --request_rate <REQUEST_RATE>
```

只有用户明确要求基础正确性检查且数据集带可判定答案时才增加：

```text
--test_accuracy
```

`--test_accuracy` 的结果不替代正式模型精度验收。

## 结果与证据

默认 wrapper 产物：

| 产物 | 默认位置 |
|---|---|
| 当前聚合 CSV | `<BENCH_ROOT>/aisbench_result.csv` |
| 当前日志 | `<BENCH_ROOT>/aisbench.log` |
| 历史输出 | `<BENCH_ROOT>/outputs/default/` |
| 服务端证据 | deploy run、Kubernetes logs 和对应时间窗 metrics |

把本次命令、resolved config、环境指纹和产物复制到独立的 run-scoped evidence
目录，避免下一次运行覆盖“当前”文件。至少提取：

- QPS；
- Output throughput；
- Total throughput；
- TTFT average / P90；
- TPOT average；
- E2E average；
- successful / failed request count。

只有 baseline 的硬件、模型、拓扑、实例数、DP、engine config、输入/输出长度、
并发/到达率、数据集和 aisbench 版本一致时，才能给出性能回归结论。否则只报告
本次绝对结果和不可比原因。

## 停止条件

遇到任一项立即停止正式压测并保留失败证据：

- 100% HTTP 400 或 500；
- `RECV=0 FAIL=N`；
- `input_len + output_len > max_model_len`；
- served model name 不一致；
- 聚合 perf 数据全为 `{}`；
- 同一个 Bad Request 原因重复出现；
- `symlink` / `temp_api` 错误在一次针对性修正后仍复现。

不得把全失败请求的耗时、空 metrics 或未完成的 run 汇总成性能结果。

## 常见故障

### `picked_ids.txt` 冲突

确认当前目录是本次 run 的 `BENCH_ROOT` 后，只删除：

```bash
rm -f picked_ids.txt
```

### wrapper 不支持 `--num-warmups`

先用 `--help` 和真实脚本确认版本。如果当前 wrapper 确实向下游传了不支持的
`--num-warmups`，在 runtime 副本中移除该参数并记录 diff；不要直接改第三方安装
目录或 tracked source。

### perf 数据为空

先检查成功/失败请求数和首个服务端错误。请求全部失败时，根因在 workload、服务
或协议，不在汇总器。

### Prefix Cache 命中率不显示

确认命令包含 `--prefix_test`。仍无结果时，保存 metrics 前后快照并计算同一 DP
域内 counter 的差值；采集范围不完整时将结果标为 unavailable。
