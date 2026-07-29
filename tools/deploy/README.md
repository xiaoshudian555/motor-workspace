# Motor Kubernetes deploy adapter

This directory will provide a thin wrapper around Motor's current deployer and
MindCluster best practice. It must not implement a competing P/D controller.

The adapter will support render, server-side dry-run, diff, confirmed apply,
status, logs, rollback and run-scoped cleanup. Readiness must cover Controller,
Coordinator, Prefill/Decode registration, KV connectivity and an OpenAI request.

