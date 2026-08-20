# Diagnosis 直接收集当前集群证据

Diagnosis 是 deploy/validation 失败后的只读出口，不是 validation 场景，也不依赖
workspace deploy run。

最少收集：当前配置目录、endpoint/context/namespace、资源清单、Pod describe、
events、当前与 previous container logs，以及 upstream `--auto_log_collect` 产物。
每项证据记录来源命令和时间。采集期间不 restart、delete、repair 或注入故障。

通用采集完成后，按日志事实路由专项诊断；precision terminate 失败可交给
`motor-diagnosis-controller-recovery-terminate`。

部署或启动失败统一交给 `motor-startup-diagnosis`：它复用通用采集，并按证据选择
environment、deployer、config、runtime-code 一个或多个方向。分类允许主因、伴随因素
和 `unknown`；自动诊断保持只读，不自动重试、修复或修改配置。
