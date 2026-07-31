# Repo-init acceptance criteria (motor-workspace)

## Success criteria

### Universal

- probes first before mutating
- asks before each mutation category
- never writes personal remotes or secrets into tracked files
- preserves extra remotes

### Tooling and auth

- reports when `gh` is missing
- reports when GitHub auth is missing
- provides read-only install guidance without mutating the system during probe

### Submodules and topology

- reports uninitialized submodules in probe output
- initializes submodules recursively only when `--submodules` is passed
- completes submodule init before configuring submodule remotes
- `repo_topology.py configure --repo <submodule>` errors when submodule is not initialized
- preserves nonstandard remotes during configure
- progress on stderr, final JSON on stdout

## Fixture coverage

Tests must cover:

- gh missing
- gh unauthenticated
- submodule uninitialized
- remote conflict (origin vs desired URL, idempotent re-apply)
- extra remote preserved after configure
