---
name: motor-config-edit
description: Translate deployment intent into a validated Motor native user_config.json + env.json. Use when the user asks to configure a model or feature, generate or edit Motor deployment configuration, or constrain Prefill/Decode placement. Output is a native config directory ready for motor-deploy-configure.
---

# motor-config-edit

3+3 **第二部分第一步**：把「用户要起什么、开哪些配置」翻译成 Motor 原生
`user_config.json` + `env.json`。产出是可直接交给 `motor-deploy-configure
--config-dir` 的配置目录。下游顺序为 `motor-deploy-preflight`（环境预检）
→ `motor-deploy-configure`（配置准备）→ `motor-k8s-deploy`（实际部署）。

## 边界

**消费**：用户自然语言意图；可选已有配置目录（迭代验证时复用）；Motor 原生
配置模板（`sources/motor/examples/infer_engines/<engine>/`）。

**产出**：一份完整的原生 `user_config.json` + `env.json`（复制自模板或已有配置，
不修改原件），以及字段 diff 和校验结果。

**不做**：不部署、不 dry-run、不注入 hostPath/PYTHONPATH、不做 server-side
校验、不调用 `motor-deploy-configure`。这些由下游步骤承担。

## 核心原则

1. **全量字段以 motor 官方文档为准，不用猜**：
   - `sources/motor/docs/zh/user_guide/configuration/config_reference.md`（全量参数
     说明：类型/默认值/取值范围）是首要参考；
   - `sources/motor/examples/features/config_sample.json`（全量配置样例）对应验证；
   - 这两个文件在本地 `sources/motor/` 里，直接读。
2. **本 skill 不写脚本替你搜代码/改文件**——这些是你的原生能力。skill 只提供
   流程约束和常用 feature 索引。
3. **不配就是默认**：`user_config.json` 只写意图要求改的字段，其余不写（或沿用
   模板），由 motor 代码默认值兜底（见 config-structure.md 加载机制）。
4. 最终产物必须是**完整的原生 `user_config.json`**（模板副本 + 字段修改），
   不能只给一段 patch。

## 工作流

### 1. 解析意图，列出要变更的字段

把用户的话拆成「起什么模型/卡数/镜像」（部署形态）和「开什么开关」（特性）。
未明确的先问，不要猜（见「字段确认策略」）。

### 2. 查映射表（快路径）

读 `references/feature-schema-map.md`。意图命中的 feature，直接用表中字段路径。

### 3. 未命中 → 读全量文档 → 再搜源码（兜底）

- 映射表没有的字段，读
  `sources/motor/docs/zh/user_guide/configuration/config_reference.md` 对应节，
  确认完整点分路径、类型、默认值、取值范围。
- 文档也没有的（新特性/新版本字段），在 `sources/motor/motor/` 搜：dataclass 字段、
  `from_json` 加载逻辑、`tests/` 用例。
- 搜到后把结果**回写映射表**（追加表格行，带出处），越用越准。
- 搜不到 = 停下问用户，不发明字段。

当用户要求 Prefill/Decode 固定节点或节点集时，读
`references/pd-placement.md`，先确认当前 Motor 版本的原生表达能力，再决定
是配置编辑、代码功能缺口，还是集群标签/约束前置条件。

### 4. 改配置

- **全新部署**：复制 `sources/motor/examples/infer_engines/<engine>/`（默认
  vllm）到 `examples/infer_engines/<engine>/generated/<job_id>/`，在副本上改。
  不要动模板原件。
- **迭代验证**：复制用户指定/已有的配置目录，在副本上改。
- 用文件编辑工具直接改 `user_config.json` 里的字段（点分路径对应 JSON 嵌套）。

### 5. 校验

按下面的清单自检，通过才算完成：

- `motor_deploy_config.image_name` / `hardware_type` / `job_id` /
  `weight_mount_path` 非空；
- prefill/decode 的 `engine_config.model`、`served_model_name` 一致；
- prefill `kv_role == "kv_producer"`，decode `kv_role == "kv_consumer"`，`kv_port`
  一致；
- `tensor_parallel_size <= 对应 Pod 的 NPU 数`；
- `env.json` 有 `motor_common_env.CANN_INSTALL_PATH` / `MOTOR_LOG_ROOT_PATH`，
  有 prefill/decode 两节 env。

### 6. 交付

向用户报告：

- 输出配置目录绝对路径；
- 字段 diff（改了什么、为什么）；
- 每个改动的源码出处（映射表命中 → 表出处；搜索命中 → 代码文件:行号）；
- 下一步：交给 `motor-deploy-configure` 读取该目录，并在目标机器上调用 Motor
  upstream `deploy.py --config_dir <目录> --dry-run`。

## 字段确认策略

- **关键字段首次提及必问**：`image_name`、`model_path`、`served_model_name`、
  `hardware_type`、`job_id` 在意图未给出时必须问，不猜默认值。
- **后续迭代验证**：用户基于已有配置迭代时（如「再开个 XX 开关验证一下」），
  沿用已有值，不重复问已确定字段。
- **其余字段**：保留模板/已有配置默认值。

## 参考

- `references/feature-schema-map.md`：常用 feature → 配置字段路径 → 取值 → 出处。
- `references/config-structure.md`：`user_config.json` 各配置节结构与加载机制。
- `references/pd-placement.md`：Prefill/Decode 固定放置的意图建模、能力确认和安全边界。
- **全量字段权威源（本地 motor 源码内）**：
  - `sources/motor/docs/zh/user_guide/configuration/config_reference.md`
  - `sources/motor/examples/features/config_sample.json`
- `sources/motor/motor/config/`：各 Config dataclass 定义（兜底权威字段源）。

## 维护

- 每次通过全量文档/源码确认新字段路径，回写 `feature-schema-map.md`。
- 修改时保持与 motor 当前 checkout 一致；条目必须带出处。
