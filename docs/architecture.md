# Architecture and boundaries

## Primary path

1. **P0**: Agent skills skeleton, `.remote-dev`, machine inventory, lock/profile,
   result contract.
2. **P1**: session-management + remote-code-parity to shared mount root;
   PYTHONPATH injection contract.
3. **P2**: motor-k8s-deploy thin wrapper around Motor deployer; confirmed apply;
   P/D readiness; OpenAI smoke; run-scoped cleanup.

## Second phase

Benchmark, diagnosis artifacts, Ascend HBM attribution and torch profiler only
after P0–P2 are stable.

## Shared mount root

- Profile field `mount_root`, default `/mnt`.
- Session directory example:
  `/mnt/motor-workspace/<workspace-id>/<session-id>/`
- Motor deployer templates already mount hostPath `/mnt:/mnt` on Controller,
  Coordinator, Engine and related roles.
- Pure Python changes: parity + inject `PYTHONPATH` + restart affected Pods.
- Editable install / ABI-sensitive changes: bootstrap Pod/Job or image bypass.

## Parity vs image bypass

| Path | When |
|------|------|
| remote-code-parity → hostPath → PYTHONPATH | Default daily development |
| tools/build/ image bypass | Release, no shared storage, explicit user request |

## Extension contracts

`tools/deploy/` helpers consume a successful parity manifest + `base_image_ref`
and invoke Motor's existing deployer. They inject per-role `PYTHONPATH` after
render. They must not rewrite AscendJob/HCCL/ranktable business logic.

`tools/build/` is optional and non-default.

## machine-management vs preflight

Machine inventory records SSH endpoints, kube context references, `mount_root`,
and candidate nodes. MindCluster/Volcano/CRD checks run as verify steps on a
registered machine — not as a replacement for inventory.
