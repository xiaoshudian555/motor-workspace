# Agent guidance

Keep this repository a thin development and validation layer around Motor.

- First-phase scope is Motor + vLLM + vLLM Ascend on MindCluster Kubernetes.
- Reuse Motor's current deployer and MindCluster resources. Do not implement a
  competing P/D controller or generic serving engine.
- Preflight and plan are read-only by default.
- Never mutate a running Pod to achieve source parity. Build a new immutable image.
- Never track credentials, kubeconfig, registry secrets, actual model paths or
  generated run artifacts.
- Keep submodule gitlinks and `workspace.lock.yaml` source commits aligned.
- Commands print progress to stderr and exactly one machine-readable JSON result
  to stdout.
- Profiling and benchmark integration are second-phase work.

