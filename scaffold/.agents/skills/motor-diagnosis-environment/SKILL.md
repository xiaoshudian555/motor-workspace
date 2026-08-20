---
name: motor-diagnosis-environment
description: Diagnose external environment causes of Motor startup failure, including Kubernetes access, operators, scheduling, nodes, NPU resources, image access, mounts, storage, and network. Use after common failure evidence points outside Motor configuration or runtime code.
---

# Motor environment diagnosis

Consume the failure boundary and evidence collected by `motor-diagnosis`. Inspect
the live endpoint and cluster with read-only tools. When CRD, component, NPU,
scheduler, or NodePort expectations matter, read the current
`motor-deploy-preflight/references/environment-contract.yaml` and apply only the
contract for the deployment mode in the native config.

Investigate the smallest relevant surface. Candidates include API reachability
and RBAC, required controllers/operators, scheduler decisions, node readiness
and taints, allocatable NPU resources, image registry access, host paths and
volumes, storage binding, DNS, and component-to-component network reachability.
Use Events and Pod status reasons before broad host inspection.

Classify environment as the root cause only when an external prerequisite is
absent, unhealthy, inaccessible, or inconsistent with a valid generated
workload. A value originating in `user_config.json` is not automatically an
environment issue: route an invalid resource request, image value, path, port,
or selector to `motor-diagnosis-config`. Route a valid config that the deployer
translated incorrectly to `motor-diagnosis-deployer`.

Report the failed prerequisite, affected objects, strongest timestamped
evidence, confidence, and the smallest confirming check. Do not create or fix
RBAC, namespaces, operators, storage, secrets, nodes, or network configuration.
