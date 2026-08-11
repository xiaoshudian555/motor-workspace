# 真实集群验收以直接工具和实时状态为准

## 只读阶段

- endpoint 可达，固定共享目录符合 inventory；
- `kubectl` context 正确且 API 可读；
- 节点、NPU resource、MindCluster/Volcano、controller/device-plugin 健康；
- 原生配置字段和路径真实存在；
- `deploy.py --dry-run` 成功；
- 目标 namespace 已存在（configure 不得创建）；
- server-side dry-run 仅对 `output_yamls/*.yaml` 成功（不含
  `.motor_config_user_config.json` 等 deployer 辅助文件）；
- 镜像和 NodePort 风险已报告。

## 需单独授权

- parity 覆盖固定远端目录；
- 修改 `user_config.json` / `env.json`；
- build-wheel 对远端 `boot.sh` 的更新；
- deploy、restart、stop、ConfigMap 修改。

## 部署通过标准

- upstream deploy command 成功；
- 本次生成的 workload 全部 rollout 成功且 Pod Ready；
- Service endpoint 存在；
- Coordinator `/readiness` 在轮询上限内返回 HTTP 200、`ready=true`（`ready=false`
  仅为等待，Pod Ready 不能替代）；
- 代码替换场景下，Pod 内实际包路径符合 image/wheel 策略。

Fixture/UT 不能替代真实集群验收。保存原始命令、输出、events 和日志，但不创建
另一套 readiness run ID。
