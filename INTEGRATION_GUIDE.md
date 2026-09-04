# Jido Package Integration Guide

Use this guide to integrate a Jido package with the shared v5 GitHub Actions
platform.

The standard package integration adds exactly three caller workflows to the
consumer repository:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/review.yml`

Do not add package-local copies of the reusable workflows. Consumer repositories
call the public workflows from this repository:

- `agentjido/github-actions/.github/workflows/jido-ci.yml@v5`
- `agentjido/github-actions/.github/workflows/jido-release.yml@v5`
- `agentjido/github-actions/.github/workflows/jido-review.yml@v5`

Use `@v5` for compatible automatic updates or `@v5.2.5` for the current exact
release. Published exact version tags do not change.

## Runner Override

All three public workflows use GitHub-hosted `ubuntu-24.04` runners by default.
Omit `runner` to keep that default. To opt in to Blacksmith, set
`runner: blacksmith-2vcpu-ubuntu-2404` under `with:` in each caller job that
needs it. CI, review, and release make this choice separately. `jido-ci.yml`
passes the label to every internal quality and test job.

Keep the label fixed in the workflow file. Do not read the release runner label
from pull request data, other event data, or a `workflow_dispatch` input.

## What Not To Implement

Do not add these files for the v5 package rollout:

- `.github/workflows/elixir-ci.yml`
- `.github/workflows/elixir-release.yml`
- `.github/workflows/elixir-quality.yml`
- `.github/workflows/elixir-test.yml`
- `.github/workflows/jido-policy.yml`
- `.github/workflows/jido-sync.yml`

Do not add these CI surfaces unless a future workflow release explicitly brings
them back:

- `quality_command`
- `dependency_submission`
- `writeback`
- `sobelow`
- `reuse`
- `.github/jido-review.yml`

`jido-review.yml` accepts a `review_config_path` input for forward compatibility,
but the current standard Jido rollout does not require a repo-local review
config file.

## Before You Start

Create a rollout branch in the package repository:

```sh
git switch main
git pull --ff-only
git switch -c chore/test-v5-ci
```

Confirm the package already has these local commands working, or decide on the
temporary threshold you will use in CI:

```sh
mix deps.get
mix format --check-formatted
MIX_ENV=test mix compile --warnings-as-errors
mix hex.audit
mix hex.outdated --all || true
mix credo --strict
mix dialyzer
mix deps.unlock --check-unused
mix docs -f html
mix test
HEX_API_KEY=dry-run mix hex.publish --dry-run --yes
```

For packages that are not yet strict-Credo clean, keep CI strict in structure but
use a package-specific Credo threshold. The current rollout thresholds are:

| Package | `credo_command` |
| --- | --- |
| `jido_signal` | omit the input; default is `mix credo --strict` |
| `jido_action` | `mix credo --min-priority higher` |
| `jido` | `mix credo --min-priority higher` |
| `jido_ai` | `mix credo --min-priority high --all` |

## File 1: `.github/workflows/ci.yml`

Create `.github/workflows/ci.yml` with this content:

```yaml
name: CI

on:
  pull_request:
  merge_group:
  push:
    branches:
      - main

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  actions: read
  contents: read

jobs:
  ci:
    name: CI
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v5
    with:
      docs_command: mix docs -f html
      test_command: mix test
```

`jido-ci.yml` owns the default test matrix. Standard packages should omit those
inputs and inherit the central defaults: quality runs on Elixir 1.20 / OTP 29,
and required tests run Elixir 1.18 / OTP 27, Elixir 1.18 / OTP 28,
Elixir 1.19 / OTP 28, and Elixir 1.20 / OTP 29. Add `test_matrix`,
`otp_versions`, `elixir_versions`, or `experimental_compile_*` inputs only
when a repository needs a deliberate override.

If the package needs a temporary Credo threshold, add exactly one
`credo_command` line under `with:`:

```yaml
      credo_command: mix credo --min-priority higher
```

or:

```yaml
      credo_command: mix credo --min-priority high --all
```

Keep CI read-only. Do not add `contents: write`, `pull-requests: write`,
dependency submission, write-back, Sobelow, or REUSE behavior to this workflow.

### CI input for checked release staging

If the package enables checked release staging, add this trusted dispatch input
to the CI caller:

```yaml
on:
  workflow_dispatch:
    inputs:
      release_validation:
        description: Trusted metadata from the release workflow
        required: false
        type: string
        default: ""
