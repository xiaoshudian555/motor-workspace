# Motor Kubernetes deploy helpers

This is a transitional location for small standalone helpers consumed by the
`motor-k8s-deploy` skill. It is not the deploy workflow source of truth.

The ownership chain is:

```text
.agents/skills/motor-k8s-deploy/scripts/
  -> .agents/lib/mws_deploy.py
  -> Motor upstream examples/deployer
```

Only helpers with a clear standalone purpose should remain here. Render,
apply, status, restart, stop, Pod code-load proof, and run-scoped evidence
belong to the deploy skill and shared deploy implementation.

Nothing in this directory may implement a competing P/D controller.
