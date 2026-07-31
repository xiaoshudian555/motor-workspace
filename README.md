# motor-workspace

Independent meta-repository for developing and validating MindIE Motor with vLLM
and vLLM Ascend on MindCluster Kubernetes. It does not replace Motor's deployer.

## Primary workflow

motor-workspace 按用户工作流划分为三个主要部分：

1. 远程开发准备与代码同步：证明远端目录中是目标代码。
2. Motor Deploy：拉起服务，并证明 Pod 实际运行的是目标代码。
3. 部署后验证与测试：对运行中的服务执行 smoke、benchmark、profiling
   和诊断。

三部分的详细职责、完成标志和交接物见
[scaffold/docs/functional-boundaries.md](scaffold/docs/functional-boundaries.md)。

```text
本地 dirty tree
  -> SSH 覆盖同步到远端固定目录 (/mnt/motor-workspace/)
  -> Pod 通过 /mnt:/mnt hostPath 读取同一路径
  -> PYTHONPATH 优先加载 motor / vllm / vllm-ascend / python-overlay
  -> 首次 deploy 或后续 deploy_restart
```

```text
repo-init
  -> machine-management
  -> remote-code-parity
  -> motor-deploy-preflight       (K8s/MindCluster environment)
  -> motor-deploy-configure       (immutable config bundle + dry-run)
  -> motor-k8s-deploy
  -> deploy_restart (日常改码)
  -> OpenAI smoke
```

The middle Motor deploy skills (`motor-deploy-preflight`, `motor-deploy-configure`)
are implemented with fixture coverage; `motor-k8s-deploy` consumes immutable
config bundles. The 3+3 contract is defined in
[scaffold/docs/motor-deploy.md](scaffold/docs/motor-deploy.md); remaining gaps
are tracked in [scaffold/docs/technical-debt.md](scaffold/docs/technical-debt.md).
Agent execution order and remaining gaps:
[scaffold/docs/technical-debt.md](scaffold/docs/technical-debt.md).
Historical work packages: [scaffold/docs/implementation-plan.md](scaffold/docs/implementation-plan.md).

Development uses **parity sync to fixed remote directories** under the shared
mount root (profile `mount_root`, default `/mnt`). No snapshot, no `current`
symlink, and no Git commit is required for daily Python edits. Immutable deploy
config bundles do use an integrity digest/fingerprint, while code-only changes
may reuse the same bundle after rebinding it to the current parity result.
Image build under `scaffold/tools/build/` remains an optional bypass for
release/delivery.

## Fixed remote workspace layout

```text
/mnt/motor-workspace/motor
/mnt/motor-workspace/vllm
/mnt/motor-workspace/vllm-ascend
/mnt/motor-workspace/python-overlay
```

`PYTHONPATH`:

```text
<motor>:<vllm>:<vllm-ascend>:<python-overlay>
```

## Repository layout

```text
sources/                       Upstream source submodules
  motor/                       MindIE Motor
  vllm/                        vLLM
  vllm-ascend/                 vLLM Ascend
scaffold/                      Workflow and remote-dev substrate
  .agents/skills/              Agent-facing workflows (primary entry)
  .agents/lib/                 Shared workflow implementation
  .remote-dev/                 Generic remote operation substrate
  profiles/                    Shared machine and deploy inputs
  workspace.lock.yaml          diagnostic lock (dirty tree allowed)
  bin/motorws                  internal skill backend (not the product CLI)
  tools/build/                 optional image bypass (non-default)
  docs/                        architecture and boundary docs
.motor-workspace-local/        ignored machine state and workflow run evidence
```

The three functional boundaries do not map one-to-one to directories.
Skills are user-facing workflows; `scaffold/.agents/lib/` and
`scaffold/.remote-dev/` are shared implementation layers. Directory ownership
is defined in [scaffold/docs/directory-ownership.md](scaffold/docs/directory-ownership.md).

## Quick start

```bash
git submodule update --init --recursive
```

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_probe.py --compact
python3 scaffold/.agents/skills/machine-management/scripts/inventory.py list
```

Every skill script writes progress to stderr and one JSON object to stdout.

## Version locking

Three submodule gitlinks under `sources/` record source commits.
`scaffold/workspace.lock.yaml` is diagnostic only: dirty working trees are
allowed, and HEAD/lock mismatch is a warning—not a deploy blocker. Base image
comes from deploy `user_config.json` first.

## Safety

- Credentials, kubeconfig content, model paths and local inventory are never tracked.
- Apply, stop, restart and remote directory overwrite require explicit consent flags.
- Deployment adapters invoke Motor's existing deployer/config semantics.

See [scaffold/docs/functional-boundaries.md](scaffold/docs/functional-boundaries.md)
for the three user-workflow boundaries and
[scaffold/docs/architecture.md](scaffold/docs/architecture.md) for implementation
layers and runtime constraints.