```

Pass the metadata and a fixed package command to `jido-ci.yml`:

```yaml
    with:
      release_validation: ${{ inputs.release_validation || '' }}
      generated_release_command: .github/scripts/validate-generated-release.sh
```

The package command must validate the exact GitOps output. It must fail for a
manual changelog edit or an extra generated change. The shared gate exports
`RELEASE_PARENT_SHA`, `RELEASE_SHA`, `RELEASE_TREE_SHA`, `RELEASE_TAG`,
`RELEASE_CHANGELOG_BEFORE_SHA`, and `RELEASE_CHANGELOG_AFTER_SHA` for this
command. Keep the command fixed in both the CI caller and release caller. Do
not take it from a dispatch input. The checked prepare run records the command
identity. The validation gate requires the CI input to have the same identity.

The validation caller must keep only `actions: read` and `contents: read`. Do
not use `secrets: inherit`. Any package-local validation job must check out
`ref: ${{ github.sha }}`. The shared gate also checks that the selected CI
workflow and the caller validation script did not change between the release
parent and generated commit. It rejects all changed files outside the trusted
release allowlist before it runs the caller command.

## File 2: `.github/workflows/release.yml`

Create `.github/workflows/release.yml` with this content:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      operation:
        description: "Release operation: auto, prepare, or publish"
        required: false
        type: choice
        default: auto
        options:
          - auto
          - prepare
          - publish
      tag_name:
        description: "Optional v-prefixed tag for publish simulation"
        required: false
        type: string
        default: ""
      dry_run:
        description: "Dry run (no git push, no tag, no GitHub release, no Hex publish)"
        required: false
        type: boolean
        default: false
      hex_dry_run:
        description: "Hex dry run only (run all git/release steps, but skip actual Hex publish)"
        required: false
        type: boolean
        default: false
      skip_tests:
        description: "Skip tests before release"
        required: false
        type: boolean
        default: false
      version_override:
        description: "Optional bare SemVer override (for example 1.2.3, not v1.2.3)"
        required: false
        type: string
        default: ""
      staged_prepare:
        description: "Validate the generated commit before protected-branch promotion"
        required: false
        type: boolean
        default: false
      staging_branch_prefix:
        description: "Owned release staging branch prefix"
        required: false
        type: string
        default: "release/gitops/"

permissions:
  actions: write
  contents: write

jobs:
  release:
    name: Release
    uses: agentjido/github-actions/.github/workflows/jido-release.yml@v5
    with:
      operation: ${{ inputs.operation || 'auto' }}
      tag_name: ${{ inputs.tag_name || '' }}
      dry_run: ${{ inputs.dry_run || false }}
      hex_dry_run: ${{ inputs.hex_dry_run || false }}
      skip_tests: ${{ inputs.skip_tests || false }}
      version_override: ${{ inputs.version_override || '' }}
      staged_prepare: ${{ inputs.staged_prepare || false }}
      staging_branch_prefix: ${{ inputs.staging_branch_prefix || 'release/gitops/' }}
    secrets:
      HEX_API_KEY: ${{ secrets.HEX_API_KEY }}
```

Release uses two flows:

- `prepare`: run manually with `workflow_dispatch` from the current repository
  default branch. This runs
  `mix git_ops.release`, creates the release commit and tag, and pushes git
  state when `dry_run` is `false`, then explicitly dispatches the publish
  workflow with `GITHUB_TOKEN`.
- `publish`: run by the prepare dispatch or by a human/external `v*` tag push.
  This publishes the existing package version to Hex and then creates the
  GitHub release after Hex confirms the package version.

Use `operation: auto` for normal operation:

- dispatch from the current default branch resolves to `prepare`
- push of a `v*` tag resolves to `publish`

Non-dry-run `prepare` requires `actions: write` and `contents: write`
permissions. It does not require a long-lived GitHub release token.

Non-dry-run `publish` requires a `HEX_API_KEY` secret.

Use `hex_dry_run: true`, not `dry_run: true`, when you want to exercise the Hex
package build without actually publishing to Hex.

Use `dry_run: true` with `operation: prepare` for the safe pre-release check.
This runs preflight plus release preparation logic and does not push a commit,
tag, GitHub release, or Hex package.

For packages with no existing `v*` release tag, the shared release workflow
treats `operation: prepare` as an initial release. A dry run reports the initial
tag it would create. A real prepare creates the initial `vVERSION` tag from the
current `mix.exs` version, then dispatches the publish workflow.

