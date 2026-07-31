# Repo-init command recipes

## Probe

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_probe.py --compact
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_probe.py
```

## Apply submodules

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_apply.py --submodules

# Optional native-build dependency initialization:
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_apply.py \
  --submodules --recursive-submodules
```

## Apply remote topology (after user consent)

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_apply.py \
  --configure-remotes --repo vllm \
  --origin-url git@github.com:USER/vllm.git \
  --upstream-url git@github.com:vllm-project/vllm.git
```

## Topology helper (direct)

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_topology.py compare-main --repo sources/vllm
python3 scaffold/.agents/skills/repo-init/scripts/repo_topology.py configure \
  --repo sources/vllm \
  --origin-url git@github.com:USER/vllm.git \
  --upstream-url git@github.com:vllm-project/vllm.git
python3 scaffold/.agents/skills/repo-init/scripts/repo_topology.py ensure-main \
  --repo sources/vllm --remote upstream --branch main
```
