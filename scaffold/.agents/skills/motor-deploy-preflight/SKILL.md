---
name: motor-deploy-preflight
description: Read-only K8s and MindCluster checks before Motor deploy. Use for environment preflight, 部署前检查, or 检查部署环境.
---

# motor-deploy-preflight

Run current-state checks with `remote.bash` on the selected endpoint. Use the
inventory `kube_context`; never fall back to an unrelated local kubeconfig.

Minimum checks:

```bash
kubectl --context "$CTX" version
kubectl --context "$CTX" auth can-i get pods -A
kubectl --context "$CTX" get nodes
kubectl --context "$CTX" api-resources
kubectl --context "$CTX" get pods -A
kubectl --context "$CTX" get services -A
```

Inspect the real output for:

- Kubernetes API reachability and read permission;
- schedulable nodes and advertised Ascend/NPU resources;
- required MindCluster/Volcano CRDs, API resources, controllers, scheduler,
  and device-plugin Pods for the selected `deploy_mode`;
- all required controller Pods are `Running` and `Ready`;
- configured image syntax and currently observed node image coverage;
- configured NodePorts are within range, unique, and unused.

Read `user_config.json` only when config-specific feasibility is requested.
Preflight is read-only: on a NodePort conflict, report the occupied port and a
free candidate, then wait for permission to edit the config. Do not create
resources, launch probe workloads, change namespace/RBAC, or claim that a
service will start merely because the base environment looks healthy.

Return a short table of check, command/evidence, and pass/fail. Do not write a
run record.
