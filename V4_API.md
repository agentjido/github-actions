# V4 API

This document freezes the intended public API for the `v4.0.0` line while the
implementation is still on the `feat/v4-workflow-platform` branch.

## Public Contract

The public, supported v4 workflows are:

- `.github/workflows/jido-ci.yml`
- `.github/workflows/elixir-release.yml`

The following workflows are internal building blocks and are not part of the
supported downstream API:

- `.github/workflows/elixir-quality.yml`
- `.github/workflows/elixir-test.yml`
- future `.github/workflows/elixir-writeback.yml`
- future `.github/workflows/cla-check.yml`

For v4, changing the name, required inputs, or default behavior of the public
workflows is a breaking change.

## CI Defaults

`jido-ci.yml` is the default consumer entrypoint.

It is intentionally opinionated. The workflow runs a compile gate and then fans
out these explicit checks:

- `mix compile --warnings-as-errors`
- `mix hex.audit`
- `mix format --check-formatted`
- `mix credo --strict`
- `mix dialyzer`
- `mix deps.unlock --check-unused`
- `mix docs`
- REUSE compliance
- Jido community file policy
- optional `mix sobelow`
- optional `mix hex.publish --dry-run`
- optional conventional commit validation

This is the main lesson worth keeping from Ash: the workflow should call out the
baseline checks explicitly so the CI contract is visible in logs and easy to
evolve. The public API should stay small even if the default implementation is
thorough.

## `jido-ci.yml`

### Stable Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `quality_otp_version` | string | `"28"` | OTP version for the quality lane |
| `quality_elixir_version` | string | `"1.19"` | Elixir version for the quality lane |
| `quality_mix_env` | string | `"dev"` | `MIX_ENV` for the quality lane |
| `otp_versions` | string | `["27", "28"]` | JSON array of OTP versions for the test matrix |
| `elixir_versions` | string | `["1.18", "1.19"]` | JSON array of Elixir versions for the test matrix |
| `experimental_compile_elixir_versions` | string | `[]` | JSON array of non-blocking compile-only Elixir versions |
| `experimental_compile_otp_versions` | string | `""` | Optional JSON array of OTP versions for experimental compile jobs |
| `experimental_compile_otp_name` | string | `""` | Optional display label for experimental compile OTP versions |
| `test_mix_env` | string | `"test"` | `MIX_ENV` for the test lane |
| `test_setup_command` | string | `""` | Optional command to run before tests |
| `test_command` | string | `"mix test"` | Main test command |
| `audit_command` | string | `"mix hex.audit"` | Audit command |
| `format_command` | string | `"mix format --check-formatted"` | Format check command |
| `credo_command` | string | `"mix credo --strict"` | Credo command |
| `dialyzer_command` | string | `"mix dialyzer"` | Dialyzer command |
| `unused_deps_command` | string | `"mix deps.unlock --check-unused"` | Unused dependency check command |
| `hex_package_command` | string | `"HEX_API_KEY=${HEX_API_KEY:-dry-run} mix hex.publish --dry-run --yes"` | Hex package validation command |
| `changelog_guard` | boolean | `true` | Enable CHANGELOG.md pull request policy |
| `changelog_guard_mode` | string | `"no_changes"` | CHANGELOG.md policy: `no_changes` or `no_unreleased` |
| `validate_hex_package` | boolean | `true` | Run the Hex package dry-run command on pull requests |
| `docs` | boolean | `true` | Build docs in the quality lane |
| `docs_command` | string | `"mix docs"` | Documentation build command |
| `sobelow` | boolean | `false` | Run Sobelow in the quality lane |
| `sobelow_command` | string | `"mix sobelow"` | Sobelow command |
| `conventional_commits` | boolean | `false` | Validate the current commit message with `git_ops` |
| `conventional_commit_command` | string | `"mix git_ops.check_message"` | Conventional commit validation command |
| `dependency_submission` | boolean | `false` | Submit dependency graph data on default-branch pushes |
| `community_files` | boolean | `true` | Check Jido community file policy |
| `community_files_source_repository` | string | `"agentjido/.github"` | Repository containing canonical community files |
| `community_files_source_ref` | string | `"main"` | Ref containing canonical community files |
| `reuse` | boolean | `true` | Run REUSE compliance check |
| `writeback` | boolean | `false` | Enable write-back after validation succeeds |
| `writeback_command` | string | `""` | Command that produces write-back changes |
| `writeback_paths` | string | `"."` | Space-delimited pathspecs eligible for commit |
| `writeback_commit_message` | string | `"ci: apply automated write-back"` | Commit message used by write-back |
| `writeback_branch_mode` | string | `"pull-request"` | Write-back strategy: `pull-request` or `direct` |

### Stable Secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `HEX_API_KEY` | no | Used for Hex dry-run validation when the repo requires auth |
| `WRITEBACK_TOKEN` | no | Optional stronger token for write-back operations |

### Stable Behavior

- `compile`, `quality`, `test`, and `summary` are the standard CI path.
- default quality checks run as separate jobs for clearer failures and better parallelism.
- docs, community file policy, and REUSE compliance are default-on.
- `writeback` is opt-in and only runs on pushes to the default branch.
- the experimental compile lane is non-blocking
- dependency submission and write-back require explicit opt-in and elevated consumer permissions.
- the standard public workflow needs only `actions: read` and `contents: read`; opt-in features inherit caller-granted write permissions because reusable workflows cannot elevate the caller token.

## `elixir-release.yml`

### Stable Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `otp_version` | string | `"28"` | OTP version for release preparation |
| `elixir_version` | string | `"1.18"` | Elixir version for release preparation |
| `mix_env` | string | `"dev"` | `MIX_ENV` for release preparation |
| `release_command` | string | `"mix git_ops.release --yes"` | Main release command |
| `version_override` | string | `""` | Optional bare SemVer override |
| `preflight_command` | string | `"mix hex.audit && MIX_ENV=test mix test"` | Validation command run before release |
| `release_push_mode` | string | `"direct"` | Release strategy: `direct` or `pull-request` |
| `release_notes_mode` | string | `"changelog"` | GitHub release notes source: `changelog` or `generated` |
| `dry_run` | boolean | `false` | Skip push, GitHub release creation, and Hex publish |
| `hex_dry_run` | boolean | `false` | Perform a Hex dry run after the git release flow |
| `skip_preflight` | boolean | `false` | Skip the preflight command entirely |
| `skip_tests` | boolean | `false` | Backward-compatible alias for skipping preflight |

### Stable Secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `HEX_API_KEY` | no | Used for Hex publish |
| `RELEASE_TOKEN` | no | Optional stronger token for pushing tags or opening release PRs |

### Stable Behavior

- no Hex publish happens before the release commit and tag are pushed in direct mode
- `pull-request` mode prepares the release branch and PR, then stops
- `direct` mode is the full push and publish path
- GitHub release creation happens after the git state is durable on origin and the package version is visible on Hex.pm.
- `dry_run` and `hex_dry_run` never create a GitHub release.

## Compatibility Rules

These rules are frozen for `v4.0.0`:

- adding a new optional input is non-breaking
- renaming or removing an input is breaking
- changing a default in a way that can fail a previously passing consumer is breaking
- moving a check from opt-in to default-on is breaking unless v4 docs already describe it as the baseline
- changing the order of release steps is breaking if it violates the push-before-publish invariant
