# Motor Kubernetes deploy helpers

This is a transitional location for small standalone helpers consumed by the
Motor configuration and deployment steps. It is not the workflow source of
truth.

The ownership chain is:

```text
.agents/skills/motor-deploy-configure/scripts/  (target)
  -> .agents/lib/mws_deploy.py
  -> Motor upstream examples/deployer

.agents/skills/motor-k8s-deploy/scripts/
  -> .agents/lib/mws_deploy.py
  -> Motor upstream examples/deployer
```

Only helpers with a clear standalone purpose should remain here. Render,
staging, substitutions, diff and dry-run belong to `motor-deploy-configure`;
apply, status, restart, stop, Ready, Pod code-load proof, and run-scoped
runtime evidence belong to `motor-k8s-deploy`.

Nothing in this directory may implement a competing P/D controller.
