# 2026-10-16 — Motor deploy routing needs three concrete anchors

## Trigger

The user reported recurring false invocation of `motor-deploy`, especially for
Docker-only and RBG starts, and asked to constrain it to requests equivalent to
"use Motor in a named place to deploy a named service."

## Evidence

- The former frontmatter listed generic phrases such as `拉起一个服务` and
  `部署服务`, so neither a Motor-native action nor a concrete target was required.
- A recorded Coordinator-only task requested a local bootstrap script and guide,
  but the broad dispatcher led into wheel, config, and Kubernetes deployment
  Skills even though no live resource action was requested.
- The user named Docker-only and RBG workflows as additional recurring
  false-positive classes.

## Agent decision gap

The old rule treated deployment-shaped words as sufficient evidence of Motor
deployment intent. It did not distinguish Motor's native deployer from other
runtimes, did not require a concrete target and service, and did not require a
live lifecycle action. That made unrelated workflows inherit deployment Skills
and their authorization gates.

## Lesson

Implicitly route to `motor-deploy` only when all three anchors are present:
Motor-native deployment, a concrete target plus named service/model, and a live
lifecycle action. An explicit `$motor-deploy` invocation remains an override.

## Scope and counterexamples

This owns implicit `motor-deploy` routing in motor-workspace. Generic service
starts, Docker-only or RBG work, local scripts/guides/config examples, and
code-only work stay outside the dispatcher. A concrete request such as
`在 120 集群用 Motor 部署 DeepSeek-V4 1P1D 服务` remains in scope.

## Interception

The dispatcher frontmatter and body, repository entry rule, Cursor entry, and
OpenAI metadata share the three-anchor gate and named counterexamples. A
regression test checks discovery-description parity, the gate, and representative
positive and negative cases; generated Claude shims are synchronized from the
tracked canonical Skill.

## Promotion

Promoted to the `motor-deploy` dispatcher and repository routing entry. The live
authoring source and tracked discovery entries were updated in the same task.

## Status

promoted — structural regression and shim checks passed. Fresh-session model
routing effect remains unverified pending a later matching request.
