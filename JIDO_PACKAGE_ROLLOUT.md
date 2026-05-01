# Jido Package Workflow Rollout

Use this guide when moving a Jido package to the shared v4 workflow platform.

## Workflow Files

Each package should have exactly these public caller workflows:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/review.yml`

Use `@v4` for normal rollout. Use `@v4.0.0` only when a package needs exact
reproducibility.

## CI

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
      experimental_compile_elixir_versions: '["v1.20.0-rc.4"]'
      experimental_compile_otp_versions: '["28.4.1"]'
      experimental_compile_otp_name: '28'
      docs_command: mix docs -f html
      test_command: mix test
```

If strict Credo is not clean yet, set `credo_command` to the package's current
quality threshold and track strict cleanup separately. Current rollout examples:

- `jido_signal`: default `mix credo --strict`
- `jido_action`: `mix credo --min-priority higher`
- `jido`: `mix credo --min-priority higher`
- `jido_ai`: `mix credo --min-priority high --all`

## Release

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

Non-dry-run `prepare` requires a `RELEASE_TOKEN` secret because tags pushed with
`GITHUB_TOKEN` do not reliably trigger the tag-publish workflow.

## Review

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

The review lane is advisory. It writes artifacts and a job summary, posts a
sticky same-repo PR comment when permissions allow it, and checks the PR title
against conventional commit format by default.

## Validation Checklist

Before opening or updating the package PR:

```sh
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].sort.each { |f| YAML.load_file(f); puts f }'
git diff --check
go run github.com/rhysd/actionlint/cmd/actionlint@latest
```

Search for stale v3 or branch-ref workflow usage:

```sh
rg -n 'feat/v4-workflow-platform|elixir-release.yml|elixir-lint.yml@|elixir-test.yml@|REUSE|Sobelow|sobelow' .github/workflows
```

The expected rollout PR checks are:

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
- `CI / Test / <matrix entries>`
- `CI / Summary`
- `Jido Review / Jido Review`

There should be no REUSE job, Sobelow job, or conventional-commit job inside CI.
