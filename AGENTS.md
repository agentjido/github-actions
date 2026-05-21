# AGENTS.md

This repository publishes reusable GitHub Actions workflows from `.github/workflows/`.
Consumer repositories import them with refs like:

```yaml
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5
uses: agentjido/github-actions/.github/workflows/jido-release.yml@v5
uses: agentjido/github-actions/.github/workflows/jido-review.yml@v5
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5.1.0
```

Those refs are git refs on this repository. They version the entire repo, not individual workflow files.

## Versioning Contract

- `@v5` is a floating major tag. It is the compatibility channel for all non-breaking v5 updates.
- `@v5.1.0` is an exact release tag. It must stay fixed forever once published.
- Consumers that want automatic compatible updates should use the major tag.
- Consumers that want strict reproducibility should use an exact `vX.Y.Z` tag or a commit SHA.
- `@main` is not a stable production pin for downstream repos.

## Current Tag Model

- Major tags in this repo are movable aliases (`v1`, `v2`, `v3`, `v4`, `v5`).
- Exact release tags are SemVer tags such as `v5.0.0`.
- Current history mixes lightweight major tags with annotated exact release tags.
- Keep exact `vX.Y.Z` tags annotated going forward.
- Treat the floating major tag as intentionally movable.

## Release Rules

- Every workflow change intended for downstream consumption should get a new exact SemVer tag.
- Never retarget, recreate, or delete a published `vX.Y.Z` tag.
- Move the matching major tag only after the exact release tag exists and the change is confirmed backward compatible.
- If a change is breaking for existing `@vX` consumers, cut a new major line instead of moving the existing major tag.
- Because tags version the whole repo, a change to any reusable workflow must be evaluated as a repo-wide release decision.

## What Counts As Breaking

- Removing or renaming a public reusable workflow file.
- Adding or changing required inputs, secrets, outputs, or permissions.
- Changing defaults in a way that can make a previously passing consumer fail.
- Removing supported toolchain, service, or runtime behavior consumers depend on.

## Release Checklist

1. Validate the workflow change in at least one consumer repo using a branch ref or commit SHA.
2. Merge the change to `main`.
3. Create an annotated exact tag on the release commit:
   `git tag -a vX.Y.Z -m "vX.Y.Z"`
4. Push the exact tag:
   `git push origin vX.Y.Z`
5. If the release is backward compatible, move the floating major tag to the same commit:
   `git tag -f vX <release-commit>`
   `git push origin refs/tags/vX --force`
6. If the release is breaking, create or advance the new major tag instead and leave the old major tag in place.

## Documentation Sync

- Keep this file and `README.md` aligned on the recommended consumer pins.
- README documents the public `jido-ci.yml`, `jido-release.yml`, and `jido-review.yml` entrypoints.
- README documents both the floating major form (`@v5`) and an exact version form (`@v5.1.0`).
- When a new major is introduced, update both files in the same change.
- When README updates its exact version example, update the example here too.
