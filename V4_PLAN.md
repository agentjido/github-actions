# V4 Plan: Jido Reusable GitHub Actions

## Purpose

V4 should make consumer repositories simpler while making this repository more opinionated,
more secure, and easier to evolve.

The target model is:

- consumer repos have one small CI workflow file
- consumer repos have one small release workflow file
- consumer repos can optionally add one small CLA workflow file later
- this repository owns the complexity through reusable workflows and internal helper actions

This plan assumes the Jido ecosystem continues to standardize on:

- GitHub Actions reusable workflows
- conventional commits
- `git_ops`-driven Hex releases
- semantic version tags for this repository (`v4`, `v4.x.y`)

## Design Goals

- Keep implementation repos easy to maintain.
- Provide a thorough Elixir CI baseline, not a minimal smoke test.
- Treat the workflows in this repo as a versioned platform with a stable public API.
- Make secure defaults the standard path.
- Support current Jido library repos first, while leaving room for Phoenix, Ash, and DB-backed repos.
- Prepare a clean future path for CLA signoff without forcing it into the initial v4 cut.
- Support CI-driven write-back workflows such as formatting, codegen, lockfile refreshes, or release
  metadata updates.

## Lessons From Ash

Ash gets one major thing right: consumer repositories stay thin.

`ash_ai` keeps its repo-level workflow very small and delegates to a shared reusable workflow in
`ash`. That pattern is worth copying. The useful takeaway is not "make one giant workflow"; it is
"publish a small public entrypoint and centralize CI policy."

For this repo, the main adaptation is:

- keep the public entrypoints small and stable
- allow multiple internal workflows and helper actions
- do not tell downstream repos to pin `@main`
- preserve this repo's SemVer tag contract for workflow consumers
- separate "validation" workflows from "write-back" workflows so permissions are explicit

## V4 Public Workflow API

V4 should publish three consumer-facing workflows.

### 1. `elixir-ci.yml`

This is the recommended default entrypoint for nearly every Jido repo.

It should orchestrate:

- policy checks
- code quality checks
- test matrix execution
- optional experimental compile lanes
- optional docs validation
- optional write-back automation when explicitly enabled

Consumer repos should usually only need:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/elixir-ci.yml@v4
    with:
      postgres: true
      db_setup_command: mix ecto.setup
```

This workflow must support two operating modes:

- validation-only mode
- validation plus write-back mode

### 2. `elixir-release.yml`

This stays separate from CI because release permissions, secrets, and approval controls are
different.

Consumer repos should usually only need:

```yaml
name: Release

on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  release:
    uses: agentjido/github-actions/.github/workflows/elixir-release.yml@v4
    secrets: inherit
```

### 3. `cla-check.yml`

This should be added in a later phase, not required for initial v4 adoption.

CLA handling has a different event and security model than normal CI, so it should remain a
separate workflow.

Consumer repos should eventually be able to use:

```yaml
name: CLA

on:
  issue_comment:
    types: [created]
  pull_request_target:
    types: [opened, synchronize, reopened, closed]

jobs:
  cla:
    uses: agentjido/github-actions/.github/workflows/cla-check.yml@v4
