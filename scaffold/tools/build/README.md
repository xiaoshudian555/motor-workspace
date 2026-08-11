# Optional image build bypass (non-default)

**Not the primary development path.** Motor code replace uses
`remote-code-parity` → `motor-build-wheel` → configure/apply with boot.sh
reconcile; runtime loads image packages or a boot.sh-installed Motor wheel.

Use this directory only when:

- delivering a release image;
- shared mount storage is unavailable; or
- the user explicitly requests an image build.

When used, select and record the image source inputs explicitly, then adapt
Motor's `docker/mindie-motor-vllm` Dockerfile/Makefile. Record image digest and
provenance in the build result. Skill routing must label this path as bypass.

The parity manifest may be referenced as synchronization evidence, but it is not
an immutable source snapshot and must not be treated as the build context.
