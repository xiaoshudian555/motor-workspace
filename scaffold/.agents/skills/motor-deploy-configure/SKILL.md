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
3. On the selected endpoint, from the fixed Motor source tree (inventory
   `source_dirs.motor` + `/examples/deployer`), run:

```bash
cd <source_dirs.motor>/examples/deployer
python3 deploy.py --config_dir <remote-config-dir> --dry-run
```

4. Inspect every newly generated Kubernetes manifest under `output_yamls/`.
   Check namespace, image, hostPath, volumeMount, resources, NodePorts,
   workload names, and absence of source `PYTHONPATH`. Ignore non-manifest
   artifacts in the same directory (see below).
5. Before server-side dry-run, verify the target namespace already exists.
   **Configure is read-only and must not create a namespace.** If it is missing,
   stop and report a blocker; namespace creation belongs to `motor-k8s-deploy`
   only after explicit user consent.

```bash
NS=<job_id from user_config>
CTX=<kube_context from inventory>

if ! kubectl --context "$CTX" get namespace "$NS" >/dev/null 2>&1; then
  echo "BLOCKER: namespace $NS does not exist; configure cannot create it"
  exit 1
fi
```

6. When API access exists, run server-side dry-run **only against generated
   Kubernetes YAML**. The deployer also writes helper files into
   `output_yamls/` (for example `.motor_config_user_config.json`, `.deploy.log`)
   that are **not** valid `kubectl apply` inputs and must be excluded.

```bash
mapfile -t YAML_FILES < <(
  find output_yamls -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) | sort
)
if [ "${#YAML_FILES[@]}" -eq 0 ]; then
  echo "BLOCKER: deploy.py --dry-run produced no Kubernetes YAML under output_yamls/"
  exit 1
fi
kubectl --context "$CTX" apply --dry-run=server \
  $(printf ' -f %s' "${YAML_FILES[@]}")
```

Dry-run must not apply resources, create a namespace, or modify the user's
config. A wheel build is handled by the remote fixed tree's existing `boot.sh`;
do not inject a second `MOTOR_WHEEL_DIR` mechanism into YAML.

Report the config directory, generated manifest list, checks, diff from existing
resources when available, namespace-exists result, and blockers. Do not produce
`deploy-config-ready`.