```

## Internal Architecture

Consumer simplicity should come from layering, not from putting all logic in one YAML file.

V4 should use this internal structure:

- public reusable workflows in `.github/workflows/`
- internal helper composite actions in `.github/actions/`
- advanced building-block workflows that the public wrapper calls

Recommended layout:

- `.github/workflows/elixir-ci.yml`
- `.github/workflows/elixir-quality.yml`
- `.github/workflows/elixir-test.yml`
- `.github/workflows/elixir-release.yml`
- `.github/workflows/cla-check.yml`
- `.github/workflows/elixir-writeback.yml`
- `.github/actions/setup-beam/`
- `.github/actions/bootstrap-mix/`
- `.github/actions/restore-mix-cache/`
- `.github/actions/install-deps/`
- `.github/actions/configure-hex/`
- `.github/actions/extract-version/`

Notes:

- composite actions should own repeated setup logic
- reusable workflows should own job topology, permissions, services, matrices, and summaries
- service containers should stay in workflows, not composite actions
- write-back logic should live in a dedicated reusable workflow or dedicated job block, not mixed
  invisibly into the normal read-only validation path

## CI Design

### Recommended CI Topology

`elixir-ci.yml` should orchestrate these jobs:

1. `policy`
2. `quality`
3. `test`
4. `experimental-compile` (optional)
5. `writeback` (optional)
6. `summary`

### `policy` Job

This job should run low-cost repository policy checks early.

Initial scope:

- changelog guard for PRs
- optional conventional commit validation
- optional future repo hygiene checks

Do not put CLA here. CLA should remain in its own workflow because it likely needs
`pull_request_target` or comment-based triggers and must not execute untrusted PR code.

### `quality` Job

This should be the best-practice Elixir quality lane for one primary toolchain.

Default tasks should be explicit and opinionated:

- `mix format --check-formatted`
- `mix compile --warnings-as-errors`
- `mix credo --strict`
- `mix dialyzer`
- `mix deps.unlock --check-unused`
- `mix hex.audit`
- optional `mix sobelow`
- optional `mix docs`
- optional `mix hex.publish --dry-run`

V4 should prefer explicit tasks over a single `mix quality` alias as the internal default.

Reasons:

- behavior is visible in workflow output
- checks can be enabled or disabled individually
- consumers do not need to maintain identical aliases
- v4 can enforce a consistent baseline across repos

Still support overrides where needed:

- `quality_command` for full replacement
- `docs_command`
- `sobelow_command`

### `test` Job

This should remain matrix-based and should be the main runtime validation path.

Defaults:

- matrix on OTP and Elixir versions
- `fail-fast: false`
- Linux runner
- optional PostgreSQL service container
- generic database setup hook before tests

Key inputs:

- `otp_versions`
- `elixir_versions`
- `mix_env`
- `test_command`
- `postgres`
- `postgres_image`
- `db_setup_command`
- `test_env`

V4 should switch to GitHub `services:` for PostgreSQL rather than manual `docker run`.

Reasons:

- simpler workflow code
- better integration with GitHub Actions networking
- less manual lifecycle management
- fewer opportunities for cleanup bugs

### `experimental-compile` Job

Keep this concept from v3.

It is useful for:

- previewing upcoming Elixir versions
- compile-only lanes for prerelease toolchains
- non-blocking signals during ecosystem transitions

It should remain:

- opt-in
- `continue-on-error: true`
- clearly labeled in job name and step summary

### `summary` Job

Add a final summary job that always runs and writes a concise result to `GITHUB_STEP_SUMMARY`.

It should collect:

- versions tested
- optional DB settings
- which optional checks were enabled
- release-readiness signals such as Hex dry run or docs check

### `writeback` Job

This is the key refinement for v4.

Some Jido repos need CI to modify repository contents. Common examples:

- formatting
- generated code refresh
- lockfile refresh
- documentation or cheatsheet regeneration
- release metadata updates

That should be a first-class workflow concern, not an ad hoc shell snippet hidden in the middle of
validation jobs.

Recommended write-back inputs:

- `writeback`
- `writeback_command`
- `writeback_paths`
- `writeback_commit_message`
- `writeback_branch_mode`

Recommended `writeback_branch_mode` values:

- `pull-request`
- `direct`

Default should be:

- `pull-request`

Reasons:

- works with protected `main`
- avoids bypass complexity
- keeps audit history clear
- lowers the risk of a bad automation mutating the default branch directly

Direct mode should exist only for repositories that deliberately choose it.

### Write-Back Operating Modes

V4 should explicitly support two repo governance modes.

#### Mode A: Protected Main, Bot PR Write-Back

This should be the default recommended mode.

Flow:

1. CI validates code.
2. Write-back job runs if enabled.
3. If there are changes, the workflow commits them to a bot branch.
4. The workflow opens or updates a pull request.
5. Normal branch protection merges that PR.

Advantages:

- no GitHub App required for bypass
- preserves protected `main`
- minimal legal and security surprise
- easiest path for open source maintenance

Tradeoff:

- one extra PR hop for automation-authored changes
- if the workflow uses the repository `GITHUB_TOKEN`, bot-authored pushes will not trigger normal
  follow-up workflow runs, so the pipeline must not depend on recursive `push` execution

#### Mode B: Protected Main, Direct Bot Push

This mode is only for repositories that truly require direct automation writes to the protected
branch.

If CI must push directly to protected `main`, then the workflow needs an actor that branch
protection or rulesets explicitly allow to bypass PR requirements or push restrictions.

Practically, that means:

- a GitHub App installed on the repository and granted write access
- that app added to the branch protection or ruleset bypass list

This is the recommended technical model if direct protected-branch writes are non-negotiable.

Tradeoff:

- more setup and legal/operational overhead than PR mode
- use this when protected-branch direct writes or follow-up workflow triggering requirements justify
  the extra complexity

#### Mode C: Unprotected Main, Direct Workflow Push

This is the simplest operationally, but the weakest model.

It removes the need for bot bypass configuration, but it also means:

- automation mistakes land immediately on default branch
- human mistakes land immediately on default branch
- this shared workflow repository becomes much riskier to operate

For this repository itself, this mode is not recommended.

For low-risk leaf repositories, it remains an intentional choice if maintainers prefer simplicity
over protection.

### Recommended Governance Choice

Refined recommendation:

- for this workflow repository: keep `main` protected
- for most consumer repositories: keep `main` protected and use PR-based write-back
- only introduce a custom GitHub App where direct writes to protected `main` are truly required
- only introduce a custom GitHub App by default for repos whose automation depends on post-push
  workflow execution or protected-branch direct writes
- do not make GitHub App setup a v4 baseline requirement for the whole ecosystem

### Optional Dependency Freshness Workflow

Elixir's library guidelines recommend validating both:

- deterministic builds using the checked-in `mix.lock`
- fresh dependency resolution against the latest allowed dependency versions

That should not be part of the default blocking PR path.

Recommended approach:

- keep the standard `elixir-ci.yml` deterministic
- add a later optional scheduled workflow for dependency freshness
- run `mix deps.unlock --all`, compile, and test on a schedule such as nightly or weekly

This can arrive as a later reusable workflow or as an opt-in mode once the core v4 surface is
stable.

## CI Best Practices To Standardize In V4

### Toolchain Setup

- Use `erlef/setup-beam`.
- Use exact OTP and Elixir inputs rather than mutating `.tool-versions` during CI.
- Keep a single helper action for setup so all workflows use the same behavior.

### Caching

- Separate beam tool cache from `deps` and `_build` cache.
- Key caches by OS, OTP, Elixir, `mix.lock`, and when needed `mix.exs`.
- Keep Dialyzer PLTs in a dedicated cache path.
- Continue removing stale rebar artifacts when cache reuse could poison builds.

### Permissions

- Default workflow permissions should be read-only.
- Elevate permissions per job only when necessary.
- Examples:
  - `contents: read` for normal CI
  - `security-events: write` only for SARIF uploads
  - `contents: write` only for write-back, release, or CLA signature storage
  - `pull-requests: write` only when the workflow opens or updates automation PRs

Validation jobs and write-back jobs should be permission-separated.

Do not give the entire CI workflow write access when only one job needs it.

### Third-Party Action Pinning

V4 implementation should pin third-party actions to full commit SHAs internally.

This is separate from how consumers pin this workflow repository.

Consumer guidance should remain:

- `@v4` for the floating compatibility line
- `@v4.x.y` for exact reproducibility

Internal implementation guidance should be:

- pin third-party actions and third-party reusable workflows to full SHAs
- only use moving refs internally when there is a strong reason and clear trust boundary

### Hex Audit Ordering

Hex documents that `mix hex.audit` should run before tasks that may load or start the application.

V4 should respect that where practical, which means `hex.audit` should run as an early dedicated
step instead of being tacked on after the rest of the quality lane.

### Timeouts and Concurrency

- set explicit `timeout-minutes` on all jobs
- use concurrency for repo-level caller workflows to cancel superseded PR runs
- do not cancel in-progress release jobs
- ensure write-back jobs use narrow concurrency groups so two runs do not race to mutate the same
  branch

## Release Design

### Release Philosophy

The release workflow should stay simple for consumers but strict internally.

It should assume:

- releases are deliberate
- releases happen from protected default branch state
- release secrets are protected
- release execution should be reproducible and observable

### Recommended Consumer Release Trigger

For Jido repos, keep `workflow_dispatch` as the standard release trigger.

Reasons:

- release remains an explicit operator action
- it is easier to use environment approvals
- it avoids automatic publish on tag creation
- it matches the current `git_ops` release model

### Release Job Shape

`elixir-release.yml` should perform:

1. checkout with full history and tags
2. git hook disablement
3. git bot configuration
4. toolchain and dependency bootstrap through shared helpers
5. optional database service setup if release preflight needs it
6. release dry run and no-op detection
7. preflight validation
8. actual release command
9. atomic push of commit and tag
10. Hex publish or Hex dry run
11. GitHub release creation
12. summary output

### Release Atomicity Invariant

This is a hard requirement for v4.

No package may be published to Hex unless the corresponding release commit and release tag have
already been pushed successfully to `origin`.

That means:

- push rejection must fail the workflow before Hex publish
- GitHub release creation must happen after the git push succeeds
- "local release succeeded but remote push failed" is an invalid terminal state

This requirement is the direct fix for the protected-branch failure mode captured in issue `#4`.

