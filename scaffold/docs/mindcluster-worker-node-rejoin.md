# MindCluster worker node rejoin

Worker rejoin is a high-risk recovery action, not a diagnosis method and not a
default response to Pod, NPU, runtime, disk, or networking symptoms. Consider it
only after evidence establishes that repairing the current node state is not the
appropriate recovery path.

Before planning a rejoin, establish the exact cluster and node identity, prove
that the target is a worker with no control-plane or etcd responsibility, and
understand the workloads and local state that may be disrupted or lost. Preserve
the node metadata that must be reconstructed, including role, labels, taints,
topology information, runtime prerequisites, and device state.

Build the procedure from the target cluster's current Kubernetes, runtime, CNI,
and MindCluster versions. Do not reuse enrollment material or destructive cleanup
steps copied from an old document. Define the exact mutation scope, rollback or
replacement path, expected disruption, and acceptance window, then obtain
explicit approval for that node before changing cluster or host state.

Reappearance of the node is not sufficient recovery evidence. Confirm stable
node health, networking and runtime prerequisites, current device discovery and
NPU resource/health views, restored labels and taints, correct scheduling, and a
representative workload using the node successfully.