In direct mode, prepare keeps the initial lightweight tag behavior from the
earlier v5 flow, and the dispatched direct publish accepts that exact legacy
tag. Direct prepare also keeps the earlier source-branch behavior. In checked
mode, the workflow creates one empty conventional release commit when GitOps
has no release commit, then creates the local annotated tag at that commit.
This keeps `R^ = P` before state recording.

For brand-new personal Hex packages where you want long-term package-scoped
keys, publish the first release manually or with a temporary broader Hex key,
then rotate to a `package:PACKAGE` scoped key for later automated releases.

Use `hex_dry_run: true` only with `operation: publish` and an existing
`tag_name` when you want to exercise `mix hex.publish --dry-run` for an already
prepared tag.

## Checked Release Configuration

Checked staging is opt-in. `staged_prepare: false` keeps the direct v5 prepare
flow. To enable the checked flow, first add the trusted CI dispatch input and
package validation command from the CI section. Then set these fixed inputs on
the release job:

```yaml
    with:
      staged_prepare: ${{ inputs.staged_prepare || false }}
      validation_workflow: ci.yml
      required_checks: >-
        ["CI / Summary"]
      release_changed_files: >-
        ["CHANGELOG.md","README.md","mix.exs"]
      generated_release_command: .github/scripts/validate-generated-release.sh
      staging_branch_prefix: ${{ inputs.staging_branch_prefix || 'release/gitops/' }}
      validation_timeout_seconds: 2700
```

List each job that must have literal `success` in the dispatched validation
run. Use the exact job name. The complete validation workflow run must also
have `success`. The gate reads jobs from the exact run ID and first run
attempt. A same-name job from another run cannot satisfy the policy. A missing,
pending, skipped, neutral, cancelled, failed, stale, or timed-out job stops
promotion.

`release_changed_files` is a fixed, repository-owned allowlist. Use the default
`["CHANGELOG.md","mix.exs"]` when GitOps changes only those files. Add
`README.md` only when the trusted parent GitOps configuration manages the
README version. Workflow, validator, `.github`, and script changes are always
rejected. The workflow records one digest for the required jobs, allowlist, and
exact validation command. That policy cannot change during one release run.

Do not pass secrets to the CI dispatch. Do not add a PAT, GitHub App secret,
Administration permission, admin bypass, release PR, or protection change.
Reusable workflows cannot add permissions that the caller did not grant.

### Checked state flow

The prepare run uses these identities:

- `P`: exact target-branch parent before generation
- `R`: one generated GitOps commit whose only parent is `P`
- `A`: one annotated tag object whose internal name is `V` and whose direct commit target is `R`
- `B`: deterministic `release/gitops/VERSION` branch
- `V`: returned validation run ID
- `U`: returned publish run ID

Before version planning, the workflow checks `P`, the default branch, all
branches under the owned staging prefix, and the durable state marker for `P`.
This stops a hard-loss retry before it can call the release planner. After the
planner derives the intended version, a second precheck validates the tag and
ref syntax and refuses the exact `B`, remote tag, or state marker before GitOps
generation. The complete prepare validation runs again after generation.

The workflow saves a private manifest and Git bundle before it changes a remote
ref. It records push intent, then uses an absent-ref lease and porcelain server
response to push only `B` at `R`. Cleanup ownership starts only when the
successful response explicitly reports a new branch. An up-to-date response is
pre-existing state. A lost response is uncertain state and does not grant
cleanup rights. Explicit `--no-follow-tags` behavior keeps `A` local even when
a runner has `push.followTags=true`. It dispatches the exact CI workflow on `B`
and records `V`. The trusted gate requires the repository, ref, `github.sha`, checkout SHA,
parent, tree, changelog blobs, workflow file, changed-file allowlist, and command
identity to match the recorded state before caller validation runs.

Before state recording, the workflow also requires the local version tag to be
exactly one annotated object. Its internal `tag` header must equal `V`, its
direct target type must be `commit`, and its direct target must be `R`. It
records this shape and checks the same local object again before promotion and
publish dispatch. Nested annotated tags and mismatched internal names stop
before staging.

After the full run and configured jobs succeed on `R`, the workflow rechecks
the repository default branch, `P`, `B`, tag absence, the exact run attempt,
the jobs, and the fixed policy. It atomically fast-forwards the default branch
to `R`, pushes the existing local tag ref at `A`, and moves `B` from `R` back
to `P`. This real ref update keeps `B` in the receive-pack transaction while
GitHub evaluates protection. Exact leases protect all three refs. A narrow,
bounded retry handles only GitHub's temporary required-check propagation
response and repeats every remote-state and validation check first. The
workflow verifies `A` and its peeled `R`, records promotion, then deletes `B`
with a separate exact lease. A changed or uncertain `B` is left for manual
inspection and does not stop publish. The workflow then dispatches publish
once and records `U`.

