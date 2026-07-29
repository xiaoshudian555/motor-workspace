# motor-workspace

`motor-workspace` is an independent meta-repository for developing and validating
MindIE Motor with vLLM and vLLM Ascend. It does not replace Motor's deployer and
does not change a running Pod's source tree.

The first supported path is:

```text
workspace status / lock verify
  -> MindCluster read-only preflight
  -> three-repository source snapshot
  -> development image build
  -> Motor deployer render/plan
  -> confirmed Kubernetes apply
  -> P/D readiness and OpenAI smoke
```

The repository currently contains the P0 foundation and the first P1 source
snapshot command. Image build and Kubernetes deployment are explicit extension
points under `tools/build/` and `tools/deploy/`.

## Repository layout

```text
motor/                         Motor submodule (upstream master)
vllm/                          vLLM submodule (main; current compatibility line 0.23.0)
vllm-ascend/                   vLLM Ascend submodule (main; current compatibility line 0.23.0)
profiles/                      hardware, build and MindCluster profiles
src/motor_workspace/           dependency-light workspace CLI
tools/build/                   Motor image build extension point
tools/deploy/                  Motor deployer adapter extension point
workspace.lock.yaml            reviewed source/runtime lock
.motor-workspace-local/        ignored run state and manifests
```

## Quick start

```bash
git submodule update --init --recursive
./bin/motorws status
./bin/motorws lock verify
./bin/motorws preflight mindcluster --profile profiles/a2-dev.yaml
./bin/motorws snapshot create
```

Every command writes one JSON result to stdout and progress to stderr. Preflight
is read-only: it performs Kubernetes discovery and never creates or changes
cluster resources.

## Version locking

The three submodule gitlinks lock source code. `workspace.lock.yaml` repeats
those commits so tooling can reject accidental mismatches and also records the
base image digest and hardware profile. Runtime versions such as Python,
PyTorch, torch_npu, CANN, Kubernetes and MindCluster belong in each run record;
they are evidence, not a manually maintained global compatibility table.

`runtime.base_image` is intentionally unresolved in the initial scaffold.
Replace it with a real `repository:tag@sha256:digest` before build or deploy.

## Safety

- Credentials, kubeconfig content, model paths and local inventory are never tracked.
- Build, push, apply, scale, delete and rollback require separate explicit consent.
- Source snapshots feed an immutable image build; they are never copied into a running Pod.
- Deployment adapters must invoke Motor's existing deployer/config semantics.

See [docs/architecture.md](docs/architecture.md) for boundaries and the phased plan.

