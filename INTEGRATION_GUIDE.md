# Jido Package Integration Guide

Use this guide to integrate a Jido package with the shared v4 GitHub Actions
platform.

The standard package integration adds exactly three caller workflows to the
consumer repository:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/review.yml`

Do not add package-local copies of the reusable workflows. Consumer repositories
call the public workflows from this repository:

- `agentjido/github-actions/.github/workflows/jido-ci.yml@v4`
- `agentjido/github-actions/.github/workflows/jido-release.yml@v4`
- `agentjido/github-actions/.github/workflows/jido-review.yml@v4`

## What Not To Implement

Do not add these files for the v4 package rollout:

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
git switch -c chore/test-v4-ci
```

Confirm the package already has these local commands working, or decide on the
temporary threshold you will use in CI:

```sh
mix deps.get
mix format --check-formatted
MIX_ENV=test mix compile --warnings-as-errors
mix hex.audit
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
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
    secrets: inherit
    with:
      otp_versions: '["27", "28"]'
      elixir_versions: '["1.18", "1.19"]'
      # Optional: keep this lane on the latest exact Elixir release-candidate tag.
      experimental_compile_elixir_versions: '["<latest-elixir-rc-tag>"]'
      experimental_compile_otp_versions: '["<matching-otp-version>"]'
      experimental_compile_otp_name: '<otp-major>'
      docs_command: mix docs -f html
      test_command: mix test
```

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

permissions:
  actions: read
  contents: write

jobs:
  release:
    name: Release
    uses: agentjido/github-actions/.github/workflows/jido-release.yml@v4
    with:
      operation: ${{ inputs.operation || 'auto' }}
      tag_name: ${{ inputs.tag_name || '' }}
      dry_run: ${{ inputs.dry_run || false }}
      hex_dry_run: ${{ inputs.hex_dry_run || false }}
      skip_tests: ${{ inputs.skip_tests || false }}
      version_override: ${{ inputs.version_override || '' }}
    secrets: inherit
```

Release uses two flows:

- `prepare`: run manually with `workflow_dispatch` from a branch. This runs
  `mix git_ops.release`, creates the release commit and tag, and pushes git
  state when `dry_run` is `false`.
- `publish`: run automatically when a `v*` tag is pushed. This publishes the
  existing package version to Hex and then creates the GitHub release after Hex
  confirms the package version.

Use `operation: auto` for normal operation:

- dispatch from a branch resolves to `prepare`
- push of a `v*` tag resolves to `publish`

Non-dry-run `prepare` requires a `RELEASE_TOKEN` secret. Tags pushed with the
default `GITHUB_TOKEN` do not reliably trigger the tag-publish workflow.

Non-dry-run `publish` requires a `HEX_API_KEY` secret.

Use `hex_dry_run: true`, not `dry_run: true`, when you want to exercise the Hex
package build without actually publishing to Hex.

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
    uses: agentjido/github-actions/.github/workflows/jido-review.yml@v4
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
| `RELEASE_TOKEN` | non-dry-run `prepare` | PAT or GitHub App token that can push commits and tags |

## Local Validation

Run this from the package repository before pushing the rollout branch:

```sh
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].sort.each { |f| YAML.load_file(f); puts f }'
git diff --check
go run github.com/rhysd/actionlint/cmd/actionlint@latest
```

Run the package checks that CI will enforce:

```sh
mix format --check-formatted
MIX_ENV=test mix compile --warnings-as-errors
mix hex.audit
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

Search for stale workflow references:

```sh
rg -n 'feat/v4-workflow-platform|elixir-release.yml|elixir-lint.yml@|elixir-test.yml@|quality_command|dependency_submission|writeback|REUSE|Sobelow|sobelow' .github/workflows
```

This search should return no matches.

## Pull Request Checklist

Open one rollout PR per package. The PR should contain only the workflow
integration and any required package-quality cleanup.

Use a conventional commit title:

```text
ci: roll out jido workflows v4
```

The PR should show these checks:

- `CI / Resolve Platform`
- `CI / Compile`
- `CI / Quality / Audit`
- `CI / Quality / Changelog Guard`
- `CI / Quality / Credo`
- `CI / Quality / Dialyzer`
- `CI / Quality / Docs`
- `CI / Quality / Format`
- `CI / Quality / Hex Package Dry Run`
- `CI / Quality / Unused Deps`
- `CI / Test / 27 - 1.18`
- `CI / Test / 27 - 1.19`
- `CI / Test / 28 - 1.18`
- `CI / Test / 28 - 1.19`
- `CI / Test / <matching-otp-version> - <latest-elixir-rc-tag> (experimental compile)`
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

## Current Rollout PRs

The first v4 package rollout used these PRs as reference implementations:

- `jido`: https://github.com/agentjido/jido/pull/267
- `jido_action`: https://github.com/agentjido/jido_action/pull/160
- `jido_signal`: https://github.com/agentjido/jido_signal/pull/149
- `jido_ai`: https://github.com/agentjido/jido_ai/pull/274
