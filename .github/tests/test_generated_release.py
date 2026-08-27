#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


validator = load_module(
    "validate_generated_release", ROOT / ".github/scripts/validate_generated_release.py"
)


def command(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class GeneratedReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        command("git", "init", "--initial-branch=main", cwd=self.repo)
        command("git", "config", "user.name", "Fixture", cwd=self.repo)
        command("git", "config", "user.email", "fixture@example.com", cwd=self.repo)
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/scripts").mkdir(parents=True)
        (self.repo / ".github/workflows/ci.yml").write_text(
            "name: CI\non: workflow_dispatch\n", encoding="utf-8"
        )
        (self.repo / ".github/scripts/validate-generated-release.sh").write_text(
            "#!/bin/sh\nexit 1\n", encoding="utf-8"
        )
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (self.repo / "mix.exs").write_text('@version "1.0.0"\n', encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-m", "chore: parent", cwd=self.repo)
        self.parent = command("git", "rev-parse", "HEAD", cwd=self.repo)
        self.make_release("- Generated entry.\n")
        self.original_cwd = Path.cwd()
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)
        self.temporary.cleanup()

    def make_release(self, entry: str, *, extra: bool = False) -> None:
        command("git", "checkout", "-B", "release-fixture", self.parent, cwd=self.repo)
        (self.repo / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [1.1.0]\n\n{entry}", encoding="utf-8"
        )
        (self.repo / "mix.exs").write_text('@version "1.1.0"\n', encoding="utf-8")
        if extra:
            (self.repo / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        else:
            (self.repo / "unexpected.txt").unlink(missing_ok=True)
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-m", "chore: release 1.1.0", cwd=self.repo)
        self.release = command("git", "rev-parse", "HEAD", cwd=self.repo)

    def metadata(self, generated_command: str | None = None) -> dict[str, object]:
        allowed_paths = ["CHANGELOG.md", "mix.exs"]
        trusted_command = generated_command or self.caller_command
        required_checks_identity = hashlib.sha256(b"required-check-fixture").hexdigest()
        changed_files_identity = validator.canonical_sha256(allowed_paths)
        command_identity = hashlib.sha256(trusted_command.encode("utf-8")).hexdigest()
        policy_identity = validator.canonical_sha256(
            {
                "required_checks_sha256": required_checks_identity,
                "release_changed_files_sha256": changed_files_identity,
                "generated_release_command_sha256": command_identity,
            }
        )
        return {
            "schema": 1,
            "repository": "owner/repo",
            "ref": "refs/heads/release/gitops/1.1.0",
            "release_sha": self.release,
            "parent_sha": self.parent,
            "tree_sha": command("git", "rev-parse", "HEAD^{tree}", cwd=self.repo),
            "changelog_before_sha": command(
                "git", "rev-parse", f"{self.parent}:CHANGELOG.md", cwd=self.repo
            ),
            "changelog_after_sha": command(
                "git", "rev-parse", f"{self.release}:CHANGELOG.md", cwd=self.repo
            ),
            "validation_workflow": ".github/workflows/ci.yml",
            "validation_workflow_sha": command(
                "git",
                "rev-parse",
                f"{self.release}:.github/workflows/ci.yml",
                cwd=self.repo,
            ),
            "release_changed_files": allowed_paths,
            "release_changed_files_sha256": changed_files_identity,
            "generated_release_command_sha256": command_identity,
            "required_checks_sha256": required_checks_identity,
            "release_policy_sha256": policy_identity,
            "tag": "v1.1.0",
            "actor": "github-actions[bot]",
        }

    def environment(self) -> dict[str, str]:
        return {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/heads/release/gitops/1.1.0",
            "GITHUB_SHA": self.release,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_ACTOR": "github-actions[bot]",
        }

    @property
    def caller_command(self) -> str:
        return (
            'test "$(git diff --name-only "$RELEASE_PARENT_SHA" "$RELEASE_SHA" '
            '| paste -sd, -)" = "CHANGELOG.md,mix.exs" && '
            'test "$(git show "$RELEASE_SHA:CHANGELOG.md" | tail -n 1)" '
            '= "- Generated entry."'
        )

    def validate(self) -> None:
        metadata = self.metadata()
        with mock.patch.dict(os.environ, self.environment(), clear=False):
            validator.validate_environment(metadata)
            release_environment = validator.validate_repository(metadata)
            validator.run_caller_command(
                self.caller_command,
                release_environment,
                metadata["generated_release_command_sha256"],
            )

    def test_exact_generated_changelog_is_accepted(self) -> None:
        self.validate()

    def test_manual_generated_changelog_is_rejected_by_caller_command(self) -> None:
        self.make_release("- Hand-written entry.\n")
        with self.assertRaises(validator.ValidationError):
            self.validate()

    def test_extra_release_change_is_rejected_by_caller_command(self) -> None:
        self.make_release("- Generated entry.\n", extra=True)
        with self.assertRaises(validator.ValidationError):
            self.validate()

    def test_changed_validator_and_permissive_policy_fail_before_command_runs(self) -> None:
        marker = self.repo / "malicious-validator-ran"
        script = self.repo / ".github/scripts/validate-generated-release.sh"
        script.write_text(
            f"#!/bin/sh\ntouch {marker.as_posix()}\nexit 0\n", encoding="utf-8"
        )
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.1.0]\n\n- Generated entry.\n", encoding="utf-8"
        )
        (self.repo / "mix.exs").write_text('@version "1.1.0"\n', encoding="utf-8")
        (self.repo / "extra.txt").write_text("extra\n", encoding="utf-8")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "--amend", "--no-edit", cwd=self.repo)
        self.release = command("git", "rev-parse", "HEAD", cwd=self.repo)

        caller = ".github/scripts/validate-generated-release.sh"
        metadata = self.metadata(caller)
        permissive = [
            ".github/scripts/validate-generated-release.sh",
            "CHANGELOG.md",
            "extra.txt",
            "mix.exs",
        ]
        metadata["release_changed_files"] = permissive
        metadata["release_changed_files_sha256"] = hashlib.sha256(
            json.dumps(permissive, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        metadata["release_policy_sha256"] = validator.canonical_sha256(
            {
                "required_checks_sha256": metadata["required_checks_sha256"],
                "release_changed_files_sha256": metadata[
                    "release_changed_files_sha256"
                ],
                "generated_release_command_sha256": metadata[
                    "generated_release_command_sha256"
                ],
            }
        )
        with mock.patch.dict(os.environ, self.environment(), clear=False):
            validator.validate_environment(metadata)
            with self.assertRaises(validator.ValidationError):
                validator.validate_repository(metadata)
        self.assertFalse(marker.exists())

    def test_generated_command_identity_mismatch_fails_before_execution(self) -> None:
        marker = self.repo / "wrong-command-ran"
        wrong = f"touch {marker.as_posix()}"
        environment = validator.validate_repository(self.metadata())
        with self.assertRaises(validator.ValidationError):
            validator.run_caller_command(
                wrong,
                environment,
                self.metadata()["generated_release_command_sha256"],
            )
        self.assertFalse(marker.exists())

    def test_combined_policy_identity_cannot_change(self) -> None:
        metadata = self.metadata()
        metadata["required_checks_sha256"] = "0" * 64
        with self.assertRaises(validator.ValidationError):
            validator.validate_repository(metadata)

    def test_spoofed_actor_and_wrong_event_are_rejected(self) -> None:
        metadata = self.metadata()
        for key, value in (
            ("GITHUB_ACTOR", "maintainer"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/main"),
            ("GITHUB_SHA", self.parent),
            ("GITHUB_REPOSITORY", "other/repo"),
        ):
            environment = self.environment()
            environment[key] = value
            with self.subTest(key=key), mock.patch.dict(
                os.environ, environment, clear=False
            ):
                with self.assertRaises(validator.ValidationError):
                    validator.validate_environment(metadata)

    def test_tampered_git_metadata_is_rejected(self) -> None:
        for key in (
            "release_sha",
            "parent_sha",
            "tree_sha",
            "changelog_before_sha",
            "changelog_after_sha",
            "validation_workflow_sha",
        ):
            metadata = self.metadata()
            metadata[key] = "0" * 40
            with self.subTest(key=key), self.assertRaises(validator.ValidationError):
                validator.validate_repository(metadata)

    def test_duplicate_json_keys_and_unsupported_fields_are_rejected(self) -> None:
        metadata_json = json.dumps(self.metadata())
        duplicate = metadata_json[:-1] + ',"tag":"v9.9.9"}'
        with self.assertRaises(validator.ValidationError):
            validator.parse_metadata(duplicate)
        metadata = self.metadata()
        metadata["shell"] = "echo injected"
        with self.assertRaises(validator.ValidationError):
            validator.parse_metadata(json.dumps(metadata))

    def test_workflow_control_change_is_rejected_before_caller_code(self) -> None:
        command("git", "checkout", "-B", "workflow-change", self.parent, cwd=self.repo)
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.1.0]\n\n- Generated entry.\n", encoding="utf-8"
        )
        (self.repo / "mix.exs").write_text('@version "1.1.0"\n', encoding="utf-8")
        (self.repo / ".github/workflows/ci.yml").write_text(
            "name: Changed\non: workflow_dispatch\n", encoding="utf-8"
        )
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-m", "chore: release 1.1.0", cwd=self.repo)
        self.release = command("git", "rev-parse", "HEAD", cwd=self.repo)
        metadata = self.metadata()
        with self.assertRaises(validator.ValidationError):
            validator.validate_repository(metadata)


if __name__ == "__main__":
    unittest.main()
