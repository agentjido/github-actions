# V4 Three-Lane Plan

## Summary

V4 has three public workflows:

- `jido-ci.yml`: read-only strict CI.
- `jido-release.yml`: privileged release preparation and tag-driven publishing.
- `jido-review.yml`: advisory pull request review automation.

Everything else in `.github/workflows/` is internal or legacy.

## CI Lane

`jido-ci.yml` is deterministic and read-only. It owns:

- compile gate with warnings as errors
- split quality jobs
- test matrix
- docs build
- Hex package dry-run
- changelog guard
- REUSE compliance
- optional Sobelow and conventional commit validation

It does not write to repositories, submit dependency data, publish releases, or
post comments.

## Release Lane

`jido-release.yml` is privileged and should be called by a consumer workflow with
both tag and manual triggers:

```yaml
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
```

Release operations:

- `prepare`: manual branch run that executes `mix git_ops.release`, then pushes the release commit and tag.
- `publish`: tag run that validates the tag against `mix.exs`, publishes to Hex, confirms Hex visibility, then creates the GitHub release.
- `auto`: publish on `refs/tags/v*`, otherwise prepare.

Non-dry-run `prepare` requires `RELEASE_TOKEN`; `GITHUB_TOKEN`-pushed tags do not
reliably trigger follow-on tag workflows.

## Review Lane

`jido-review.yml` is advisory and separate from CI. It owns:

- changed-file summary
- workflow trigger/permission risk signals
- package/release-impact signals
- missing test/docs/changelog signals
- CI status snapshot
- `jido-review.md` and `jido-review.json` artifacts
- optional sticky PR comment

V1 does not use `pull_request_target` and does not run a Jido agent by default.
The `agent_review` input is an extension point for a later purpose-built review
engine.

## Branch Pilot

Validate only on `agentjido/jido_signal@chore/test-v4-ci` before broader rollout.

Required pilot state:

- CI uses `jido-ci.yml@feat/v4-workflow-platform`.
- Release uses `jido-release.yml@feat/v4-workflow-platform`.
- Review uses `jido-review.yml@feat/v4-workflow-platform`.
- PR #149 has green CI and green advisory review.
- Release dispatch dry-run reaches git_ops dry-run without pushing or publishing.
- Publish simulation with `operation: publish`, `hex_dry_run: true`, and `tag_name: v2.1.1` validates structurally without creating a GitHub release.

Do not tag `github-actions`, do not move `v4`, and do not roll out to other Jido
packages until the pilot is stable.
