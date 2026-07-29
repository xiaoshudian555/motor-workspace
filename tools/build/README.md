# Motor image build adapter

This directory will materialize a build context from a successful source
snapshot and adapt Motor's current `docker/mindie-motor-vllm` Dockerfile/Makefile.

Required output:

- immutable image digest;
- Motor, vLLM and vLLM Ascend source commits and snapshot hashes;
- base image digest and patch application result;
- import/NPU smoke results and build log references.

No shared image tag may be overwritten implicitly.

