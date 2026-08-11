---
name: motor-smoke
description: Validate the deployed Coordinator management readiness endpoint. Use for the minimal post-deploy readiness check.
---

# motor-smoke

Read `references/motor-readiness.md`, resolve the current namespace from the
native Motor config or cluster, and use `remote.bash`/`kubectl` directly.

1. Discover the Coordinator management Service and its ready endpoint.
2. Reach it from the remote host, or start a temporary `kubectl port-forward`
   with the remote job/monitor tool.
3. Request `GET /readiness` on management port 1026.
4. Always stop the temporary port-forward.

Pass requires HTTP 200 and a JSON body with `ready=true`. Pod `Ready`, TCP
connect, `/health`, `/startup`, `/liveness`, or `/v1/models` do not replace
this check. Do not use management port 1026 for inference; inference uses the
separate Coordinator Service on port 1025.

Report the discovered Service, endpoint method, HTTP status, response body,
and cleanup result directly. Do not create a validation run record.
