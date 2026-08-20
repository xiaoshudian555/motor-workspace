---
name: motor-diagnosis-deployer
description: Diagnose failures in Motor's native deploy.py execution, template processing, manifest generation, and Kubernetes apply orchestration. Use when startup fails before or while resources are generated or applied.
---

# Motor deployer diagnosis

Use the original native deploy command, exit status, complete stdout/stderr,
config paths, generated files, and failure time window. Preserve them before a
retry. Inspect the fixed remote Motor deployer source and the matching local
source when useful; do not build a replacement wrapper.

Determine the last completed deployer stage and the first decisive error. Check
only relevant boundaries: argument/config loading, template selection, YAML
generation, NodePort resolution, environment preparation, and `kubectl apply`.
Compare generated Kubernetes manifests with the native config when translation
is in question, and use server-side dry-run evidence when it already exists.

Attribute to deployer only when valid inputs and available external
prerequisites reach an incorrect deployer decision, unhandled exception,
malformed/missing manifest, lost config value, wrong operation order, or
incorrect apply behavior. Invalid user input belongs to
`motor-diagnosis-config`; Kubernetes API, RBAC, webhook, operator, or cluster
availability failures belong to `motor-diagnosis-environment` unless the
deployer invoked them incorrectly.

Report the failing stage, first decisive error, input/output relationship,
relevant deployer source location when proven, confidence, and alternative
hypotheses. Do not edit templates, rerun deploy, apply manifests, or change the
config during diagnosis.