### Release Push Modes

Release write-back needs to be more explicit than generic CI write-back because publication is
irreversible once Hex accepts the package.

V4 should support a release-specific push mode input, for example:

- `release_push_mode: pull-request`
- `release_push_mode: direct`

Default should be:

- `release_push_mode: pull-request`

#### `release_push_mode: pull-request`

Recommended default for protected repositories.

Flow:

1. prepare the release commit and release tag candidate on a bot branch
2. open or update a release PR
3. merge that PR through normal protected-branch checks
4. run the final publish step only from protected `main` after the merge state is durable

This is the lowest-overhead protected-branch-safe mode for open source maintenance.

#### `release_push_mode: direct`

Use this only when the repository intentionally supports direct automation writes to the protected
default branch.

Requirements:

- the push actor must be allowed by branch protection or rulesets
- the workflow must use a credential that can perform that write
- the workflow must push the release commit and tag before Hex publish

In most cases this means using a GitHub App token or another explicitly authorized token rather
than assuming `github.token` can bypass branch protection.

### Release Preflight Standard

The default release preflight should be stronger than "just run `mix test`".

Recommended default:

- `mix hex.audit`
- `mix test`
- optional docs compilation
- optional package dry run before actual publish

Support overrides:

- `preflight_command`
- `db_setup_command`
- `skip_preflight`

