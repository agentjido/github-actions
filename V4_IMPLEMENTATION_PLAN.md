# V4 Implementation Plan

## Summary

Implement the three-lane V4 workflow platform on
`agentjido/github-actions@feat/v4-workflow-platform`, then validate it on
`agentjido/jido_signal@chore/test-v4-ci`.

## Implementation Steps

1. Make `jido-ci.yml` read-only.
   - Remove CI write-back and dependency submission inputs, secrets, jobs, and docs.
   - Keep compile, quality, tests, docs, Hex dry-run, policy, and summary.

2. Replace the old release workflow with `jido-release.yml`.
   - Support `operation: auto | prepare | publish`.
   - Use manual dispatch for git_ops release prep.
   - Use `push.tags: ["v*"]` callers for publish.
   - Require `RELEASE_TOKEN` for non-dry-run prepare.
   - Validate tag version against `mix.exs` before publish.
   - Create GitHub releases only after Hex confirms the package version exists.

3. Add `jido-review.yml`.
   - Generate deterministic PR review artifacts and summary.
   - Optionally post/update one sticky same-repo PR comment.
   - Keep `agent_review` disabled by default.

4. Update docs.
   - Public workflows are exactly `jido-ci.yml`, `jido-release.yml`, and `jido-review.yml`.
   - Remove v4 guidance for CI write-back and dependency submission.
   - Update release examples to include tag and dispatch triggers.

5. Update `jido_signal`.
   - Keep CI on branch ref.
   - Point release to `jido-release.yml@feat/v4-workflow-platform`.
   - Add review workflow using `jido-review.yml@feat/v4-workflow-platform`.

## Validation

- Parse every workflow YAML file.
- Run `git diff --check`.
- Run `actionlint`.
- Confirm no stale release workflow references remain.
- Confirm no CI write-back or dependency submission API remains.
- Rerun `jido_signal` PR #149 and require:
  - green CI
  - no skipped dependency submission job
  - no skipped write-back job
  - green Jido Review job
  - sticky review comment or clear permission skip
- Run `jido_signal` release dispatch dry-run with `operation: auto`.
- Run `jido_signal` release publish simulation with `operation: publish`, `hex_dry_run: true`, and `tag_name: v2.1.1`.

## Assumptions

- V4 is unreleased, so renaming the public release entrypoint is acceptable.
- CI must stay read-only.
- Release is intentionally privileged.
- Review is advisory until signal/noise is proven.
- Ecosystem reverse-dependency smoke testing is out of scope for this branch.
