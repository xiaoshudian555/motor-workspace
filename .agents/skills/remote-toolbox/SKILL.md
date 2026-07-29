---
name: remote-toolbox
description: Managed session compatibility backend for target/probe/exec/job/sync/artifact/cleanup. Prefer .remote-dev for ad hoc remote work.
---

# remote-toolbox

Motor-workspace reuses VAWS remote-toolbox semantics with `.motor-workspace-local/`
session state. Use `.remote-dev/tools/*` for direct remote operations against a
session shared mount root endpoint.

Managed session flows should pass `--session-id` to resolve host, root, and cwd
from `session.json`.

See also `.remote-dev/README.md`.
