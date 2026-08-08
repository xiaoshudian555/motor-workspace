---
name: motor-diagnosis-controller-recovery-terminate
description: Diagnose PyMotor precision auto-recovery terminate failures from a motor-diagnosis run.
---

# PyMotor controller recovery terminate diagnosis

Diagnose only the PyMotor precision-alarm path that actively terminates a P/D
instance. This skill does not cover cmotor startup, cmotor shrink-P/reserve-D,
or generic mindie-llm link failures.

## Input contract

Consume a completed `motor-diagnosis` run under:

```text
.motor-workspace-local/validation-runs/<diagnosis_run_id>/
├── context.json
├── manifest.json
└── logs/<auto_log_collect_session>/*.log
```

`manifest.json.diagnosis_routes` should contain
`motor-diagnosis-controller-recovery-terminate`. If it does not, this skill may
still run when the user explicitly identifies a precision auto-recovery issue,
but it must report that automatic routing did not match.

Treat all logs as read-only evidence. Preserve file name and line number for
every quoted event. Correlate Coordinator and Controller records by timestamp,
`instance_id`, and `p_instance_id`; do not combine unrelated alarms.

## PyMotor code path

| Stage | Component | Source |
|---|---|---|
| Precision alarm | Coordinator | `motor/coordinator/fault_tolerance/alarm/precision_alarm.py` |
| Alarm HTTP request | Coordinator → Controller | `motor/coordinator/api_client/controller_api_client.py` |
| Alarm intake | Controller API | `motor/controller/api_server/controller_api.py` |
| Instance termination | Recovery | `motor/controller/core/recovery_service.py` |
| Stop command | NodeManager client | `motor/controller/api_client/node_manager_api_client.py` |

The expected chain is:

```text
PrecisionReporter threshold
  → PrecisionAlarm probe/report
  → POST /observability/add_alarm (0xFC001107)
  → _maybe_precision_auto_recover
  → terminate_instance_for_recovery(D, "precision_alarm")
  → terminate_instance_for_recovery(P, "precision_alarm") when p_instance_id is present
  → separate_instance
  → NodeManagerApiClient.stop × N
```

## Entry patterns

Search all files under the diagnosis run's `logs/` directory:

```bash
rg -n "PrecisionReporter: threshold reached|Reporting alarm to controller|Report alarms success|Exception occurred while reporting alarms" <logs_dir>
rg -n "add_alarm:|precision-auto-recover|ControllerAPI: precision_auto_recovery_enabled" <logs_dir>
rg -n "Recovery:|terminate_instance_for_recovery|Error sending stop command to node manager" <logs_dir>
```

Continue only when the same time window contains `precision-auto-recover`,
`precision_alarm`, or alarm id `0xFC001107`.

## Decision tree

```text
Precision threshold reached
├─ no Reporting alarm / reporting exception
│  └─ U: Coordinator did not deliver the alarm
├─ Report alarms success, but no Controller add_alarm
│  └─ U: request routing or evidence gap
└─ Controller add_alarm
   ├─ skip alarm_id
   │  └─ C: alarm is not 0xFC001107
   ├─ disabled by config
   │  └─ C: precision_auto_recovery_enabled is false
   ├─ invalid/empty instance id
   │  └─ C: alarm payload cannot select the target instance
   └─ terminating D/P
      ├─ instance not found
      │  └─ R: InstanceManager has no matching instance
      ├─ missing after separate_instance
      │  └─ R: separation/state synchronization failed
      ├─ NodeManager stop error or ok=False
      │  └─ R: NodeManager stop path failed
      └─ terminate ok but instance remains
         └─ X: wrong id, incomplete P/D termination, restart, or false-positive stop
```

## Staged checks

### U — Coordinator delivery

| Check | Expected | Failure meaning |
|---|---|---|
| `PrecisionReporter: threshold reached` | Precision chain started | Missing means this is upstream of this skill |
| `PrecisionAlarm: reporting alarm_id` | Alarm prepared | Missing means probe/report did not complete |
| `Reporting alarm to controller.*0xFC001107` | Request sent | Missing means report was skipped or not called |
| `Report alarms success` | Controller HTTP response succeeded | Exception points to address/network/TLS |
| `standby coordinator does not need to report` | Absent for the active Coordinator | Present suggests role selection must be checked |

### C — Controller decision

| Check | Expected | Failure meaning |
|---|---|---|
| `add_alarm:.*precision_auto_recovery_enabled=` | Alarm received | Missing means request/evidence gap |
| `precision-auto-recover: begin instance_id=` | Recovery entered | Missing: inspect skip/disabled records |
| `skip alarm_id` | Absent | Alarm id is not the precision alarm id |
| `disabled by config` | Absent | Feature is disabled, not an execution failure |
| `invalid instance_id` / `skip D (empty` | Absent | Payload contains no usable D id |
| `skip P (empty p_instance_id` | Scenario-dependent | Allowed unless the scenario requires P termination |

Also verify the startup record:

```text
ControllerAPI: precision_auto_recovery_enabled=True
```

### R — Recovery and NodeManager

| Check | Expected | Failure meaning |
|---|---|---|
| `terminating D instance_id=` | D termination starts | Missing means stage C blocked it |
| `Recovery: separate_instance ... reason=precision_alarm` | Logical isolation starts | Missing means Recovery was not invoked |
| `Recovery: instance ... not found` | Absent | Instance id is stale, wrong, or unsynchronized |
| `missing after separate_instance` | Absent | Instance state changed unexpectedly |
| `Recovery: stop ... node_mgr_count=` | Count greater than zero | Zero means no NodeManager target exists |
| `NodeManagerApiClient.stop ... ok=True` | All stop requests succeed | `ok=False` requires NodeManager investigation |
| `Error sending stop command to node manager` | Absent | Address/network/TLS/process failure |
| `terminate_instance_for_recovery ... succeeded` | Overall success | partial/failed must be explained by preceding evidence |

An HTTP request without an exception does not by itself prove the engine process
stopped. When Controller reports success but the instance remains, compare the
alarm ids with the actual P/D instance ids, then inspect NodeManager/engine logs
for process exit and K8s logs for recreation.

## Output contract

```markdown
## 结论
{U/C/R/X 阶段 + 一句话根因；证据不足时明确写“未定位到根因”}

## 证据链
| # | 时间 | 文件:行号 | 实例 | 日志摘要 | 推导 |
|---|---|---|---|---|---|

## 根因分类
- [ ] U 告警未到 Controller
- [ ] C 配置/告警 ID/实例 ID 阻止 terminate
- [ ] R InstanceManager/separate 异常
- [ ] R NodeManager stop 失败
- [ ] X 日志成功但实例未停或被重建

## 下一步
| 优先级 | 操作 | 需要的证据 | 可信度 |
|---|---|---|---|
```

Do not recommend code changes unless the evidence identifies a code defect and
the user explicitly asks for a fix.
