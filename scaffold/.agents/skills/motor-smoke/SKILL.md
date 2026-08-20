---
name: motor-smoke
description: Validate the deployed Coordinator management readiness endpoint. Use for the minimal post-deploy readiness check.
---

# motor-smoke

Read `references/motor-readiness.md`, resolve the current namespace from the
native Motor config or cluster, and use `remote.bash`/`kubectl` directly.

## Preconditions

- Workloads may still be starting. **Pod `Ready` is informational only** and
  does not pass smoke; only Coordinator `GET /readiness` with JSON `ready=true`
  passes.
- HTTP 200 with `"ready": false` means **startup convergence in progress**, not
  an immediate FAIL. Poll until ready or timeout.

## Procedure

1. Discover the Coordinator management Service and its ready endpoint.
2. Reach it from the remote host, or start a temporary `kubectl port-forward`
   with the remote job/monitor tool.
3. Poll `GET /readiness` on management port 1026 with bounded wait:

| Parameter | Default |
|---|---|
| Poll interval | 15s |
| Maximum wait | 600s (10 min) |
| Pass condition | HTTP 200 and JSON body `ready=true` |

4. On each poll, record evidence: UTC timestamp, HTTP status, response body
   (or truncated body), and elapsed seconds since first attempt. While
   `ready=false`, report **WAITING** (not FAIL).
5. FAIL only when:
   - maximum wait elapsed with last body still `ready=false`;
   - HTTP errors persist and are not recoverable (for example management
     Service missing, port-forward cannot start);
   - response is not parseable JSON when HTTP 200.
6. Always stop the temporary port-forward.

On FAIL, automatically invoke `motor-startup-diagnosis` with the readiness poll
timeline, final response, endpoint discovery evidence, and cleanup result. Do
this before any retry, restart, config edit, or redeploy. A port-forward setup
failure may still be a client access problem; provide its evidence and let the
diagnosis route classify it rather than assuming a Motor startup defect.

Pass requires the final poll to satisfy HTTP 200 and `ready=true`. `/health`,
`/startup`, `/liveness`, TCP connect, or `/v1/models` do not replace this
check. Do not use management port 1026 for inference; inference uses the
separate Coordinator Service on port 1025.

Report the discovered Service, endpoint method, poll count, final HTTP status,
final response body, total wait time, and cleanup result directly. Do not
create a validation run record.
