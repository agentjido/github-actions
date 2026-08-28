#!/usr/bin/env python3

from __future__ import annotations

import re
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE = (ROOT / ".github/workflows/jido-release.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/jido-ci.yml").read_text(encoding="utf-8")
QUALITY = (ROOT / ".github/workflows/elixir-quality.yml").read_text(encoding="utf-8")
STATE = (ROOT / ".github/scripts/release_state.py").read_text(encoding="utf-8")
VALIDATOR = (ROOT / ".github/scripts/validate_generated_release.py").read_text(
    encoding="utf-8"
)
BASE_RELEASE = subprocess.run(
    [
        "git",
        "show",
        "cc70e2a1dcf3066772d3f3368ae170a97e588bc6:.github/workflows/jido-release.yml",
    ],
    cwd=ROOT,
    check=True,
    text=True,
    stdout=subprocess.PIPE,
).stdout


def input_block(workflow: str, name: str) -> str:
    match = re.search(
        rf"^      {re.escape(name)}:\n(?P<body>(?:        .*\n)+)", workflow, re.MULTILINE
    )
    if not match:
        raise AssertionError(f"Missing workflow input: {name}")
    return match.group("body")


def step_block(workflow: str, name: str) -> str:
    match = re.search(
        rf"^      - name: {re.escape(name)}\n(?P<body>(?:        .*\n|\n)+?)(?=^      - name:|\Z)",
        workflow,
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"Missing workflow step: {name}")
    return match.group(0)


def job_block(workflow: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>(?:    .*\n|\n)+?)(?=^  [A-Za-z0-9_-]+:|\Z)",
        workflow,
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"Missing workflow job: {name}")
    return match.group(0)


def run_body(block: str) -> str:
    marker = "        run: |\n"
    if marker not in block:
        raise AssertionError("Step has no run body")
    return block.split(marker, 1)[1]


def executable_body(workflow: str, name: str, **replacements: str) -> str:
    body = textwrap.dedent(run_body(step_block(workflow, name)))
    for source, target in replacements.items():
        body = body.replace(source, target)
    return body


