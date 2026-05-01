# Jido Ecosystem GitHub Actions

Reusable GitHub Actions workflows for Elixir CI/CD across the Jido ecosystem.

## Workflows

| Workflow | Purpose | Public API |
| --- | --- | --- |
| `jido-ci.yml` | Read-only Jido CI: compile gate, split quality jobs, test matrix, docs, policy checks | Yes |
| `jido-release.yml` | Tag-driven Hex publish and dispatch-supported git_ops release preparation | Yes |
| `jido-review.yml` | Advisory pull request review packet, artifacts, summary, and optional sticky comment | Yes |
| `elixir-quality.yml` | Internal quality building block used by `jido-ci.yml` | No |
| `elixir-test.yml` | Internal test building block used by `jido-ci.yml` | No |
| `elixir-lint.yml` | Legacy v3 lint workflow | No new v4 adoption |

## Quick Start

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  actions: read
  contents: read

jobs:
  ci:
    name: CI
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
    secrets: inherit
```

Create `.github/workflows/release.yml`:

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
        options: [auto, prepare, publish]
      dry_run:
        required: false
        type: boolean
        default: false
      hex_dry_run:
        required: false
        type: boolean
        default: false

permissions:
  actions: read
  contents: write

jobs:
  release:
    name: Release
    uses: agentjido/github-actions/.github/workflows/jido-release.yml@v4
    with:
      operation: ${{ inputs.operation || 'auto' }}
      dry_run: ${{ inputs.dry_run || false }}
      hex_dry_run: ${{ inputs.hex_dry_run || false }}
    secrets: inherit
```

Create `.github/workflows/review.yml`:

```yaml
name: Jido Review

on:
  pull_request:
    branches: [main]

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

## `jido-ci.yml` Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `quality_otp_version` | string | `28` | OTP version for compile and quality jobs |
| `quality_elixir_version` | string | `1.19` | Elixir version for compile and quality jobs |
| `quality_mix_env` | string | `dev` | `MIX_ENV` for quality jobs |
| `otp_versions` | string | `["27", "28"]` | JSON array for the test matrix |
| `elixir_versions` | string | `["1.18", "1.19"]` | JSON array for the test matrix |
| `experimental_compile_elixir_versions` | string | `[]` | Non-blocking compile-only Elixir versions |
| `experimental_compile_otp_versions` | string | `""` | Optional OTP matrix for experimental compile jobs |
| `experimental_compile_otp_name` | string | `""` | Optional display label for experimental OTP versions |
| `test_mix_env` | string | `test` | `MIX_ENV` for tests |
| `test_setup_command` | string | `""` | Optional command before tests |
| `test_command` | string | `mix test` | Test command |
| `audit_command` | string | `mix hex.audit` | Audit command |
| `format_command` | string | `mix format --check-formatted` | Format check command |
| `credo_command` | string | `mix credo --strict` | Credo command |
| `dialyzer_command` | string | `mix dialyzer` | Dialyzer command |
| `unused_deps_command` | string | `mix deps.unlock --check-unused` | Unused dependency check command |
| `hex_package_command` | string | `HEX_API_KEY=${HEX_API_KEY:-dry-run} mix hex.publish --dry-run --yes` | Hex package validation command |
| `changelog_guard` | boolean | `true` | Enable CHANGELOG.md PR policy |
| `changelog_guard_mode` | string | `no_changes` | `no_changes` or `no_unreleased` |
| `validate_hex_package` | boolean | `true` | Run the Hex package dry-run command on PRs |
| `docs` | boolean | `true` | Run docs build |
| `docs_command` | string | `mix docs` | Docs build command |
| `sobelow` | boolean | `false` | Run Sobelow |
| `sobelow_command` | string | `mix sobelow` | Sobelow command |
| `conventional_commits` | boolean | `false` | Validate current commit message with git_ops |
| `conventional_commit_command` | string | `mix git_ops.check_message` | Conventional commit command |
| `reuse` | boolean | `true` | Run REUSE compliance check |

## `jido-release.yml` Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `operation` | string | `auto` | `auto`, `prepare`, or `publish` |
| `tag_name` | string | `""` | Optional v-prefixed tag for dispatch publish simulation |
| `otp_version` | string | `28` | OTP version |
| `elixir_version` | string | `1.18` | Elixir version |
| `mix_env` | string | `dev` | `MIX_ENV` for release |
| `release_command` | string | `mix git_ops.release --yes` | Release preparation command |
| `version_override` | string | `""` | Optional bare SemVer override |
| `preflight_command` | string | `mix hex.audit && MIX_ENV=test mix test` | Validation before release |
| `release_notes_mode` | string | `changelog` | `changelog` or `generated` |
| `dry_run` | boolean | `false` | Run without push, GitHub release, or Hex publish |
| `hex_dry_run` | boolean | `false` | Run Hex publish as a dry run |
| `skip_preflight` | boolean | `false` | Skip preflight validation |
| `skip_tests` | boolean | `false` | Backward-compatible alias for skipping preflight |

## `jido-review.yml` Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `post_comment` | boolean | `true` | Post or update one sticky PR comment |
| `fail_on_error` | boolean | `false` | Fail when review generation/commenting fails |
| `review_config_path` | string | `.github/jido-review.yml` | Optional repo review config path |
| `max_diff_lines` | number | `2000` | Maximum diff lines in the packet |
| `agent_review` | boolean | `false` | Run an optional external review command |
| `agent_review_command` | string | `""` | Command that consumes the review packet |

## Version Pinning

- `@v4`: Recommended for compatible automatic updates after v4 is released.
- `@v4.0.0`: Pin to an exact release for maximum reproducibility.
- `@main`: Not recommended for production consumers.

```yaml
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
uses: agentjido/github-actions/.github/workflows/jido-release.yml@v4
uses: agentjido/github-actions/.github/workflows/jido-review.yml@v4
```

These refs are git refs on this repository, so they version the entire workflow repo rather than an individual workflow file.

## Permissions

- `jido-ci.yml` needs `actions: read` and `contents: read`.
- `jido-release.yml` needs `actions: read` and `contents: write`; non-dry-run `prepare` requires a `RELEASE_TOKEN` secret so pushed tags can trigger publish workflows.
- `jido-review.yml` needs `actions: read`, `contents: read`, `issues: write`, and `pull-requests: write` when `post_comment` is enabled.

Reusable workflows cannot elevate the caller's token permissions. The caller workflow must grant the maximum scopes the reusable workflow needs.

## Release Contract

- Publish a new exact `vX.Y.Z` tag for every downstream-facing workflow change.
- Treat published exact tags as immutable.
- Move the floating major tag only after the exact release tag exists and the release is confirmed backward compatible.
- Cut a new major instead of moving the current one if a change would break existing `@vX` consumers.
- Keep `README.md` and `AGENTS.md` aligned whenever release guidance changes.

## Release Prerequisites

Consumer repositories need `git_ops` configured:

```elixir
defp deps do
  [
    {:git_ops, "~> 2.9", only: :dev, runtime: false}
  ]
end
```

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

## License

MIT License - See [LICENSE](LICENSE) for details.
