---
name: remote-toolbox
description: Remote target/probe/exec/job/sync/artifact/cleanup backend. Prefer .remote-dev for ad hoc remote work.
---

# remote-toolbox

Motor-workspace uses `.remote-dev/tools/*` for direct remote operations against a
machine endpoint. Resolve machine inventory in `.agents/lib` first, then pass
`host`/`port`/`root`/`cwd` to remote tools.

See also `.remote-dev/README.md`.