### Protected Branch Operator Guidance

V4 docs should state this plainly for maintainers:

- `contents: write` on `github.token` does not by itself guarantee protected-branch push access
- if a repository requires pull requests or required checks on `main`, direct release pushes may be
  rejected unless the push actor is explicitly allowed
- repositories should choose one of two supported release models:
  - protected `main` with PR-based release preparation
  - protected `main` with an authorized bot actor for direct push

The workflow README should document this as a normal configuration choice, not as a special-case
troubleshooting note.

### Secrets and Environments

Release secrets should be stored in a protected GitHub Environment, not only at repository scope.

Recommended default environment name:

- `release`

Primary secret:

- `HEX_API_KEY`

Optional additional secret if needed:

- a GitHub App token or PAT only when `GITHUB_TOKEN` is insufficient

### GitHub Release Notes

V4 should support:

- changelog-based notes when available
- generated notes fallback

This should be an input, for example:

- `release_notes_mode: changelog`
- `release_notes_mode: generated`

### Hex Publishing

Hex will publish docs automatically when ExDoc is configured for the project.

That means the workflow should treat package release as the primary action and keep separate docs
site deployment outside of the core release workflow unless a repo explicitly needs it.

## Future CLA Signoff Design

### Why CLA Should Be Separate

CLA is not just another CI job.

It has a different trust model:

- it may need `pull_request_target`
- it may need comment-triggered rechecks
- it should not check out or run PR code
- it may need write access to PR statuses or signature storage

That is exactly why it should be isolated in `cla-check.yml`.

### Recommended V4.1 Direction

Use a provider-backed CLA workflow rather than inventing custom legal state logic in shell scripts.

Best initial candidate:

- `contributor-assistant/github-action`

Why:

- GitHub-native workflow integration
- comment-based signature flow
- supports CLA and DCO modes
- supports signature storage in a separate repository

### CLA Workflow Security Rules

- no checkout of contributor code
- no execution of project code
- minimal permissions by default
- only elevate `contents: write` if signatures are stored in a repository
- prefer storing signatures in a dedicated legal or signatures repository rather than code repos

### Recommended Org-Level Signature Storage

If CLA is adopted across multiple repos, use a dedicated repository for signatures.

For example:

- `agentjido/cla-signatures`

Benefits:

- avoids writing signature artifacts into product repos
- keeps protected default branches cleaner
- centralizes legal records
- makes repo-level CLA workflows almost identical

### CLA Rollout Recommendation

- Phase A: define CLA document and org policy
- Phase B: pilot `cla-check.yml` on one public repo
- Phase C: standardize on a shared signatures repository
- Phase D: add CLA workflow examples to this repo README

## Consumer Repository Experience

The standard consumer experience should be:

### Required files

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

### Optional later

- `.github/workflows/cla.yml`

That keeps each implementation repo extremely small while still allowing this repo to evolve the
platform behind the scenes.

For repos that need write-back, the default consumer experience should still stay small:

```yaml
jobs:
  ci:
    uses: agentjido/github-actions/.github/workflows/elixir-ci.yml@v4
    permissions:
      contents: write
      pull-requests: write
    with:
      writeback: true
      writeback_command: mix format && mix docs
      writeback_branch_mode: pull-request
```

For direct protected-branch writes, the workflow should document the extra repository setup
required:

