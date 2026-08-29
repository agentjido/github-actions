# Jido Ecosystem GitHub Actions

Reusable GitHub Actions workflows for Elixir CI/CD across the Jido ecosystem.

## Public Workflows

| Workflow | Purpose | Public API |
| --- | --- | --- |
| `jido-ci.yml` | Read-only Jido CI: compile gate, quality cache prep, split quality jobs, test matrix, docs, package checks | Yes |
| `jido-release.yml` | Direct or checked git_ops release preparation and idempotent publish recovery | Yes |
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

`jido-ci.yml` owns the default BEAM matrix for standard packages. Callers
should omit `test_matrix`, `otp_versions`, `elixir_versions`, and
`experimental_compile_*` unless the repository needs a deliberate override.
The default quality lane uses Elixir 1.20 on OTP 29. The default required test
matrix keeps the package baseline at Elixir 1.18 while testing compatible
latest toolchains:

- Elixir 1.18 / OTP 27
- Elixir 1.18 / OTP 28
- Elixir 1.19 / OTP 28
- Elixir 1.20 / OTP 29

## Runner Selection

The three public workflows accept an optional `runner` string input. It
defaults to `ubuntu-24.04`, so existing callers do not change. Set the input to
an available runner label when a repository needs another compatible runner:

```yaml
jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5
    with:
      runner: blacksmith-2vcpu-ubuntu-2404
```

`jido-ci.yml` passes the same label to all internal quality and test jobs. Set
the input separately on the release and review callers when those workflows
must use the same runner provider. Keep runner labels fixed in repository-owned
workflow code. Do not take a release runner label from event data or a manual
dispatch input.

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

## Checked Release Staging

`jido-release.yml` has an opt-in `staged_prepare` mode for a protected target
branch. The default is `false`, so existing v5 callers keep the direct prepare
flow.

Checked staging uses this fixed state flow:

1. Require the current repository default branch. Before version planning, refuse any owned-prefix staging branch or durable state for the exact parent. After planning, refuse the exact tag and staging branch again before GitOps generation.
2. Record the exact parent, one generated commit, one annotated tag object that directly names and targets that commit, and the fixed validation policy.
3. Save a private manifest and Git bundle.
4. Push only `release/gitops/VERSION`, or the configured prefix, at the exact generated commit. Explicit push options prevent automatic tag following. Cleanup ownership starts only after the server response reports that this run created the new branch.
5. Dispatch the configured validation workflow and record its returned run ID and first attempt.
6. Require the complete run and each configured job from that exact run attempt to report literal `success` on the generated commit.
7. Recheck the default branch, target parent, staging branch, tag absence, run, jobs, and fixed policy.
8. In one atomic push, fast-forward the default branch, push the existing annotated tag, and move the validated staging branch back to the recorded parent. Exact leases protect all three refs. A narrow retry handles only GitHub's temporary required-check propagation response and repeats all state and validation checks.
9. Verify the remote tag object and peeled commit, record successful promotion, and delete the staging branch with a separate exact lease. A changed or uncertain branch is never deleted. Then dispatch publish once.

The validation caller must use `workflow_dispatch`, accept only the
`release_validation` metadata input, and pass a fixed
`generated_release_command` to `jido-ci.yml`. It must use only `actions: read`
and `contents: read`. Do not pass inherited secrets to validation. The shared
gate checks the repository, ref, commit, parent, tree, changelog blobs,
workflow file, event, actor, changed-file allowlist, and command identity before
it runs the caller command. The allowlist is `CHANGELOG.md` and `mix.exs` by
default. A trusted caller can add a required non-executable release file. It
cannot allow a workflow, validator, or script change. A branch name alone is
not a trust signal. The release tag must be one annotated object whose internal
name is the version and whose direct target is the generated commit. Nested
annotated tags are rejected before staging.

The configured `required_checks` value is the repository-owned check policy.
The workflow records its digest and does not read branch protection. This does
not need repository Administration permission. If protection adds a new check
during prepare, the final protected atomic push rejects promotion until that
new rule is satisfied. The workflow does not claim to snapshot protection.

Before promotion, a failure can delete only a staging branch that the server
confirmed this run created and that is still at the recorded commit. Push
intent alone does not grant cleanup rights. An up-to-date response or a lost
response leaves the branch in place and blocks another prepare because its
owner cannot be proved. A pre-existing branch is never cleanup-owned.
Successful promotion consumes the validated staging tip inside the atomic
transaction, then deletes the branch with a separate exact lease. A failed or
uncertain post-promotion cleanup leaves the branch for manual inspection but
does not stop publish. After promotion, do not run prepare again. Use publish-only
recovery for the existing tag. Publish recovery first checks the remote annotated tag,
target-branch reachability, package version, Hex state, and GitHub release
state. It skips an existing Hex version and creates a missing GitHub release.
It stops if a GitHub release exists while the Hex version is absent.

Direct mode keeps the earlier prepare, branch, lightweight initial tag, Hex
upload, and GitHub release edit behavior when `staged_prepare` is `false`. The
default-branch and annotated-tag trust gates apply only to checked mode. Enable
checked mode when the protected promotion and trusted validation boundary are
required.

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
    secrets:
      HEX_API_KEY: ${{ secrets.HEX_API_KEY }}
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
