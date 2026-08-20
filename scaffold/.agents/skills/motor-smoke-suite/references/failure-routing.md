# Smoke suite failure routing

Read this reference only when planning failure handling or when a selected
stage does not pass.

## Universal first response

Preserve the first failure before changing the deployment:

1. Record UTC time, endpoint, kube context, namespace, stage, exact assertion,
   expected value, observed value, and elapsed time.
2. Use `motor-diagnosis` to collect current workloads, Pods, Services,
   endpoints, sorted events, describes, current/previous container logs, and
   native deployer `--auto_log_collect` artifacts when present.
3. Correlate evidence to the failed time window and concrete Pod/instance. Old
   errors outside the window are context, not the cause.
4. Do not restart, redeploy, delete, edit config, scale, or inject another fault
   before read-only evidence is preserved.

## Route by failed stage

| Failed observation | Immediate route | Classification goal |
|---|---|---|
| K8s workload not created or not Ready | `motor-diagnosis` | scheduling, image, mount, NPU resource, controller, crash, or config |
| Coordinator management Service missing/unreachable | `motor-diagnosis` | Service selector/endpoint, Pod state, port-forward, or network |
| `/readiness` stays `ready=false` | `motor-diagnosis` | missing P/D instances, registration, Controller/Coordinator convergence, or engine startup |
| `/readiness` HTTP/JSON invalid | `motor-diagnosis` | wrong endpoint/port, process failure, proxy response, or version mismatch |
| Inference connect/timeout/5xx failure | `motor-diagnosis` | endpoint routing, Coordinator, engine, queue, crash, or downstream timeout |
| Inference 4xx | inspect sanitized request and live model/config first, then `motor-diagnosis` if server state is implicated | request/schema/model mismatch versus server defect |
| Streaming response incomplete | `motor-diagnosis` with request/response evidence | protocol termination, proxy, Coordinator, or engine failure |
| Benchmark has failed requests, unhealthy Pods, or unreachable service | `motor-diagnosis` | service correctness before performance |
| Benchmark is valid but misses throughput/latency baseline | `motor-performance-analysis` | Motor scheduling, P/D service, client limitation, transfer, or vLLM-Ascend candidate |
| Precision auto-recovery terminate markers are present | `motor-diagnosis-controller-recovery-terminate` after generic collection | Coordinator delivery, Controller decision, recovery, or NodeManager stop |
| Supported `motor-reliability` scenario fails without a matching specialized diagnosis | `motor-diagnosis`, then report diagnosis capability gap | preserve evidence without inventing a root-cause workflow |
| Accuracy/correctness threshold fails | preserve raw evaluator output and report capability gap | dataset/config/evaluator/model attribution is not implemented |

## Diagnosis result contract

Use one primary category and a confidence level:

| Category | Meaning |
|---|---|
| environment | cluster API, scheduler, operator, node, NPU resource, image, or mount |
| deployment-config | native Motor config or generated workload mismatch |
| workload-startup | Pod scheduling, image pull, container startup, probe, or crash |
| coordinator-control | Coordinator/Controller registration, topology, or convergence |
| inference-path | Service/endpoint/protocol/request routing or engine request failure |
| benchmark-client | load generator, dataset, config, rate/concurrency, or invalid result |
| performance | valid workload misses baseline; requires performance attribution |
| specialized-recovery | a supported recovery-specific diagnosis route matched |
| unknown | evidence is insufficient or contradictory |

The report must distinguish:

- **symptom**: the failed smoke assertion;
- **proximate cause**: the closest evidenced failure;
- **root cause**: only when the evidence chain proves it;
- **repair**: never performed automatically by this suite.