The workflow does not read or snapshot branch protection. The caller-supplied
`required_checks` JSON is the exact validation policy. If repository protection
adds a new required check during prepare, the final protected atomic push safely
rejects the transaction. This requires no Administration permission.

### Failure and recovery

| State | Result | Recovery |
| --- | --- | --- |
| Failure before `B` is pushed | No remote release state | Fix the cause. A saved state artifact can still block a repeated version. |
| Failure after the server confirms this run created `B` | Main and tag stay unchanged | Automatic cleanup deletes only this run's owned `B` at `R`. It refuses a changed branch or invalid ownership record. |
| Pre-existing `B`, including `B = R` | Main and tag stay unchanged | Stop. An up-to-date push result does not grant ownership and does not delete the branch. |
| Lost staging-push response or runner loss | `B` can remain; the private state artifact remains | Do not regenerate the commit. Ownership is uncertain, so automatic cleanup is refused. The branch and artifact block another prepare. |
| Atomic promotion rejection | Main, tag, and `B` all stay unchanged | Temporary required-check propagation is retried only after all state and validation checks pass again. For any final rejection, fix the external cause, then use owned pre-promotion cleanup only when the run recorded server-confirmed branch creation. |
| Post-promotion staging cleanup is rejected or uncertain | Main and the annotated tag are final; `B` can remain at `P` or a changed SHA | Publish continues. Inspect `B` and delete it manually only when its current SHA and ownership are known. |
| Failure after promotion | Main and annotated tag are final | Do not run prepare. Run publish-only recovery for the existing tag. |
| Dispatch response without a run ID | The dispatch result is uncertain | Do not dispatch again automatically. Inspect Actions and use the recorded state. |

For publish-only recovery, run the Release workflow with `operation: publish`,
the existing `tag_name`, `staged_prepare: true`, and the same staging branch
prefix. Recovery validates the remote annotated tag, its reachability from the
target branch, and package/tag equality before it changes package state.

Publish recovery uses this state table:

| Hex version | GitHub release | Action |
| --- | --- | --- |
| absent | absent | Publish Hex once, confirm it, then create the GitHub release. |
| present | absent | Skip Hex and create the GitHub release. |
| present | present | Leave both states unchanged and report success. |
| absent | present | Stop because the state is inconsistent. |

Publish runs use tag-based concurrency. A failed or uncertain upload is not
retried in the same run. Start publish-only recovery only after you check the
external Hex and GitHub state.

In checked mode, the release job validates its source before checkout or Mix
execution. Prepare accepts only the current default-branch SHA. Publish accepts
only an existing annotated version tag whose peeled commit is reachable from
the current default branch. A feature branch, stale branch SHA, lightweight
tag, unrelated tag, or unreachable tag stops before release code, write API
calls, or secret-dependent steps. Direct mode keeps the earlier branch,
lightweight initial tag, Hex upload, and GitHub release edit behavior when
`staged_prepare` is `false`.

## File 3: `.github/workflows/review.yml`

Create `.github/workflows/review.yml` with this content:

```yaml
name: Jido Review

on:
  pull_request:
    branches:
      - main

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  actions: read
  contents: read
  issues: write
  pull-requests: write

jobs:
  review:
    name: Jido Review
    uses: agentjido/github-actions/.github/workflows/jido-review.yml@v5
```

The review lane is advisory. It writes:

- a job summary
- `jido-review.md`
- `jido-review.json`
- one sticky PR comment when same-repo PR permissions allow it

The review lane checks conventional commit PR titles by default. Keep this
separate from CI; do not add review behavior to `.github/workflows/ci.yml`.

## Release Prerequisites

Each package that uses `.github/workflows/release.yml` needs `git_ops`
configured.

In `mix.exs`, the package should include `git_ops` for development release work:

```elixir
defp deps do
  [
    {:git_ops, "~> 2.9", only: :dev, runtime: false}
  ]
end
```

In the package config, set the repository URL and tag prefix. Replace
`YOUR_REPO` with the package repository name:

```elixir
if config_env() != :prod do
  config :git_ops,
    mix_project: Mix.Project.get!(),
    changelog_file: "CHANGELOG.md",
    repository_url: "https://github.com/agentjido/YOUR_REPO",
    manage_mix_version?: true,
    version_tag_prefix: "v"
end
```

