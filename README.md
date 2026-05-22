# Jido Ecosystem GitHub Actions

Reusable GitHub Actions workflows for Elixir CI/CD across the Jido ecosystem.

## Public Workflows

| Workflow | Purpose | Public API |
| --- | --- | --- |
| `jido-ci.yml` | Read-only Jido CI: compile gate, split quality jobs, test matrix, docs, package checks | Yes |
| `jido-release.yml` | Dispatch-driven Hex publish and git_ops release preparation | Yes |
| `jido-review.yml` | Advisory pull request review packet, artifacts, summary, and optional sticky comment | Yes |
| `elixir-quality.yml` | Internal quality building block used by `jido-ci.yml` | No |
| `elixir-test.yml` | Internal test building block used by `jido-ci.yml` | No |

Consumer repositories should call only the three public `jido-*` workflows.
Internal `elixir-*` workflows are implementation details and can change without
downstream compatibility guarantees.

## Consumer Integration

Use [INTEGRATION_GUIDE.md](./INTEGRATION_GUIDE.md) for the exact package
filenames, complete workflow contents, validation commands, release dry-run
flow, and rollout checklist.

Standard Jido packages should add exactly these caller workflows:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/review.yml`

The callers import:

```yaml
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5
uses: agentjido/github-actions/.github/workflows/jido-release.yml@v5
uses: agentjido/github-actions/.github/workflows/jido-review.yml@v5
```

## Version Pinning

- `@v5`: Recommended for compatible automatic updates.
- `@v5.1.0`: Exact v5.1.0 release, fixed forever.
- Commit SHA: Maximum reproducibility.
- `@main`: Development branch, not a stable production pin.

These refs are git refs on this repository. They version the entire workflow
repo, not an individual workflow file.

## Permissions

Reusable workflows cannot elevate the caller token. The caller workflow must
grant the maximum permissions the reusable workflow needs.

| Workflow | Required caller permissions |
| --- | --- |
| `jido-ci.yml` | `actions: read`, `contents: read` |
| `jido-release.yml` | `actions: write`, `contents: write` |
| `jido-review.yml` | `actions: read`, `contents: read`, `issues: write`, `pull-requests: write` when comment posting is enabled |

`jido-release.yml` also needs this secret for real Hex publishes:

- `HEX_API_KEY` for non-dry-run `publish`.

Release preparation uses the ephemeral `GITHUB_TOKEN` to push release commits
and tags, then explicitly dispatches the publish workflow. It does not require a
long-lived GitHub release token.

Release preflight also checks Jido ecosystem dependency freshness with
`mix hex.outdated --all`. Only Hex packages named `jido` or `jido_*` are
enforced; if any of those dependencies are not on the latest Hex release, the
workflow fails and the dependency must be updated in a normal PR before
publishing. The workflow does not modify dependencies automatically.

## Release Contract

- Publish a new exact `vX.Y.Z` tag for every downstream-facing workflow change.
- Treat published exact tags as immutable.
- Move the floating major tag only after the exact release tag exists and the release is confirmed backward compatible.
- Cut a new major instead of moving the current one if a change would break existing `@vX` consumers.
- Keep `README.md` and `AGENTS.md` aligned whenever release guidance changes.

## Minimal Examples

CI caller:

```yaml
permissions:
  actions: read
  contents: read

jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5
    secrets: inherit
```

Release caller:

```yaml
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:

permissions:
  actions: write
  contents: write

jobs:
  release:
    uses: agentjido/github-actions/.github/workflows/jido-release.yml@v5
    secrets: inherit
```

Review caller:

```yaml
permissions:
  actions: read
  contents: read
  issues: write
  pull-requests: write

jobs:
  review:
    uses: agentjido/github-actions/.github/workflows/jido-review.yml@v5
```

## License

Apache-2.0 - See [LICENSE](LICENSE) for details.
