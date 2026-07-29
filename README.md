# motor-workspace

Independent meta-repository for developing and validating MindIE Motor with vLLM
and vLLM Ascend on MindCluster Kubernetes. It does not replace Motor's deployer.

## Primary workflow (Agent skills)

```text
repo-init
  -> machine-management
  -> session-management
  -> remote-code-parity
  -> motor-k8s-deploy
  -> OpenAI smoke
```

Development uses **parity sync to a shared mount root** (profile `mount_root`,
default `/mnt`) and existing Pod hostPath mounts — not a new image per edit.
Image build under `tools/build/` is an optional bypass for release/delivery only.

## Repository layout

```text
motor/                         Motor submodule
vllm/                          vLLM submodule
vllm-ascend/                   vLLM Ascend submodule
.agents/skills/                Agent domain workflows (primary entry)
.remote-dev/                   Remote read/edit/bash/search substrate
profiles/                      hardware + MindCluster profiles
workspace.lock.yaml            reviewed source/runtime lock
.motor-workspace-local/        ignored run state (machines, sessions, runs)
bin/motorws                    internal skill backend (not the product CLI)
tools/build/                   optional image bypass (non-default)
tools/deploy/                  deploy adapter helpers for motor-k8s-deploy skill
```

## Quick start

```bash
git submodule update --init --recursive
```

With an Agent-capable IDE, use natural language:

> "Initialize this workspace and add my NPU machine."

Or invoke skills directly, for example:

```bash
python3 .agents/skills/repo-init/scripts/repo_init_probe.py --compact
python3 .agents/skills/machine-management/scripts/inventory.py list
python3 .agents/skills/session-management/scripts/session_create.py --machine <alias>
python3 .agents/skills/remote-code-parity/scripts/parity_sync.py --session-id <id>
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_plan.py --session-id <id> --profile profiles/a2-dev.yaml
```

Every skill script writes progress to stderr and one JSON object to stdout.

## Version locking

Three submodule gitlinks lock source code. `workspace.lock.yaml` repeats those
commits and records `mount_root`, `hardware_profile`, and deploy-time
`base_image_ref`. Runtime versions belong in each run record as diagnostic
evidence, not a manually maintained compatibility matrix.

## Safety

- Credentials, kubeconfig content, model paths and local inventory are never tracked.
- Apply, scale, delete, rollback and remote directory overwrite require consent.
- Deployment adapters invoke Motor's existing deployer/config semantics.

See [docs/architecture.md](docs/architecture.md) for boundaries and phases.
