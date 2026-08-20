# motor-benchmark 与 aisbench wrapper 审查记录

## 状态

本文记录 2026-08-20 对 `motor-benchmark` Skill、AISBench 上游和现场 wrapper
的设计审查与修改思路。实际压测仍以
[`motor-benchmark` Skill](../../../.agents/skills/motor-benchmark/SKILL.md) 为准。

2026-08-20 已把 native-first 路由和本文两个 P0 分组写入 Skill 执行契约：普通
性能、指定数据集和稳态优先使用原生 `ais_bench`；只有公共前缀比例/数量、seed、
逐 DP 预热和自动命中率证据需要 wrapper。结构化落盘目前仍由 Skill 约束 agent
完成，尚无统一解析脚本。

## 背景与讨论过程

1. 最初的问题是：参考 AISBench 官方仓库，检查 workspace 中
   `motor-benchmark` Skill 还缺什么。
2. 第一轮只对照 AISBench 官方 `master`，发现当前 Skill 使用的
   `aisbench_test.py`、`repeat_rate`、`prefix_test` 等并非官方 CLI，因此初步判断
   Skill 混用了自定义 wrapper 和官方接口。
3. 随后确认现场实际使用
   [`rayn-zzz/aisbench_auto_tools_prefix`](https://github.com/rayn-zzz/aisbench_auto_tools_prefix)
   作为 AISBench wrapper。重新阅读 wrapper 后，确认这些参数、数据集生成、前缀
   预热和命中率采集均是 wrapper 提供的正式能力，不是 Skill 凭空假设的参数。
4. 因此修正第一轮结论：当前主路径应明确建模为
   `motor-benchmark -> aisbench_auto_tools_prefix -> AISBench`。真正的缺口主要在
   wrapper 版本兼容、失败判定、证据结构、运行目录副作用和 prefix 指标可信度，
   而不是简单替换成官方 CLI。

## 审查过的仓库与文件

### motor-workspace

- `scaffold/.agents/skills/motor-benchmark/SKILL.md`
- `scaffold/.agents/skills/motor-benchmark/references/aisbench.md`
- `scaffold/docs/technical-debt.md` 中的 `TD-P2-03`
- 本目录的 benchmark 职责与完成标准

### AISBench 官方仓库

- 仓库：[`AISBench/benchmark`](https://github.com/AISBench/benchmark)
- 审查时 `master`：`800cd1507fb5443358376ec1415fba34364f3b28`
- 重点查看：
  - CLI 参数及 `--num-warmups`、`--debug`、`--pressure`；
  - 服务化模型配置中的 `request_rate`、`batch_size`、stream 和
    `ignore_eos`；
  - synthetic dataset 与公共前缀能力；
  - 性能结果的 CSV、JSON、JSONL、日志和并发可视化产物；
  - total/stable stage 的统计语义。

### aisbench_auto_tools_prefix wrapper

- 仓库：
  [`rayn-zzz/aisbench_auto_tools_prefix`](https://github.com/rayn-zzz/aisbench_auto_tools_prefix)
- 审查的是 2026-08-20 可见的 `main`；仓库 README 标记最新更新为
  2026-08-14。由于 `main` 会继续变化，落地兼容规则时必须再记录准确 commit 或
  文件 SHA256。
- 重点查看：
  - `README.md`：参数和 prefix cache 场景定义；
  - `aisbench_test.py`：参数解析、AISBench 命令、临时配置、前缀预热、metrics
    前后快照及结果保存；
  - `generate_dataset.py`：`repeat_rate`、`prefix_num`、`seed`、定长/变长数据集；
  - `cal_prefix_hit_rate.py`：HBM/external prefix counter 解析；
  - `save_file.py`：从控制台日志解析 CSV；
  - `config.py`、`default_api.py`：运行路径、endpoint、API key 与 generation
    参数。

## 已确认的 wrapper 语义

### `repeat_rate`

`repeat_rate` 是数据集构造参数：`0.5` 和 `50%` 均表示公共前缀约占每条目标输入
长度的 50%。定长数据集的大致构造为：

```text
公共前缀 + 3 个由 seed 控制的随机 token + 后缀
```

因此它表示理论可复用前缀比例，不是实际观测命中率。实际 HBM/external prefix
cache 命中率来自 vLLM counter 在 workload 前后的差值。

### `prefix_test`

启用 `prefix_test` 后，wrapper 先发送 `dp * prefix_num` 条公共前缀进行预热，
预热阶段使用 `concurrency=dp`、`output_len=1`、`request_rate=0`，然后再执行正式
全量数据集。

wrapper 会在预热阶段和正式阶段分别读取 prefix metrics。正式报告必须区分：

```text
warmup_prefix_hit_rate
formal_prefix_hit_rate
```

不能把预热阶段命中率误当成正式 workload 命中率。

### AISBench 配置

wrapper 会生成 `temp_api.py`，性能模式固定使用 `temperature=0` 和
`ignore_eos=True`，并把临时 model config 与测试数据集软链进 AISBench 工作目录。
因此 wrapper 主路径已有 AISBench 配置生成能力，但它会修改 `WORK_PATH`，不是纯
只读封装。

## 已落地的 P0 契约与后续顺序

### P0：结果正确性

以下要求已写入 `motor-benchmark` Skill 和 `references/aisbench.md`：

1. Skill 已采用上述 `repeat_rate` 语义，同时继续强调它不等于实际命中率。
2. Skill 不再用 `total_req` 代替成功请求数，要求从 AISBench 原始 JSON/JSONL 或日志
   提取 `Success Requests`、`Failed Requests`，并保留 POST/RECV/FINISH/FAIL、
   timeout、空响应和 HTTP 错误证据。
3. Skill 将 wrapper `save_file.py` 的默认哨兵值 `99999`、`9999` 视为解析失败。任一核心
   字段仍为哨兵值、缺失或与原始 AISBench 结果不一致时，run 必须失败，禁止报告
   性能结论。
4. wrapper 当前正式性能命令固定带 `--debug`。Skill 要求正式 run 移除该 flag，
   校验目标并发与实际平均/最大并发；未达到目标时标记 `client-limited`，不得归因
   给 Motor 服务端。

### P0：版本与运行目录

以下要求已写入 `motor-benchmark` Skill 和 `references/aisbench.md`：

5. Skill 要求开跑前记录 wrapper commit/SHA256、AISBench 版本、安装路径、Python 版本和
   `ais_bench --help`。建立至少包含 `--num-warmups`、输出 schema 和 model class
   的兼容矩阵。
6. Skill 明确 wrapper 会创建或替换以下 runtime 文件：
   - wrapper 目录中的 `temp_api.py`、`aisbench.log`、`aisbench_result.csv`；
   - AISBench model config 下的 `vllm_api_chat_temp.py`；
   - AISBench dataset 下的 `test.jsonl` 和可能的 `train.jsonl`。
7. `BENCH_ROOT` 和 `WORK_PATH` 必须是用户批准的专用 runtime/untracked 目录；Skill 禁止
   多个 benchmark 并发共享同一 `WORK_PATH`，也禁止指向需要保留原状的 tracked
   AISBench source。
8. wrapper 会把 `API_KEY` 替换进临时配置，与引用式 secret 要求冲突。在 wrapper
   支持环境变量或其他 secret reference 前，Skill 要求带鉴权正式压测
   fail-closed，不能把明文写入配置、命令或归档。

### P1：Prefix 指标可信度

9. 保存每个 Pod/engine 在正式 workload 前后的原始 metrics 快照，而不只保存
   wrapper 打屏汇总。
10. 检查 counter 单调性、engine 集合一致性、skipped Pod、同 endpoint 多模型 series
    和同时间窗其他流量。证据不完整时命中率为 unavailable。
11. `dp` 条预热请求只是尝试覆盖所有 DP。预热后必须检查每个预期 engine/DP 的
    query counter 是否有正增量；没有覆盖完整时继续预热或停止正式 run。

### P1：证据与重复测试

12. wrapper 的 `--repeat` 只保证最后一次聚合数据易于获取。正式比较应每轮单独调用
    并立即归档，或暂时禁止 `--repeat > 1`。
13. 每次正式 workload 建议在
    `.motor-workspace-local/benchmark-runs/<namespace>-<timestamp>/` 保存：
    - `manifest.json`：run 状态、时间、版本、模型、硬件、拓扑和 workload；
    - 完整命令和脱敏后的 `config.py`、`temp_api.py`；
    - 数据集生成参数与文件 checksum；
    - AISBench 完整时间戳输出目录；
    - wrapper CSV 对应行及原始日志；
    - success/failure 统计；
    - prefix metrics before/after 与正式命中率汇总。

## 暂不扩大的范围

当前 wrapper 的主要目标是固定请求数、并发/到达率、prefix cache 和 GSM8K 格式
数据集。AISBench 官方的 pressure、复杂 `traffic_cfg`、timestamp trace、多轮对话
和投机推理指标可以后续按真实需求接入，不应先于上述结果正确性和证据问题。

## 一句话结论

当前 Skill 的方向没有错：它确实是在操作一套有明确 prefix 扩展的 AISBench
wrapper。下一步不应简单改成裸 `ais_bench`，而应把 wrapper 当成正式、版本化的
执行后端，补齐它的失败语义、运行副作用、client capacity Gate 和结构化证据。
