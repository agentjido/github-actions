# V4 Implementation Plan

This document turns [V4_PLAN.md](./V4_PLAN.md) into an execution sequence.

It is intentionally practical:

- what to build
- in what order
- in which files
- how to verify each step
- what should block the next step

## Implementation Principles

- Keep `v3` stable while building `v4` in parallel.
- Ship small, reviewable changes instead of one large cutover.
- Validate each layer in this repo before piloting in consumer repos.
- Default to protected-branch-safe behavior.
- Treat release-path changes as higher risk than CI-path changes.

## Deliverables

V4.0.0 should ship with:

- `elixir-ci.yml`
- `elixir-quality.yml`
- `elixir-test.yml`
- `elixir-release.yml`
- internal helper composite actions under `.github/actions/`
- updated `README.md`
- updated `AGENTS.md`

V4.1.0 or later should ship with:

- `cla-check.yml`
- optional dependency freshness workflow

## Phase 0: Freeze Inputs And Decisions

Goal:

- settle the public API before writing implementation

Tasks:

1. Finalize the public workflow names:
   - `elixir-ci.yml`
   - `elixir-release.yml`
2. Finalize v4 public inputs for CI:
   - `otp_versions`
   - `elixir_versions`
   - `mix_env`
   - `postgres`
   - `postgres_image`
   - `db_setup_command`
   - `test_command`
   - `writeback`
   - `writeback_command`
   - `writeback_paths`
   - `writeback_commit_message`
   - `writeback_branch_mode`
   - optional `docs`, `sobelow`, `conventional_commits`, `hex_publish_dry_run`
3. Finalize v4 public inputs for release:
   - `otp_version`
   - `elixir_version`
   - `mix_env`
   - `release_command`
   - `version_override`
   - `preflight_command`
   - `db_setup_command`
   - `release_push_mode`
   - `dry_run`
   - `hex_dry_run`
   - `release_notes_mode`
4. Decide the exact default values for every input.
5. Decide the supported write-back modes for v4.0:
   - CI: `pull-request`, `direct`
   - Release: `pull-request`, `direct`

Files to update:

- [V4_PLAN.md](/Users/mhostetler/Source/OrigJido/github-actions/V4_PLAN.md:1)
- this file if needed

Exit criteria:

- no unresolved public API questions remain
- input names are stable enough to document in README examples

## Phase 1: Build Shared Helper Actions

Goal:

- remove repeated setup logic before refactoring workflows

Tasks:

1. Create `.github/actions/setup-beam/`.
   - inputs: OTP version, Elixir version, strict mode
   - behavior: install beam toolchain
2. Create `.github/actions/bootstrap-mix/`.
   - behavior: install Hex and Rebar safely
3. Create `.github/actions/restore-mix-cache/`.
   - behavior: restore beam tool cache plus `deps`/`_build` cache
4. Create `.github/actions/install-deps/`.
   - behavior: `mix deps.get`, optional `mix deps.compile`, cleanup stale rebar artifacts
5. Create `.github/actions/configure-hex/`.
   - behavior: shared Hex auth/setup where needed
6. Create `.github/actions/extract-version/`.
   - behavior: determine project version after release operations

Files to create:

- `.github/actions/setup-beam/action.yml`
- `.github/actions/bootstrap-mix/action.yml`
- `.github/actions/restore-mix-cache/action.yml`
- `.github/actions/install-deps/action.yml`
- `.github/actions/configure-hex/action.yml`
- `.github/actions/extract-version/action.yml`

Verification:

1. Add a temporary internal workflow or use local validation to ensure each action accepts inputs.
2. Confirm no helper action tries to manage service containers.
3. Confirm cache keys include OS, OTP, Elixir, and `mix.lock` where appropriate.

Exit criteria:

- current workflows can consume helper actions without behavior regression

## Phase 2: Refactor `elixir-test.yml`

Goal:

- make test execution the first clean v4 building block

Tasks:

1. Refactor `.github/workflows/elixir-test.yml` to use the helper actions.
2. Replace manual PostgreSQL `docker run` lifecycle with `services:`.
3. Keep matrix support for OTP and Elixir versions.
4. Keep experimental compile support.
5. Add generic DB setup hook:
   - `db_setup_command`
6. Add support for environment injection if needed:
   - `test_env`
7. Ensure the workflow is still reusable via `workflow_call`.

Files to update:

- [.github/workflows/elixir-test.yml](/Users/mhostetler/Source/OrigJido/github-actions/.github/workflows/elixir-test.yml:1)

