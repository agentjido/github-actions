# V4 API

This document freezes the intended public API for the `v4.0.0` line while the
implementation is still on the `feat/v4-workflow-platform` branch.

## Public Contract

The public, supported v4 workflows are:

- `.github/workflows/jido-ci.yml`
- `.github/workflows/jido-release.yml`
- `.github/workflows/jido-review.yml`

The following workflows are internal building blocks and are not part of the
supported downstream API:

- `.github/workflows/elixir-quality.yml`
- `.github/workflows/elixir-test.yml`
- `.github/workflows/elixir-lint.yml`

For v4, changing the name, required inputs, or default behavior of a public
workflow is a breaking change.

## `jido-ci.yml`

`jido-ci.yml` is the read-only strict CI entrypoint. It runs a compile gate and
then fans out explicit quality, package, docs, license, and test jobs.

### Stable Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `quality_otp_version` | string | `"28"` | OTP version for compile and quality |
| `quality_elixir_version` | string | `"1.19"` | Elixir version for compile and quality |
| `quality_mix_env` | string | `"dev"` | `MIX_ENV` for quality checks |
| `otp_versions` | string | `["27", "28"]` | JSON OTP test matrix |
| `elixir_versions` | string | `["1.18", "1.19"]` | JSON Elixir test matrix |
| `experimental_compile_elixir_versions` | string | `[]` | Non-blocking compile-only Elixir versions |
| `experimental_compile_otp_versions` | string | `""` | Optional OTP matrix for experimental compile |
| `experimental_compile_otp_name` | string | `""` | Optional experimental OTP label |
| `test_mix_env` | string | `"test"` | `MIX_ENV` for tests |
| `test_setup_command` | string | `""` | Optional command before tests |
| `test_command` | string | `"mix test"` | Main test command |
| `audit_command` | string | `"mix hex.audit"` | Audit command |
| `format_command` | string | `"mix format --check-formatted"` | Format command |
| `credo_command` | string | `"mix credo --strict"` | Credo command |
| `dialyzer_command` | string | `"mix dialyzer"` | Dialyzer command |
| `unused_deps_command` | string | `"mix deps.unlock --check-unused"` | Unused dependency command |
| `hex_package_command` | string | `"HEX_API_KEY=${HEX_API_KEY:-dry-run} mix hex.publish --dry-run --yes"` | Hex package dry-run command |
| `changelog_guard` | boolean | `true` | Enable CHANGELOG.md PR policy |
| `changelog_guard_mode` | string | `"no_changes"` | `no_changes` or `no_unreleased` |
| `validate_hex_package` | boolean | `true` | Run Hex package dry run during CI |
| `docs` | boolean | `true` | Build docs |
| `docs_command` | string | `"mix docs"` | Docs command |
| `sobelow` | boolean | `false` | Run Sobelow |
| `sobelow_command` | string | `"mix sobelow"` | Sobelow command |
| `conventional_commits` | boolean | `false` | Run `git_ops` commit validation |
| `conventional_commit_command` | string | `"mix git_ops.check_message"` | Conventional commit command |
| `reuse` | boolean | `true` | Run REUSE compliance check |

### Stable Secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `HEX_API_KEY` | no | Used only for Hex dry-run validation when a repo requires auth |

### Stable Behavior

- The standard public workflow needs only `actions: read` and `contents: read`.
- CI never pushes commits, opens PRs, submits dependency data, publishes releases, or comments on PRs.
- The experimental compile lane is non-blocking.

## `jido-release.yml`

`jido-release.yml` is the privileged release lane. Consumer workflows should run
it on both `push.tags: ["v*"]` and `workflow_dispatch`.

### Stable Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `operation` | string | `"auto"` | `auto`, `prepare`, or `publish` |
| `tag_name` | string | `""` | Optional v-prefixed tag for dispatch publish simulation |
| `otp_version` | string | `"28"` | OTP version |
| `elixir_version` | string | `"1.18"` | Elixir version |
| `mix_env` | string | `"dev"` | `MIX_ENV` for release |
| `release_command` | string | `"mix git_ops.release --yes"` | Release preparation command |
| `version_override` | string | `""` | Optional bare SemVer override |
| `preflight_command` | string | `"mix hex.audit && MIX_ENV=test mix test"` | Validation command |
| `release_notes_mode` | string | `"changelog"` | `changelog` or `generated` |
| `dry_run` | boolean | `false` | Skip push, GitHub release, and Hex publish |
| `hex_dry_run` | boolean | `false` | Run Hex publish as dry run |
| `skip_preflight` | boolean | `false` | Skip preflight |
| `skip_tests` | boolean | `false` | Backward-compatible alias for skipping preflight |

### Stable Secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `HEX_API_KEY` | no | Used for Hex publish |
| `RELEASE_TOKEN` | prepare non-dry-run | Token that can push release commits/tags and trigger tag workflows |

### Stable Behavior

- `operation: auto` publishes on `refs/tags/v*`; otherwise it prepares a release.
- `prepare` runs `git_ops.release`, then pushes the release commit and tag atomically.
- `publish` never runs `git_ops.release`; it validates the tag version against `mix.exs`.
- GitHub release creation happens only after Hex confirms the package version exists.
- `dry_run` and `hex_dry_run` never create a GitHub release.

## `jido-review.yml`

`jido-review.yml` is an advisory PR review lane. It is separate from CI so review
automation can evolve without destabilizing required checks.

### Stable Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `post_comment` | boolean | `true` | Post or update a sticky PR comment |
| `fail_on_error` | boolean | `false` | Fail when packet generation or comment publishing fails |
| `review_config_path` | string | `".github/jido-review.yml"` | Optional repo review configuration |
| `max_diff_lines` | number | `2000` | Maximum diff lines in the packet |
| `agent_review` | boolean | `false` | Enable external agent review command |
| `agent_review_command` | string | `""` | Command consuming `JIDO_REVIEW_JSON` and `JIDO_REVIEW_MARKDOWN` |

### Stable Behavior

- Runs only from `pull_request` callers; v1 does not use `pull_request_target`.
- Always writes a job summary and uploads `jido-review.md` / `jido-review.json`.
- Posts one sticky PR comment for same-repository PRs when permissions allow it.
- Fork PR comment failures are advisory unless `fail_on_error` is true.
- `agent_review` is an extension point and is disabled by default.

## Compatibility Rules

- Adding a new optional input is non-breaking.
- Renaming or removing a public workflow, input, secret, output, or required permission is breaking.
- Moving a read-only check from opt-in to default-on is breaking unless v4 docs already describe it as baseline.
- Moving behavior between CI, release, and review lanes is breaking when it changes required caller permissions or required checks.
