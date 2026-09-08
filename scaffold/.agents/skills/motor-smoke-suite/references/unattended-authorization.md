# Unattended authorization

Use this contract only for scheduled or CI suite runs. Interactive chat stays on
explicit per-run consent.

## Modes

| Mode | When | Consent |
|---|---|---|
| `interactive` | User asks in chat | Present the concrete mutation/load plan and obtain consent before live work |
| `unattended` | Outer scheduler or CI invokes a fixed entrypoint with a signed profile | Do **not** re-prompt. The signed profile is the authorization |

Never treat an ordinary “拉起一个服务 / 部署 / 扩缩容 / 注入故障” chat request as
`unattended`. Missing or invalid profile → `BLOCKED`, not silent interactive
fallback that mutates the cluster.

## Signed profile

Live approved profiles belong under the untracked path:

`.motor-workspace-local/suite-profiles/<profile_id>.yaml`

Copy from
`references/profiles/unattended-suite.profile.example.yaml`, fill machine-specific
values, then set `approved_by` and `approved_at`. Do not commit approved profiles
with endpoint secrets or tokens.

### Required fields

| Field | Meaning |
|---|---|
| `mode` | Must be `unattended` |
| `profile_id` | Stable id used in logs and reports |
| `approved_by` | Human who signed the plan |
| `approved_at` | UTC timestamp of the signature |
| `valid_until` | Optional UTC expiry; expired → `BLOCKED` |
| `endpoint` | Endpoint id or host key used by the runner |
| `kube_context` | Exact kube context |
| `namespace` | Exact namespace/job id this run may create or mutate |
| `cases` | Subset of the six suite cases, in run order |
| `allowed_actions` | Exact action tokens the suite may perform |
| `workload_intent` | Model family, served name, parallel sizes, P/D topology, HA flags — logical intent, not host paths |
| `config.user_config_path` / `config.env_config_path` | Endpoint-local native config paths for this run |
| `config.weight_mount_path` | Endpoint-local weight mount when asserted |
| `baselines` | GPQA gate and any comparable performance baseline |
| `software_revisions` | Expected Motor / vLLM / vLLM-Ascend / image / AISBench ids when asserted |

`workload_intent` should match `references/suite-profiles.md` unless the signed
profile explicitly substitutes another model or parallel plan. Absolute paths
never belong in the Skill-owned intent table.

### Allowed action tokens

| Token | Covers |
|---|---|
| `deploy` | Native `deploy.py` apply for the profile namespace/config only |
| `delete_owned` | `delete.sh` / stop only for resources created by this run's namespace |
| `update_instance_num` | `motor-scale` transitions listed under `cases` |
| `reliability:coordinator-failover` | That one reliability scenario |
| `reliability:decode-engine-restart` | That one reliability scenario |
| `reliability:prefill-link-isolation` | That one reliability scenario |
| `accuracy:gpqa` | GPQA accuracy case |
| `benchmark:gsm8k` | GSM8K performance case with profile baseline |

Any action not listed is out of scope. Broadening mid-run is forbidden.

## Suite behavior in `unattended`

1. Load and validate the profile before any mutation.
2. Resolve live endpoint/context/namespace/config/revisions. If live facts
   contradict the profile, stop with `BLOCKED`.
3. Record `profile_id`, approval metadata, and a content hash of the profile in
   the run report.
4. When routing to `motor-deploy`, `motor-k8s-deploy`, `motor-scale`, or
   `motor-reliability`, pass **suite pre-authorization**: mode `unattended`,
   `profile_id`, matched `allowed_actions` token(s), and exact target from the
   profile. Those Skills must not ask for new consent while the action stays
   inside that scope.
5. Deploy, readiness, inference probes, restoration, and cleanup remain fixtures
   or case assertions. They still need matching `allowed_actions`.
6. On failure: preserve evidence, run mandatory restoration/cleanup covered by
   the profile, then continue only when restoration is proved.

## Outer schedule entrypoint

Scheduling is outside this Skill. A cron, CI job, or Cursor Automation should
invoke one fixed instruction, for example:

```text
Run motor-smoke-suite in unattended mode with the signed profile path.
Do not ask for consent. Report PASS/FAIL/BLOCKED/RESTORATION FAILED with
per-case evidence and exit non-zero on non-PASS.
```

Prefer a real Pymotor automation entrypoint when its source is available. The
signed profile and action scope still define what that entrypoint may do.
