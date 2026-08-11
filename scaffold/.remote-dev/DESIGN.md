# Remote Developer Substrate Design

`.remote-dev` provides native-shaped remote tools to MCP-capable agents. It is
independent of Motor deployment policy: Motor Skills consume these tools, while
the substrate only resolves endpoints and performs remote operations.

## Architecture

```text
MCP client
  → mcp/server.py
  → mcp/tools.py + mcp/schemas.py
  → core endpoint/file/shell/search/patch/job/artifact operations
  → SSH or direct endpoint
```

There is one MCP surface and no per-tool CLI fallback. The server supports
standard stdio `Content-Length` JSON-RPC framing; newline-delimited JSON-RPC is
retained only for lightweight local tests.

## Endpoint model

Accepted selectors:

- direct `host + port`, with optional `user`, `root`, `cwd`, and identity file;
- a configured alias from `.remote-dev/endpoints.json` or
  `.remote-dev/endpoints.local.json`.

Managed MWS `session_id`, `session_file`, and `machine` selectors are rejected.
The default permission root is `/`; callers may pass a narrower root. The default
working directory is `/mnt/motor-workspace` when no endpoint-specific cwd exists.

## Tool surface

- file: `remote.read`, `remote.write`, `remote.edit`, `remote.multi_edit`,
  `remote.ls`;
- execution: `remote.bash`, `remote.monitor`, job status/tail/stop;
- search: `remote.glob`, `remote.grep`;
- patch: `remote.apply_patch`;
- artifacts: manifest, pull, push;
- context: snapshot and probe.

MCP resources expose endpoint state, context, jobs, bounded stdout/stderr, and
artifact manifests. Runtime state is local and untracked under
`.remote-dev/state/`.

## Safety properties

- endpoint root/cwd path policy is checked before remote operations;
- edit/write can use read-ledger optimistic concurrency checks;
- patch validates the complete operation set before writing and blocks unsafe
  targets;
- artifact transfer verifies manifests and hashes;
- background jobs preserve endpoint identity and bounded model-visible output;
- secrets are not written to tracked endpoint configuration.

## Claude Skill shims

Canonical Skills live under `.agents/skills/`. Lightweight Claude discovery
shims at both project levels are generated and checked with:

```bash
python3 .remote-dev/tools/sync_claude_skills.py
python3 .remote-dev/tools/sync_claude_skills.py --check
```

## Validation

```bash
python3 -m compileall -q .agents .remote-dev
python3 -m unittest discover -s .remote-dev/tests
python3 -m pytest -q tests
python3 .remote-dev/tools/validate_remote_dev_scaffold.py --local-only
```

Live endpoint validation requires `--host` and `--port`, or a configured
`--alias`. It creates a unique scratch directory, exercises the MCP-backed core
operations, and removes that exact directory afterward.
