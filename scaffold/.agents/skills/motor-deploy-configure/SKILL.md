---
name: motor-deploy-configure
description: Validate Motor native user_config.json and env.json with the upstream deployer dry-run. Use for deploy configure or config validation.
---

# motor-deploy-configure

Motor's native `user_config.json` and `env.json` are the only deployment
configuration. Do not generate a workspace-specific profile, plan, bundle,
fingerprint, or digest contract.

## Procedure

1. Read the latest native config and summarize namespace/job ID, deploy mode,
   image, model paths, P/D counts, node selectors, NPU requirements, ports, and
   package mode. Do not invent missing values.
2. Confirm referenced paths are under the selected shared mount and the
   upstream YAML templates already mount that root.
3. On the selected endpoint, from the fixed Motor source tree, run:

```bash
cd /mnt/motor-workspace/motor/examples/deployer
python3 deploy.py --config_dir <remote-config-dir> --dry-run
```

4. Inspect every newly generated YAML. Check namespace, image, hostPath,
   volumeMount, resources, NodePorts, workload names, and absence of source
   `PYTHONPATH`.
5. When API access exists, run server-side dry-run against the generated files:

```bash
kubectl --context "$CTX" apply --dry-run=server -f output_yamls/
```

Dry-run must not apply resources or modify the user's config. A wheel build is
handled by the remote fixed tree's existing `boot.sh`; do not inject a second
`MOTOR_WHEEL_DIR` mechanism into YAML.

Report the config directory, generated files, checks, diff from existing
resources when available, and blockers. Do not produce `deploy-config-ready`.
