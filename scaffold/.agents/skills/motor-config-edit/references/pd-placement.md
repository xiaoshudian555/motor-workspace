# Prefill/Decode placement intent

Fixed placement is a deployment-configuration concern, not a diagnosis step.
Translate the request into desired role-to-node eligibility without assuming a
particular Kubernetes field or modifying generator code by default.

## Clarify the intent

Establish:

- whether Prefill, Decode, or both roles are constrained;
- whether the target is an exact node, a replaceable node set, or a topology
  property;
- why the constraint exists and whether it must survive node replacement;
- required NPU quantity and topology per replica;
- expected behavior when no eligible node can satisfy the request.

Prefer a replaceable node set or stable topology property over a hostname when
that matches the user's operational intent.

## Confirm current support

Inspect the current Motor configuration reference, native examples, config
loader, generated workload, and relevant tests. Determine whether the checked
out version has a native placement representation and how it reaches the
effective workload.

Do not invent a configuration key. If native configuration cannot express the
request, report a code capability gap and separate it from ordinary config
editing. Patching templates or deployer generation requires explicit scope and
review as a code change.

## Validate the complete constraint

Before declaring the placement feasible, account for the intersection of:

- role replica count and per-replica NPU demand;
- eligible nodes and their current NPU health/topology;
- existing labels, taints, tolerations, affinity, and scheduler policy;
- other workloads already consuming eligible resources;
- failure and replacement behavior for the selected nodes.

Cluster-wide free capacity is insufficient evidence; at least one eligible
placement must satisfy the entire constraint set.

## Delivery boundary

For a supported native field, include it in the config diff and let downstream
dry-run verify the generated workload. Treat node labels or other live cluster
state as external prerequisites. Creating or changing them is a separate,
explicitly approved cluster mutation and is not authorized by config editing.
