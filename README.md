# Jido Ecosystem GitHub Actions

Reusable GitHub Actions workflows for Elixir CI/CD across the Jido ecosystem.

## Workflows

| Workflow | Purpose | Public API |
| --- | --- | --- |
| `jido-ci.yml` | Default Jido CI: compile gate, split quality jobs, test matrix, optional policy/write-back jobs | Yes |
| `elixir-release.yml` | Protected-branch-safe git_ops releases, Hex publish, and GitHub releases | Yes |
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

For migration from an existing repo-specific `mix quality` alias:

```yaml
jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
    secrets: inherit
    with:
      quality_command: mix quality
```

For write-back automation:

```yaml
permissions:
  actions: read
  contents: write
  pull-requests: write

jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
    secrets: inherit
    with:
      writeback: true
      writeback_command: mix format
      writeback_paths: "."
      writeback_branch_mode: pull-request
```

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  workflow_dispatch:

permissions:
  actions: read
  contents: write
  pull-requests: write

jobs:
  release:
    name: Release
    uses: agentjido/github-actions/.github/workflows/elixir-release.yml@v4
    secrets: inherit
```

## `jido-ci.yml` Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `quality_otp_version` | string | `28` | OTP version for compile and quality jobs |
| `quality_elixir_version` | string | `1.19` | Elixir version for compile and quality jobs |
| `quality_mix_env` | string | `dev` | `MIX_ENV` for quality and write-back jobs |
| `otp_versions` | string | `["27", "28"]` | JSON array for the test matrix |
| `elixir_versions` | string | `["1.18", "1.19"]` | JSON array for the test matrix |
| `experimental_compile_elixir_versions` | string | `[]` | Non-blocking compile-only Elixir versions |
| `experimental_compile_otp_versions` | string | `""` | Optional OTP matrix for experimental compile jobs |
| `experimental_compile_otp_name` | string | `""` | Optional display label for experimental OTP versions |
| `test_mix_env` | string | `test` | `MIX_ENV` for tests |
| `test_setup_command` | string | `""` | Optional command before tests |
| `test_command` | string | `mix test` | Test command |
| `quality_command` | string | `""` | Full replacement quality command for migration |
| `changelog_guard` | boolean | `true` | Enable CHANGELOG.md PR policy |
| `changelog_guard_mode` | string | `no_changes` | `no_changes` or `no_unreleased` |
| `validate_hex_package` | boolean | `true` | Run `mix hex.publish --dry-run` on PRs |
| `docs` | boolean | `false` | Run docs build |
| `docs_command` | string | `mix docs` | Docs build command |
| `sobelow` | boolean | `false` | Run Sobelow |
| `sobelow_command` | string | `mix sobelow` | Sobelow command |
| `conventional_commits` | boolean | `false` | Validate current commit message with git_ops |
| `conventional_commit_command` | string | `mix git_ops.check_message` | Conventional commit command |
| `dependency_submission` | boolean | `false` | Submit dependency graph data on default-branch pushes |
| `credo_sarif` | boolean | `false` | Upload Credo SARIF to code scanning |
| `writeback` | boolean | `false` | Enable automated write-back |
| `writeback_command` | string | `""` | Command that produces write-back changes |
| `writeback_paths` | string | `.` | Space-delimited pathspecs eligible for write-back commits |
| `writeback_commit_message` | string | `ci: apply automated write-back` | Write-back commit message |
| `writeback_branch_mode` | string | `pull-request` | `pull-request` or `direct` |

## `elixir-release.yml` Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `otp_version` | string | `28` | OTP version |
| `elixir_version` | string | `1.18` | Elixir version |
| `mix_env` | string | `dev` | `MIX_ENV` for release |
| `release_command` | string | `mix git_ops.release --yes` | Release command |
| `version_override` | string | `""` | Optional bare SemVer override |
| `preflight_command` | string | `mix hex.audit && mix test` | Validation before release |
| `db_setup_command` | string | `""` | Optional release preflight setup command |
| `database_url` | string | `""` | Optional release preflight `DATABASE_URL` |
| `release_push_mode` | string | `direct` | `direct` or `pull-request` |
| `release_notes_mode` | string | `changelog` | `changelog` or `generated` |
| `dry_run` | boolean | `false` | Run release flow without push, GitHub release, or Hex publish |
| `hex_dry_run` | boolean | `false` | Push git release state but run Hex dry-run instead of publish |
| `skip_preflight` | boolean | `false` | Skip preflight validation |
| `skip_tests` | boolean | `false` | Backward-compatible alias for skipping preflight |

## Version Pinning

- `@v4`: Recommended for compatible automatic updates after v4 is released.
- `@v4.0.0`: Pin to an exact release for maximum reproducibility.
- `@main`: Not recommended for production consumers.

```yaml
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4
uses: agentjido/github-actions/.github/workflows/jido-ci.yml@v4.0.0
```

These refs are git refs on this repository, so they version the entire workflow repo rather than an individual workflow file.

## Permissions

The default CI path needs `actions: read` and `contents: read`. The workflow
uses the Actions API to resolve the reusable workflow repository and ref for
its internal helper checkout.

Opt-in features need additional permissions in the consumer workflow:

- `dependency_submission: true` needs `contents: write`.
- `credo_sarif: true` needs `security-events: write`.
- `writeback: true` needs `contents: write` and `pull-requests: write`.
- direct protected-branch write-back needs a token or GitHub App that branch rules allow to push.

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

## Consumer Repositories

- [jido](https://github.com/agentjido/jido)
- [jido_action](https://github.com/agentjido/jido_action)
- [jido_signal](https://github.com/agentjido/jido_signal)
- [req_llm](https://github.com/agentjido/req_llm)
- [llm_db](https://github.com/agentjido/llm_db)

## License

MIT License - See [LICENSE](LICENSE) for details.