Verification:

1. Run matrix validation on at least one no-DB consumer shape.
2. Run PostgreSQL validation on at least one DB-backed consumer shape.
3. Run experimental compile lane with at least one non-blocking toolchain combination.

Exit criteria:

- tests pass with helper actions
- Postgres works through `services:`
- no duplicated setup remains in the test workflow

## Phase 3: Build `elixir-quality.yml`

Goal:

- establish the v4 best-practice quality lane

Tasks:

1. Create `.github/workflows/elixir-quality.yml`.
2. Make it use helper actions for setup and bootstrap.
3. Run explicit tasks instead of a monolithic alias by default:
   - `mix hex.audit`
   - `mix format --check-formatted`
   - `mix compile --warnings-as-errors`
   - `mix credo --strict`
   - `mix dialyzer`
   - `mix deps.unlock --check-unused`
4. Add optional tasks via inputs:
   - docs
   - sobelow
   - Hex package dry run
   - conventional commit validation
5. Keep a changelog guard for PRs.
6. Add SARIF upload only if explicitly enabled and keep its permission narrow.

Files to create or update:

- `.github/workflows/elixir-quality.yml`

Verification:

1. Run quality workflow against a standard library-style consumer.
2. Confirm `hex.audit` runs early.
3. Confirm optional checks stay off unless enabled.
4. Confirm permission escalation is limited to the smallest job scope.

Exit criteria:

- quality workflow can replace the existing lint workflow behavior

## Phase 4: Build Write-Back Support

Goal:

- make repo mutation a first-class capability instead of custom ad hoc logic

Tasks:

1. Decide whether write-back lives:
   - directly inside `elixir-ci.yml`, or
   - in a dedicated `.github/workflows/elixir-writeback.yml`
2. Implement write-back inputs:
   - `writeback`
   - `writeback_command`
   - `writeback_paths`
   - `writeback_commit_message`
   - `writeback_branch_mode`
3. Implement `pull-request` mode:
   - create or update bot branch
   - commit only intended paths
   - open or update PR
4. Implement `direct` mode:
   - require explicit opt-in
   - fail clearly if the push credential cannot write
5. Add narrow concurrency so two write-back runs do not race.
6. Ensure job permissions are isolated:
   - `contents: write`
   - `pull-requests: write` only when needed

Files to create or update:

- `.github/workflows/elixir-writeback.yml` or `.github/workflows/elixir-ci.yml`

Verification:

1. Test PR-based write-back on a protected branch repo.
2. Confirm no unintended files are committed.
3. Confirm `GITHUB_TOKEN` recursion limitations are documented and do not break the expected flow.
4. If direct mode is implemented in v4.0, test failure behavior when push permission is absent.

Exit criteria:

- PR-based write-back works end to end
- direct mode is either proven or explicitly deferred

## Phase 5: Build Public `elixir-ci.yml`

Goal:

- expose a single simple CI entrypoint to consumer repos

Tasks:

1. Create `.github/workflows/elixir-ci.yml`.
2. Make it orchestrate:
   - policy
   - quality
   - test
   - experimental compile
   - optional write-back
   - summary
3. Keep the public input surface small.
4. Ensure the default mode is validation-only.
5. Ensure write-back remains explicit and opt-in.
6. Ensure job summaries clearly show:
   - versions
   - enabled checks
   - DB settings
   - write-back outcome

Files to create:

- `.github/workflows/elixir-ci.yml`

Verification:

1. Create a sample consumer workflow in a pilot repo with a single `uses:` job.
2. Confirm that standard repos need little or no repo-specific glue.
3. Confirm that protected-branch PR write-back works with the same public entrypoint.

Exit criteria:

- one small consumer `ci.yml` file is enough for normal adoption

## Phase 6: Refactor `elixir-release.yml`

Goal:

- make release behavior protected-branch-safe and atomic

Tasks:

1. Refactor `.github/workflows/elixir-release.yml` to use helper actions.
2. Add explicit release inputs:
   - `preflight_command`
   - `db_setup_command`
   - `release_push_mode`
   - `release_notes_mode`
3. Enforce the release atomicity invariant:
   - no Hex publish before successful push of commit and tag
4. Implement `release_push_mode: pull-request`.
   - prepare release changes on a branch
   - open or update PR
   - document the final publish handoff or follow-up publish trigger
5. Implement `release_push_mode: direct`.
   - require a credential that can actually push
   - fail before publish if protected-branch write is rejected
