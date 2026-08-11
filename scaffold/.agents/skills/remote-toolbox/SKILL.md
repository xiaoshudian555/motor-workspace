---
name: remote-toolbox
description: Remote MCP read/edit/bash/search/job/artifact backend. Prefer .remote-dev for ad hoc remote work.
---

# remote-toolbox

Motor-workspace uses `.remote-dev` MCP tools for direct remote operations against
a machine endpoint. Resolve machine inventory in `.agents/lib` first, then pass
`host`/`port`/`root`/`cwd` to `remote.*`; there are no per-tool CLI wrappers.

See also `.remote-dev/README.md`.
