---
name: motor-functional
description: Run focused Motor functional checks with direct kubectl, HTTP, metrics, or tracing tools. Use for inference and deployed feature behavior.
---

# motor-functional

Read `references/case-catalog.json` to select the smallest case set and
`references/coordinator-endpoints.md` before endpoint access. Do not compile a
second spec format or call a workspace dispatcher.

1. Resolve namespace, served model, enabled feature config, and current
   Coordinator Services from native config and live K8s state.
2. Present the selected cases and pass criteria before any material load.
3. Use `kubectl port-forward` through the remote job/monitor tool and clean it
   up on exit.
4. For inference, POST `/v1/completions` with a `prompt` to the infer Service
   on port 1025. Never send inference to management port 1026.
5. For metrics, query the live observability endpoint after one controlled
   request. For tracing, inject a sampled W3C `traceparent` and query Tempo.

Record the exact request, sanitized response, metrics/traces, and result in the
conversation or a user-requested artifact path. Missing handlers, disabled
features, or absent backends are unavailable, never passed. Do not mutate
deployment config or claim performance, accuracy, stability, or reliability.
