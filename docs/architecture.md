# Architecture and boundaries

## First phase

1. P0: source/runtime lock, local run state and read-only MindCluster preflight.
2. P1: three-repository source snapshot and development image build.
3. P2: thin adapter around Motor's deployer, render/plan/confirmed apply, P/D
   readiness, OpenAI smoke and run-scoped cleanup.

## Second phase

Add benchmark, diagnosis artifacts, Ascend HBM attribution and torch profiler
collection/analysis only after P0-P2 are stable.

## MindCluster preflight

The preflight examines:

- NodeD, ClusterD, Ascend Device Plugin, Ascend Docker Runtime, Volcano and
  Ascend Operator presence/readiness;
- AscendJob and PodGroup API discovery;
- Volcano scheduler and queue visibility;
- NPU allocatable resources and node readiness;
- current-user access to the target namespace and required resources.

Names differ between MindCluster releases, so component matching is driven by a
profile and raw evidence is retained in the run directory.

## Extension contracts

`tools/build/` must consume a source manifest and produce an image digest plus
provenance. `tools/deploy/` must consume a successful image digest and invoke
Motor's existing deployer/config semantics. Neither extension may silently
modify source submodules or shared Kubernetes resources.

