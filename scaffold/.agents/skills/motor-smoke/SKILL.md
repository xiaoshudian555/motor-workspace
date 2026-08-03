---
name: motor-smoke
description: Validate a successful Motor deploy with Motor-aware readiness and real non-stream/stream inference. Use after motor-k8s-deploy when users ask whether a service really started, request smoke validation, or need a minimal post-deploy inference check.
---

# motor-smoke

Consume a successful `deploy-complete` run and produce a run-scoped
`motor-smoke` validation result. Read `references/motor-readiness.md` before
changing pass criteria or substituting another probe endpoint.

```bash
python3 .agents/skills/motor-smoke/scripts/smoke_run.py \
  --machine <alias> \
  --deploy-run-id <id>
```

If Motor API-key authentication is enabled, put the plaintext key in
`MOTOR_SMOKE_API_KEY`; the key is never written to artifacts or stdout.

For TLS, provide a locally readable CA and, when required, a client certificate:

```bash
python3 .agents/skills/motor-smoke/scripts/smoke_run.py \
  --machine <alias> --deploy-run-id <id> \
  --ca-file <ca.pem> \
  --client-cert-file <client.pem> --client-key-file <client-key.pem>
```

## Pass criteria

Require all of the following:

1. Verify that the exact Coordinator inference and management Services have ready endpoints.
2. Require management `GET /readiness` to return HTTP 200 with JSON `ready=true`.
3. Require a non-streaming `POST /v1/completions` to return generated output.
4. Require a streaming request to return valid JSON SSE choice events, generated output,
   and `data: [DONE]`.

Pod `Ready`, TCP connect, `/startup`, `/liveness`, `/health`, and `/v1/models`
alone do not pass this skill. See `references/motor-readiness.md`.

Write artifacts under
`.motor-workspace-local/validation-runs/{smoke_run_id}/`. The workflow does not
restart, scale, reconfigure, or otherwise mutate the deployment. Clean up its
temporary port-forward processes on exit. `kubectl port-forward` runs on the
selected remote machine; an SSH local-forward tunnel exposes only its temporary
loopback listener to the smoke client on the development host.