- install the bot GitHub App
- add the app to the branch protection or ruleset bypass list
- grant only the minimum repository permissions needed

## Migration Strategy

### Phase 1: Internal Refactor

- add helper composite actions for setup, cache, deps, and version extraction
- keep current v3 behavior unchanged while building v4 in parallel

### Phase 2: Build V4 CI

- add `elixir-quality.yml`
- refactor `elixir-test.yml`
- add public `elixir-ci.yml` wrapper
- add explicit `writeback` job support
- document the new recommended consumer usage

### Phase 3: Build V4 Release

- refactor `elixir-release.yml` to share bootstrap logic
- add stronger preflight behavior
- add environment-aware secret guidance
- improve release notes handling
- add explicit `release_push_mode`
- enforce the release atomicity invariant
- document protected-branch-safe release setup

### Phase 4: Pilot Consumers

Pilot v4 in a small set of repos:

- one pure library repo
- one DB-backed repo
- one repo with the most release complexity

Suggested candidates:

- `jido_signal`
- `llm_db`
- `req_llm`

Pilot both governance models:

- one repo using protected `main` plus PR-based write-back
- one repo using direct write-back only if there is a real need to prove that path

### Phase 5: Cut `v4.0.0`

- freeze the v4 public inputs
- update README examples to use `@v4`
- tag `v4.0.0`
- move floating `v4` tag to the same release commit

### Phase 6: CLA Follow-Up

- implement `cla-check.yml`
- document CLA adoption
- tag `v4.1.0` or later once stable

## Acceptance Criteria

V4 is ready when all of the following are true:

- a consumer repo can adopt CI with a single `uses: .../elixir-ci.yml@v4`
- a consumer repo can adopt releases with a single `uses: .../elixir-release.yml@v4`
- internal workflow setup logic is no longer duplicated across files
- PostgreSQL support uses service containers
- third-party actions are pinned to SHAs internally
- release secrets can be protected by GitHub Environment approvals
- README and AGENTS document the public v4 contract
- at least two consumer repos run successfully on v4 before the `v4` tag moves
- write-back behavior is validated in at least one protected-branch repository before `v4` is cut
- release behavior is validated in at least one protected-branch repository before `v4` is cut
- Hex publish cannot occur after a rejected protected-branch push in the v4 release flow

## Non-Goals For Initial V4

- replacing `git_ops`
- adding self-hosted runner support
- bundling docs site deployment into the core release workflow
- building NIF cross-compilation and binary publishing features
- making CLA mandatory before the first v4 release

## Recommended Next Implementation Order

1. Build shared composite actions for setup and dependency bootstrap.
2. Refactor `elixir-test.yml` to use service containers and generic DB setup hooks.
3. Create `elixir-quality.yml` with explicit best-practice checks.
4. Add `elixir-writeback.yml` or equivalent dedicated write-back job design.
5. Create the public `elixir-ci.yml` wrapper.
6. Refactor `elixir-release.yml` around shared helpers and protected environment use.
7. Pilot v4 in two consumer repos.
8. Add `cla-check.yml` as a follow-on release.

## References

- GitHub reusable workflows:
  [docs.github.com/en/actions/how-tos/sharing-automations/reusing-workflows](https://docs.github.com/en/actions/how-tos/sharing-automations/reusing-workflows)
- GitHub reusable workflow behavior and permission model:
  [docs.github.com/en/actions/reference/workflows-and-actions/reusable-workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/reusable-workflows)
- GitHub `GITHUB_TOKEN` and least-privilege guidance:
  [docs.github.com/en/actions/configuring-and-managing-workflows/authenticating-with-the-github_token](https://docs.github.com/en/actions/configuring-and-managing-workflows/authenticating-with-the-github_token)
- GitHub Actions security hardening and SHA pinning:
  [docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- GitHub PostgreSQL service containers:
  [docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- Hex `mix hex.publish`:
  [hexdocs.pm/hex/Mix.Tasks.Hex.Publish.html](https://hexdocs.pm/hex/Mix.Tasks.Hex.Publish.html)
- Hex `mix hex.audit`:
  [hexdocs.pm/hex/Mix.Tasks.Hex.Audit.html](https://hexdocs.pm/hex/Mix.Tasks.Hex.Audit.html)
- Elixir library publishing guidance:
  [hexdocs.pm/elixir/library-guidelines.html](https://hexdocs.pm/elixir/library-guidelines.html)
- Contributor Assistant GitHub Action:
  [github.com/contributor-assistant/github-action](https://github.com/contributor-assistant/github-action)
