# MindCluster diagnosis principles

## The diagnostic invariant

The visible failure is an observation point, not necessarily the owning layer.
Find the last contract that demonstrably succeeded and the earliest contract
that demonstrably failed. Errors after that boundary are usually consequences;
errors before it are competing causes that still need evidence.

Prefer the earliest **failed prerequisite or handoff** over the earliest log
line. Components run concurrently, clocks may differ, and retries can make a
later symptom appear first in collected output.

## Responsibility model

MindCluster is not a single linear pipeline. Workload intent and device state
develop on separate paths and meet at scheduling:

```text
workload intent -> API acceptance -> operator expansion -----------\
                                                               scheduler
hardware/DCMI -> device discovery -> resource/health view -> topology/
                                                                   |
                                                                   v
                                                     binding -> node/runtime
                                                                   |
                                                                   v
                                                        process -> service path
```

Use this model to ask which contract should have produced the next observable
state. Do not infer that one component calls the next merely because they are
adjacent in the diagram.

| Domain | Contract it owns | Evidence that changes the diagnosis |
|---|---|---|
| Deployment input and generated objects | Desired workload and service objects represent the intended deployment | Submitted/effective objects and generation outcome |
| API Server | Objects satisfy schema, admission, authorization, and cluster uniqueness rules | Acceptance or rejection tied to the exact object |
| Infer Operator | Accepted custom resources produce correctly owned child objects | Child existence, ownership, reconciliation status, and controller observations |
| Ascend Device Plugin | Devices are discovered, registered, reported with current health, and allocated to containers | Node resource view, device health/fault view, allocation outcome |
| ClusterD | Node device, topology, and fault information is aggregated into the view consumed by scheduling | Published cluster/topology view and its freshness relative to device state |
| Volcano | Eligible workloads are admitted and bound according to queue, resource, topology, and placement constraints | Scheduling decisions, constraints considered, and selected or rejected candidates |
| kubelet and Ascend runtime | A bound Pod can obtain its image, mounts, devices, drivers, and runtime environment and start | Node-local lifecycle state and the first failed prerequisite |
| Motor processes | Components initialize, register, heartbeat, and coordinate with the intended identities and addresses | Process timeline, effective targets, and peer-side observations |
| Service, DNS, and CNI data plane | Names resolve and required traffic reaches the intended endpoints across the relevant nodes | Endpoint membership plus path-specific resolution and connectivity evidence |

## Route from symptom to the first question

The table selects the first responsibility boundary to inspect; it does not
pre-decide the root cause.

| Observation | First question |
|---|---|
| Object submission is rejected | Which API, admission, authorization, or uniqueness contract rejected the exact object? |
| A custom resource exists but expected children do not | Did the owning operator observe and reconcile it, and where did reconciliation stop? |
| A workload remains unscheduled | Which queue, placement, resource, topology, taint, or health constraint eliminated every candidate? |
| NPU capacity appears present but cannot be allocated | Do advertised capacity, allocatable resources, assigned devices, health/fault state, and topology view describe the same current reality? |
| A workload is bound but its container does not start | Which node-local image, storage, mount, device, driver, runtime, disk, or permission prerequisite failed first? |
| A Motor component cannot register or heartbeat | Is the intended peer identity and address correct, does it have a live endpoint, is the path reachable, and did either process reject or fail the exchange? |
| Communication fails only across nodes | Where does the path first diverge across endpoint selection, name resolution, routing/encapsulation, interface choice, MTU, policy, firewall, or return routing? |
| One node or device disagrees with the rest of the cluster | Which node-local fact is stale or unhealthy, and which aggregating or consuming component has or has not observed it? |

## Evidence discipline

For each hypothesis, answer:

1. What observation supports it?
2. What evidence contradicts it or remains absent?
3. What is the smallest read-only observation that separates it from the next
   plausible cause?
4. What result is expected on each side of that distinction?
5. If confirmed, what exact contract should recovery restore and how will that
   be observed?

Use narrow, current evidence first. Preserve surrounding context rather than a
single matched word. Correlate timestamps, object identities, ownership, node,
device, container instance, and previous/current process state. Compare desired
state with effective state instead of assuming that submitted intent reached
the running workload unchanged.

## Common reasoning traps

- Cluster-wide free NPU count does not prove that one eligible node satisfies a
  workload's complete resource and placement constraints.
- A healthy-looking Device Plugin process does not prove that its resource and
  fault views are current or consumable by scheduling.
- A running networking component does not prove the required data path works.
- A registration timeout does not prove a network fault; the target identity,
  effective configuration, endpoint state, peer process, and path all compete.
- A restart that clears the symptom does not explain which contract failed.
- Changing fault classification changes software handling; it does not repair a
  device.
- Weakening eviction, garbage collection, or health policy can manufacture
  apparent availability while increasing production risk.

## Recovery boundary

Diagnosis ends with a causal explanation or a minimal evidence request.
Recovery is a separate, explicitly authorized action. Prefer the smallest
change that restores the failed contract, preserve a rollback path, and verify
both the recovered state and the absence of recurrence. Node lifecycle work and
deployment placement changes are separate workflows, not diagnostic shortcuts.
