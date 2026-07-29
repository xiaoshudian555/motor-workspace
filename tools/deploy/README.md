# Motor Kubernetes deploy helpers

Thin helpers for the `motor-k8s-deploy` skill. They wrap Motor's current
`examples/deployer` entry points — they do not implement a competing P/D
controller.

Responsibilities:

- render / server-side dry-run / diff / confirmed apply;
- inject per-role `PYTHONPATH` for session source roots after render;
- status, logs, rollback and run-scoped cleanup;
- readiness covering Controller, Coordinator, Prefill/Decode, KV and OpenAI smoke.

Primary implementation lives under `.agents/skills/motor-k8s-deploy/scripts/`.
