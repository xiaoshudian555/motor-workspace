---
name: motor-smoke
description: Validate that a successful Motor deploy has a live Coordinator management Service whose GET /readiness body reports ready=true. Use for the minimal post-deploy Coordinator readiness gate; real inference requests belong to motor-functional.
---

# motor-smoke

Consume a successful `deploy-complete` run and produce a run-scoped readiness
result. Read `references/motor-readiness.md` before changing the readiness
criterion.

```bash
python3 .agents/skills/motor-smoke/scripts/smoke_run.py \
  --machine <alias> \
  --deploy-run-id <id>
```

## Pass criteria

Require both:

1. The Coordinator management Service has a ready endpoint.
2. Management `GET /readiness` returns HTTP 200 with JSON `ready=true`.

Pod `Ready`, TCP connect, `/startup`, `/liveness`, `/health`, and `/v1/models`
do not replace the readiness-body check.

Do not send inference requests in this skill. Non-stream/stream inference,
metrics, tracing, and feature behavior belong to `motor-functional`.

**Do not** reuse the mgmt Service ClusterIP or port 1026 for inference curl.
Mgmt is readiness only (1026); infer is port 1025 on a separate Service. See
`../motor-functional/references/coordinator-endpoints.md`.

Write artifacts under `.motor-workspace-local/validation-runs/{smoke_run_id}/`.
The remote machine runs `kubectl port-forward`; an SSH tunnel exposes its
temporary loopback listener locally. Clean up the forward on exit.