Add or confirm these repository or organization secrets:

| Secret | Required for | Notes |
| --- | --- | --- |
| `HEX_API_KEY` | real `publish` | Hex API key with publish rights for the package |

## Local Validation

Run this from the package repository before pushing the rollout branch:

```sh
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].sort.each { |f| YAML.load_file(f); puts f }'
git diff --check
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
```

Run the package checks that CI will enforce:

```sh
mix format --check-formatted
MIX_ENV=test mix compile --warnings-as-errors
mix hex.audit
mix hex.outdated --all || true
mix credo --strict
mix dialyzer
mix deps.unlock --check-unused
mix docs -f html
mix test
HEX_API_KEY=dry-run mix hex.publish --dry-run --yes
```

If the package is using a temporary Credo threshold, replace
`mix credo --strict` with the exact `credo_command` configured in
`.github/workflows/ci.yml`.

`mix hex.outdated --all` exits non-zero for any outdated dependency, so the
local command above is inspection-oriented. The release workflow runs it as a
targeted freshness gate and fails only when a Hex dependency named `jido` or
`jido_*` is not on its latest Hex release. Fix those failures by updating the
dependency in a normal PR before publishing; the release workflow intentionally
does not run `mix deps.update` or rewrite lockfiles.

Search for stale workflow references:

```sh
rg -n 'feat/v4-workflow-platform|elixir-release.yml|elixir-lint.yml@|elixir-test.yml@|quality_command|dependency_submission|writeback|RELEASE_TOKEN|REUSE|Sobelow|sobelow' .github/workflows
```

This search should return no matches.

## Pull Request Checklist

Open one rollout PR per package. The PR should contain only the workflow
integration and any required package-quality cleanup.

Use a conventional commit title:

```text
ci: roll out jido workflows v5
```

The PR should show these checks:

- `CI / Resolve Platform`
- `CI / Compile`
- `CI / Prepare Quality Cache`
- `CI / Quality / Audit`
- `CI / Quality / Changelog Guard`
- `CI / Quality / Credo`
- `CI / Quality / Dialyzer`
- `CI / Quality / Docs`
- `CI / Quality / Format`
- `CI / Quality / Hex Package Dry Run`
- `CI / Quality / Unused Deps`
- `CI / Test / 27 - 1.18`
- `CI / Test / 28 - 1.18`
- `CI / Test / 28 - 1.19`
- `CI / Test / 29 - 1.20`
- `CI / Summary`
- `Jido Review / Jido Review`

There should be no skipped dependency-submission job, no write-back job, no
Sobelow job, and no REUSE job.

## Release Dry-Run Checklist

After the PR is open, test release preparation without pushing git state:

1. Open the package repository in GitHub.
2. Go to Actions.
3. Select `Release`.
4. Run the workflow on the rollout branch.
5. Use `operation: auto`.
6. Set `dry_run: true`.
7. Leave `hex_dry_run: false`.

Expected result: the workflow resolves to `prepare`, runs release preflight and
release preparation, and does not push commits, tags, GitHub releases, or Hex
packages.

To simulate publish packaging, use an existing `v*` tag:

1. Run the `Release` workflow manually.
2. Set `operation: publish`.
3. Set `tag_name` to an existing tag, for example `v1.2.3`.
4. Set `hex_dry_run: true`.
5. Keep `dry_run: false`.

Expected result: the workflow checks out the tag, validates the package version,
runs the Hex publish dry run, and does not create a real Hex release.

## Checked-Staging Rollout

Use the published `@v5.2.5` pin when adopting checked staging. For a future
shared workflow release:

1. Select a new, unused exact version.
2. Validate the shared workflow change in a consumer using its commit SHA.
3. Review and merge the change, then require successful checks on `main`.
4. Create an annotated exact version tag on that tested release commit.
5. Move `v5` to the same commit only when the change is backward compatible.
6. Update consumers that use an exact version in separate PRs. Run full CI and
   the release dry run before their first checked release.

Do not change any published exact version tag or tag a feature branch as a
release. Breaking changes require a new major version.

## Historical Rollout PRs

The first v4 package rollout used these PRs as reference implementations:

- `jido`: https://github.com/agentjido/jido/pull/267
- `jido_action`: https://github.com/agentjido/jido_action/pull/160
- `jido_signal`: https://github.com/agentjido/jido_signal/pull/149
- `jido_ai`: https://github.com/agentjido/jido_ai/pull/274