6. Add clear protected-branch documentation.
7. Keep dry-run and Hex dry-run behavior.
8. Keep no-op detection for no releasable commits.

Files to update:

- [.github/workflows/elixir-release.yml](/Users/mhostetler/Source/OrigJido/github-actions/.github/workflows/elixir-release.yml:1)

Verification:

1. Run dry-run on a standard library repo.
2. Prove the workflow cannot publish to Hex after a rejected push.
3. Test at least one protected-branch repo end to end.
4. Confirm release notes mode works for both changelog and generated notes.

Exit criteria:

- issue `#4` is concretely resolved by the implementation, not just by design

## Phase 7: Documentation Update

Goal:

- make adoption obvious for maintainers

Tasks:

1. Update `README.md` with new v4 examples.
2. Document recommended consumer setup:
   - standard CI
   - release
   - optional write-back
3. Document protected-branch guidance.
4. Document when a GitHub App is needed:
   - direct protected-branch writes
   - post-push workflow triggering requirements
5. Update `AGENTS.md` if maintainer release/version guidance changes.

Files to update:

- [README.md](/Users/mhostetler/Source/OrigJido/github-actions/README.md:1)
- [AGENTS.md](/Users/mhostetler/Source/OrigJido/github-actions/AGENTS.md:1)

Verification:

1. README examples are copy-pasteable.
2. All documented public inputs match actual workflow inputs.
3. Version guidance stays aligned with the tag contract.

Exit criteria:

- a maintainer can adopt v4 from docs alone

## Phase 8: Pilot Repositories

Goal:

- validate v4 in real consumers before tagging

Pilot set:

1. one pure library repo
2. one DB-backed repo
3. one repo that needs write-back
4. one repo that exercises release behavior on protected `main`

Suggested candidates:

- `jido_signal`
- `llm_db`
- `req_llm`
- `jido_action`

Tasks:

1. Add v4 CI to each pilot repo.
2. Record any missing inputs or assumptions.
3. Validate PR-based write-back in at least one pilot.
4. Validate protected-branch release behavior in at least one pilot.
5. Fix platform gaps in this repo, not in ad hoc consumer logic.

Verification:

1. Collect successful workflow links for each pilot.
2. Capture any deviations from the intended public API.

Exit criteria:

- at least two pilot repos succeed without consumer-side workaround logic
- protected-branch release behavior is proven

## Phase 9: Cut `v4.0.0`

Goal:

- publish the new workflow platform

Tasks:

1. Freeze public inputs.
2. Update README examples to use `@v4`.
3. Create annotated tag `v4.0.0`.
4. Push `v4.0.0`.
5. Move floating `v4` tag to the same release commit.
6. Announce migration guidance for consumer repos.

Verification:

1. tag exists and points to the intended release commit
2. `v4` points to the same commit
3. docs examples match shipped workflows

Exit criteria:

- `v4.0.0` and `v4` are published

## Phase 10: Post-Release Follow-Up

Goal:

- finish the adjacent features without blocking v4.0

Tasks:

1. Add `cla-check.yml`.
2. Add optional dependency freshness workflow.
3. Add more repo policy checks if needed.
4. Add migration notes from v3 to v4.

Files to create:

- `.github/workflows/cla-check.yml`
- optional dependency freshness workflow

Exit criteria:

- post-v4 features do not require breaking the v4 public contract

## Recommended PR Sequence

Keep the implementation sequence tight and reviewable.

1. PR 1: helper composite actions
2. PR 2: refactor `elixir-test.yml`
3. PR 3: add `elixir-quality.yml`
4. PR 4: add write-back support
5. PR 5: add public `elixir-ci.yml`
6. PR 6: refactor `elixir-release.yml`
7. PR 7: docs and examples
8. PR 8+: pilot-repo adoption PRs

## Stop Conditions

Stop and reassess if any of these happen:

- public inputs are still changing after pilots start
- release push modes are too ambiguous to document clearly
- PR-based write-back adds too much operational friction for maintainers
- direct protected-branch write mode requires more org-level setup than the ecosystem can support

## Minimum Bar Before Tagging `v4`

Do not cut `v4.0.0` until all of these are true:

- helper actions are in place
- public `elixir-ci.yml` exists and is stable
- `elixir-release.yml` enforces push-before-publish
- protected-branch release behavior is proven in a real repo
- PR-based write-back is proven in a real repo
- docs match shipped inputs
