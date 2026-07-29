# Optional image build bypass (non-default)

**Not the primary development path.** Daily validation uses
`remote-code-parity` → shared mount root → Pod hostPath + `PYTHONPATH`.

Use this directory only when:

- delivering a release image;
- shared mount storage is unavailable; or
- the user explicitly requests an image build.

When used, materialize a build context from parity output and adapt Motor's
`docker/mindie-motor-vllm` Dockerfile/Makefile. Record image digest and provenance
in the run manifest. Skill routing must label this path as bypass.