class WorkflowContractTest(unittest.TestCase):
    def test_checked_release_inputs_and_backward_compatible_default(self) -> None:
        expected = {
            "staged_prepare": ("boolean", "false"),
            "validation_workflow": ("string", '"ci.yml"'),
            "required_checks": ("string", '"[]"'),
            "release_changed_files": ("string", "'[\"CHANGELOG.md\",\"mix.exs\"]'"),
            "generated_release_command": (
                "string",
                '".github/scripts/validate-generated-release.sh"',
            ),
            "staging_branch_prefix": ("string", '"release/gitops/"'),
            "validation_timeout_seconds": ("number", "2700"),
        }
        for name, (input_type, default) in expected.items():
            with self.subTest(name=name):
                block = input_block(RELEASE, name)
                self.assertIn(f"type: {input_type}", block)
                self.assertIn(f"default: {default}", block)
        self.assertIn("inputs.staged_prepare != true", RELEASE)
        self.assertIn("git push --atomic --no-follow-tags origin", RELEASE)

    def test_release_permissions_are_minimal_and_have_required_reads(self) -> None:
        self.assertRegex(
            RELEASE,
            r"permissions:\n      actions: write\n      contents: write",
        )
        self.assertNotIn("\n      checks:", RELEASE)
        self.assertNotIn("pull-requests:", RELEASE)
        self.assertNotIn("secrets: inherit", RELEASE)

    def test_validation_inputs_exist_in_ci_and_quality(self) -> None:
        for workflow in (CI, QUALITY):
            for name in ("release_validation", "generated_release_command"):
                with self.subTest(workflow=workflow.splitlines()[0], name=name):
                    block = input_block(workflow, name)
                    self.assertIn("required: false", block)
                    self.assertIn("type: string", block)
                    self.assertIn('default: ""', block)

    def test_nested_ci_workflows_use_exact_remote_commits(self) -> None:
        commit = "410854ddd3173779056b274040bb8a58ca5ffb97"
        for workflow in ("elixir-quality.yml", "elixir-test.yml"):
            with self.subTest(workflow=workflow):
                self.assertIn(
                    f"uses: agentjido/github-actions/.github/workflows/{workflow}@{commit}",
                    CI,
                )
        self.assertNotIn("uses: ./.github/workflows/", CI)

    def test_validation_gate_uses_exact_sha_and_read_only_permissions(self) -> None:
        for workflow in (CI, QUALITY):
            self.assertIn("ref: ${{ github.sha }}", workflow)
            validation = job_block(workflow, "release-validation")
            self.assertIn("    permissions:\n      contents: read", validation)
            self.assertNotIn("actions:", validation)
            self.assertIn("validate_generated_release.py", workflow)
        self.assertIn("github-actions[bot]", VALIDATOR)
        self.assertIn('event_name != "workflow_dispatch"', VALIDATOR)
        self.assertIn("changed workflow control files", VALIDATOR)

    def test_ordinary_pull_request_changelog_policy_is_unchanged(self) -> None:
        self.assertIn(
            "if: ${{ inputs.changelog_guard && github.event_name == 'pull_request' }}",
            QUALITY,
        )
        self.assertIn(
            "CHANGELOG.md should not be edited manually because git_ops generates it",
            QUALITY,
        )

    def test_staging_and_promotion_refspec_contract(self) -> None:
        self.assertIn('f"{manifest[\'release_sha\']}:{branch_ref}"', STATE)
        self.assertIn('"--atomic"', STATE)
        self.assertIn('"--porcelain"', STATE)
        self.assertIn("[new branch]", STATE)
        self.assertIn("[up to date]", STATE)
        self.assertIn('f"{tag_ref}:{tag_ref}"', STATE)
        self.assertNotIn('f"+{manifest[\'release_sha\']}', STATE)
        self.assertIn("--force-with-lease={target_ref}", STATE)
        self.assertIn("--force-with-lease={branch_ref}", STATE)
        self.assertIn("--force-with-lease={tag_ref}:", STATE)
        self.assertIn('f":{branch_ref}"', STATE)
        self.assertIn("Promotion did not atomically remove", STATE)
        self.assertIn('"--no-follow-tags"', STATE)
        self.assertIn("Refusing to delete a staging branch", STATE)

    def test_dispatch_run_ids_and_tag_serialization_are_required(self) -> None:
        self.assertNotIn("return_run_details", STATE)
        self.assertIn('response.get("workflow_run_id")', STATE)
        self.assertIn("Do not retry it automatically", STATE)
        self.assertIn(
            "inputs.staged_prepare && format('release-{0}-{1}'", RELEASE
        )
        self.assertIn(
            "format('release-{0}-{1}-{2}', github.repository, github.ref, inputs.operation)",
            RELEASE,
        )
        self.assertIn("publish_dispatch_attempted", STATE)

    def test_no_untrusted_secret_flow_or_protection_mutation(self) -> None:
        self.assertNotIn("secrets: inherit", CI)
        self.assertNotIn("secrets: inherit", QUALITY)
        self.assertIn(
            "inputs.release_validation == '' && secrets.HEX_API_KEY || ''", CI
        )
        forbidden = (
            "pull-requests: write",
            "branches/main/protection --method PUT",
            "/protection/required_status_checks",
            "administration:",
            "admin",
            "--force refs/heads/main",
        )
        combined = RELEASE + STATE
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, combined)

    def test_local_state_and_precheck_order(self) -> None:
        trusted_ref = RELEASE.index("Validate trusted release ref before checkout")
        checkout = RELEASE.index("Checkout code")
        parent_precheck = RELEASE.index(
            "Refuse repeated checked release before planning"
        )
        plan = RELEASE.index("Plan checked release before generation")
        precheck = RELEASE.index("Refuse repeated checked release before generation")
        generate = RELEASE.index("Prepare release with git_ops")
        preserve = RELEASE.index("Preserve private checked-release state before staging")
        stage = RELEASE.index("Push only the owned staging branch")
        validate = RELEASE.index("Dispatch exact release validation")
        promote = RELEASE.index("Atomically promote the checked commit and annotated tag")
        publish = RELEASE.index("Dispatch publish once")
        self.assertLess(trusted_ref, checkout)
        self.assertLess(parent_precheck, plan)
        self.assertLess(plan, precheck)
        self.assertLess(precheck, generate)
        self.assertLess(preserve, stage)
        self.assertLess(stage, validate)
        self.assertLess(validate, promote)
        self.assertLess(promote, publish)

    def test_required_checks_are_bound_to_exact_run_attempt_jobs(self) -> None:
        self.assertIn("/attempts/{attempt}/jobs", STATE)
        self.assertNotIn("job_check_run", STATE)
        self.assertIn('job.get("run_attempt") != attempt', STATE)
        self.assertIn('"run_attempt": manifest["validation_run_attempt"]', STATE)
        self.assertNotIn(f"commits/{{sha}}/check-runs", STATE)
        self.assertGreaterEqual(STATE.count("validate_required_jobs(manifest"), 2)
        self.assertIn("required_checks_sha256", RELEASE)

    def test_allowlist_command_and_staging_ownership_are_bound(self) -> None:
        for value in (
            "release_changed_files_sha256",
            "generated_release_command_sha256",
            "staging_push_attempted",
            "staging_cleanup_owned",
            "staging_ownership_proof",
        ):
            self.assertIn(value, STATE)
        self.assertIn("validate_changed_file_policy", VALIDATOR)
        self.assertLess(
            VALIDATOR.index("validate_changed_file_policy(metadata"),
            VALIDATOR.index("def run_caller_command"),
        )
        self.assertIn("generated_release_command does not match", VALIDATOR)

    def test_default_branch_and_annotated_tag_gate_precedes_checkout(self) -> None:
        gate = step_block(RELEASE, "Validate trusted release ref before checkout")
        self.assertIn("inputs.staged_prepare == true", gate)
        self.assertIn("current_default_branch", gate)
        self.assertIn('repos/$GITHUB_REPOSITORY" --jq \'.default_branch\'', gate)
        self.assertIn('GITHUB_REF" != "refs/heads/$DEFAULT_BRANCH', gate)
        self.assertIn('select(.object.type == "tag")', gate)
        self.assertIn("compare/${release_sha}...${default_sha}", gate)
        self.assertLess(RELEASE.index(gate), RELEASE.index("Checkout code"))
        self.assertIn("git commit --allow-empty", RELEASE)
        self.assertIn('git tag "$release_tag"', RELEASE)
        publish_recheck = step_block(
            RELEASE, "Recheck publish tag and reachability before package write"
        )
        self.assertIn("steps.mode.outputs.operation == 'publish'", publish_recheck)
        self.assertIn("inputs.staged_prepare == true", publish_recheck)

    def test_direct_mode_keeps_base_publish_commands(self) -> None:
        names = (
            "Require Hex token for publish",
            "Publish to Hex.pm",
            "Check Hex package version",
            "Extract changelog notes",
            "Create or update GitHub release with changelog notes",
            "Create GitHub release with generated notes",
        )
        for name in names:
            with self.subTest(name=name):
                base = step_block(BASE_RELEASE, name)
                current = step_block(RELEASE, name)
                self.assertEqual(run_body(current), run_body(base))
                self.assertIn("inputs.staged_prepare != true", current)
        checked_publish = step_block(RELEASE, "Complete idempotent publish state")
        self.assertIn("inputs.staged_prepare == true", checked_publish)
        self.assertIn("mix hex.publish --yes", RELEASE)
        self.assertIn("gh release edit", RELEASE)

    def test_legacy_initial_release_tag_command_is_preserved(self) -> None:
        base_prepare = step_block(BASE_RELEASE, "Prepare release with git_ops")
        current_prepare = step_block(RELEASE, "Prepare release with git_ops")
        self.assertIn('git tag "$release_tag"', base_prepare)
        self.assertIn('git tag "$release_tag"', current_prepare)
        self.assertIn("git commit --allow-empty", current_prepare)

    def test_direct_initial_release_keeps_head_and_creates_lightweight_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            mix = fake_bin / "mix"
            mix.write_text("#!/bin/sh\nprintf '1.0.0\\n'\n", encoding="utf-8")
            mix.chmod(0o755)
            subprocess.run(
                ["git", "init", "--initial-branch=main"], cwd=root, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=root, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.com"],
                cwd=root,
                check=True,
            )
            (root / "mix.exs").write_text("version fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "mix.exs"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "chore: parent"], cwd=root, check=True)
            parent = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            body = executable_body(
                RELEASE,
                "Prepare release with git_ops",
                **{'${{ inputs.dry_run }}': "false"},
            )
            environment = os.environ.copy()
            environment.update(
                PATH=f"{fake_bin}:{environment['PATH']}",
                GITHUB_OUTPUT=(root / "output").as_posix(),
                MIX_ENV="dev",
                RELEASE_COMMAND="fake-generator",
                VERSION_OVERRIDE="",
                STAGED_PREPARE="false",
                PLANNED_TAG="",
                PLANNED_SKIPPED="",
                RELEASE_PARENT=parent,
            )
            subprocess.run(["bash", "-c", body], cwd=root, env=environment, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            tag_type = subprocess.run(
                ["git", "cat-file", "-t", "v1.0.0"],
                cwd=root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(head, parent)
            self.assertEqual(tag_type, "commit")

    def test_direct_feature_initial_prepare_dispatch_and_publish_stays_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            remote = root / "remote.git"
            fake_bin = root / "bin"
            work.mkdir()
            fake_bin.mkdir()
            subprocess.run(
                ["git", "init", "--initial-branch=feature-release"],
                cwd=work,
                check=True,
                stdout=subprocess.PIPE,
            )
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=work, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.com"],
                cwd=work,
                check=True,
            )
            (work / "mix.exs").write_text("version fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "mix.exs"], cwd=work, check=True)
            subprocess.run(
                ["git", "commit", "-m", "chore: parent"], cwd=work, check=True
            )
            subprocess.run(
                ["git", "remote", "add", "origin", remote.as_posix()],
                cwd=work,
                check=True,
            )

            command_log = root / "commands.log"
            mix = fake_bin / "mix"
            mix.write_text(
                "#!/bin/sh\n"
                "echo \"mix $*\" >> \"$COMMAND_LOG\"\n"
                "printf '1.0.0\\n'\n",
                encoding="utf-8",
            )
            mix.chmod(0o755)
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "echo \"gh $*\" >> \"$COMMAND_LOG\"\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                PATH=f"{fake_bin}:{environment['PATH']}",
                COMMAND_LOG=command_log.as_posix(),
                GITHUB_REPOSITORY="owner/repo",
            )

            prepare_mode_output = root / "prepare-mode"
            prepare_mode = executable_body(RELEASE, "Resolve release operation")
            subprocess.run(
                ["bash", "-c", prepare_mode],
                cwd=work,
                env={
                    **environment,
                    "GITHUB_OUTPUT": prepare_mode_output.as_posix(),
                    "GITHUB_REF": "refs/heads/feature-release",
                    "OPERATION_INPUT": "prepare",
                    "TAG_NAME_INPUT": "",
                    "DEFAULT_BRANCH": "main",
                    "STAGED_PREPARE": "false",
                },
                check=True,
            )
            self.assertIn(
                "target_branch=feature-release", prepare_mode_output.read_text()
            )

            parent = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=work,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            prepare = executable_body(
                RELEASE,
                "Prepare release with git_ops",
                **{"${{ inputs.dry_run }}": "false"},
            )
            subprocess.run(
                ["bash", "-c", prepare],
                cwd=work,
                env={
                    **environment,
                    "GITHUB_OUTPUT": (root / "prepare").as_posix(),
                    "MIX_ENV": "dev",
                    "RELEASE_COMMAND": "unused-initial-generator",
                    "VERSION_OVERRIDE": "",
                    "STAGED_PREPARE": "false",
                    "PLANNED_TAG": "",
                    "PLANNED_SKIPPED": "",
                    "RELEASE_PARENT": parent,
                },
                check=True,
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "cat-file", "-t", "v1.0.0"],
                    cwd=work,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip(),
                "commit",
            )

            push = executable_body(RELEASE, "Push prepared release commit and tag")
            subprocess.run(
                ["bash", "-c", push],
                cwd=work,
                env={
                    **environment,
                    "RELEASE_VERSION": "v1.0.0",
                    "TARGET_BRANCH": "feature-release",
                },
                check=True,
            )
            dispatch = executable_body(RELEASE, "Dispatch publish workflow")
            subprocess.run(
                ["bash", "-c", dispatch],
                cwd=work,
                env={
                    **environment,
                    "GH_TOKEN": "fixture",
                    "RELEASE_VERSION": "v1.0.0",
                    "TARGET_BRANCH": "feature-release",
                    "PUBLISH_WORKFLOW": "release.yml",
                },
                check=True,
            )
            self.assertIn(
                "--ref feature-release -f operation=publish -f tag_name=v1.0.0",
                command_log.read_text(),
            )

            publish_mode_output = root / "publish-mode"
            subprocess.run(
                ["bash", "-c", prepare_mode],
                cwd=work,
                env={
                    **environment,
                    "GITHUB_OUTPUT": publish_mode_output.as_posix(),
                    "GITHUB_REF": "refs/heads/feature-release",
                    "OPERATION_INPUT": "publish",
                    "TAG_NAME_INPUT": "v1.0.0",
                    "DEFAULT_BRANCH": "main",
                    "STAGED_PREPARE": "false",
                },
                check=True,
            )
            self.assertIn("target_branch=\n", publish_mode_output.read_text())
            trusted_gate = step_block(
                RELEASE, "Validate trusted release ref before checkout"
            )
            trusted_checkout = step_block(RELEASE, "Verify trusted release checkout")
            self.assertIn("inputs.staged_prepare == true", trusted_gate)
            self.assertIn("inputs.staged_prepare == true", trusted_checkout)

            checkout_tag = executable_body(RELEASE, "Checkout publish tag")
            subprocess.run(
                ["bash", "-c", checkout_tag],
                cwd=work,
                env={**environment, "TAG_NAME": "v1.0.0"},
                check=True,
            )
            validate_version = executable_body(
                RELEASE, "Validate publish tag matches package version"
            )
            subprocess.run(
                ["bash", "-c", validate_version],
                cwd=work,
                env={
                    **environment,
                    "GITHUB_OUTPUT": (root / "version").as_posix(),
                    "TAG_NAME": "v1.0.0",
                    "BARE_VERSION": "1.0.0",
                    "MIX_ENV": "dev",
                },
                check=True,
            )
            publish = executable_body(
                RELEASE,
                "Publish to Hex.pm",
                **{"${{ inputs.hex_dry_run }}": "false"},
            )
            subprocess.run(
                ["bash", "-c", publish],
                cwd=work,
                env={**environment, "HEX_API_KEY": "fixture", "MIX_ENV": "dev"},
                check=True,
            )
            self.assertIn("mix hex.publish --yes", command_log.read_text())

    def test_direct_publish_key_package_and_release_cases_execute_like_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "commands.log"
            output = root / "output"
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [1.2.3]\n\n- Notes.\n", encoding="utf-8"
            )
            mix = fake_bin / "mix"
            mix.write_text(
                "#!/bin/sh\n"
                "echo \"mix $*\" >> \"$COMMAND_LOG\"\n"
                "if [ \"$1 $2\" = \"hex.info fixture\" ]; then\n"
                "  if [ \"$HEX_PRESENT\" = true ]; then exit 0; else exit 1; fi\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            mix.chmod(0o755)
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "echo \"gh $*\" >> \"$COMMAND_LOG\"\n"
                "if [ \"$1 $2\" = \"release view\" ]; then\n"
                "  if [ \"$RELEASE_PRESENT\" = true ]; then exit 0; else exit 1; fi\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                PATH=f"{fake_bin}:{environment['PATH']}",
                COMMAND_LOG=log.as_posix(),
                GITHUB_OUTPUT=output.as_posix(),
                PACKAGE="fixture",
                BARE_VERSION="1.2.3",
                TAG_NAME="v1.2.3",
                MIX_ENV="dev",
            )

            require = executable_body(RELEASE, "Require Hex token for publish")
            denied = subprocess.run(
                ["bash", "-c", require],
                cwd=root,
                env={**environment, "HAS_HEX_API_KEY": "false"},
                check=False,
            )
            allowed = subprocess.run(
                ["bash", "-c", require],
                cwd=root,
                env={**environment, "HAS_HEX_API_KEY": "true"},
                check=False,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(allowed.returncode, 0)

            publish = executable_body(
                RELEASE,
                "Publish to Hex.pm",
                **{'${{ inputs.hex_dry_run }}': "false"},
            )
            subprocess.run(
                ["bash", "-c", publish],
                cwd=root,
                env={**environment, "HEX_API_KEY": "secret"},
                check=True,
            )
            self.assertIn("mix hex.publish --yes", log.read_text())

            check_hex = executable_body(RELEASE, "Check Hex package version")
            for package_present in (False, True):
                output.write_text("", encoding="utf-8")
                subprocess.run(
                    ["bash", "-c", check_hex],
                    cwd=root,
                    env={
                        **environment,
                        "HEX_PRESENT": str(package_present).lower(),
                    },
                    check=True,
                )
                self.assertIn(
                    f"on-hex={str(package_present).lower()}", output.read_text()
                )

            release = executable_body(
                RELEASE, "Create or update GitHub release with changelog notes"
            )
            (root / "release_notes.md").write_text("- Notes.\n", encoding="utf-8")
            for release_present, expected in ((True, "edit"), (False, "create")):
                log.write_text("", encoding="utf-8")
                subprocess.run(
                    ["bash", "-c", release],
                    cwd=root,
                    env={
                        **environment,
                        "RELEASE_PRESENT": str(release_present).lower(),
                    },
                    check=True,
                )
                self.assertIn(f"gh release {expected}", log.read_text())

    def test_checked_feature_and_lightweight_tag_stop_before_release_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "api.log"
            marker = root / "release-code-ran"
            default_sha = "1" * 40
            tag_sha = "2" * 40
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "echo \"$*\" >> \"$API_LOG\"\n"
                "case \"$*\" in\n"
                "  *\"repos/owner/repo --jq .default_branch\"*) printf 'main\\n' ;;\n"
                f"  *git/ref/heads/main*) printf '%s\\n' '{{\"object\":{{\"sha\":\"{default_sha}\"}}}}' ;;\n"
                f"  *git/ref/tags/v1.2.3*) printf '%s\\n' '{{\"object\":{{\"type\":\"commit\",\"sha\":\"{tag_sha}\"}}}}' ;;\n"
                "  *) exit 90 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            gate = executable_body(RELEASE, "Validate trusted release ref before checkout")
            guarded = gate + f"\ntouch {marker.as_posix()}\n"
            environment = os.environ.copy()
            environment.update(
                PATH=f"{fake_bin}:{environment['PATH']}",
                API_LOG=log.as_posix(),
                GITHUB_OUTPUT=(root / "output").as_posix(),
                GITHUB_REPOSITORY="owner/repo",
                DEFAULT_BRANCH="main",
            )

            feature = subprocess.run(
                ["bash", "-c", guarded],
                cwd=root,
                env={
                    **environment,
                    "RELEASE_OPERATION": "prepare",
                    "RELEASE_TAG": "",
                    "GITHUB_REF": "refs/heads/feature-release",
                    "GITHUB_SHA": tag_sha,
                },
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(feature.returncode, 0)
            self.assertFalse(marker.exists())

            log.write_text("", encoding="utf-8")
            lightweight = subprocess.run(
                ["bash", "-c", guarded],
                cwd=root,
                env={
                    **environment,
                    "RELEASE_OPERATION": "publish",
                    "RELEASE_TAG": "v1.2.3",
                    "GITHUB_REF": "refs/heads/main",
                    "GITHUB_SHA": default_sha,
                },
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(lightweight.returncode, 0)
            self.assertFalse(marker.exists())
            api_log = log.read_text()
            self.assertNotRegex(api_log, r"--method (POST|PUT|PATCH|DELETE)")
            self.assertNotIn("mix", api_log)


if __name__ == "__main__":
    unittest.main()
