#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


release_state = load_module(
    "release_state", ROOT / ".github/scripts/release_state.py"
)
RealGitHub = release_state.GitHub


def command(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class RepositoryFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.remote = self.root / "remote.git"
        self.state = self.root / "state"
        self.work.mkdir()
        command("git", "init", "--initial-branch=main", cwd=self.work)
        command("git", "config", "user.name", "Fixture", cwd=self.work)
        command("git", "config", "user.email", "fixture@example.com", cwd=self.work)
        (self.work / ".github/workflows").mkdir(parents=True)
        (self.work / ".github/workflows/ci.yml").write_text(
            "name: CI\non: workflow_dispatch\n", encoding="utf-8"
        )
        (self.work / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (self.work / "mix.exs").write_text(
            'defmodule Fixture.MixProject do\n  @version "1.0.0"\nend\n',
            encoding="utf-8",
        )
        command("git", "add", ".", cwd=self.work)
        command("git", "commit", "-m", "chore: parent", cwd=self.work)
        self.parent = command("git", "rev-parse", "HEAD", cwd=self.work)
        command("git", "init", "--bare", self.remote.as_posix())
        command(
            "git",
            "--git-dir",
            self.remote.as_posix(),
            "symbolic-ref",
            "HEAD",
            "refs/heads/main",
        )
        command("git", "remote", "add", "origin", self.remote.as_posix(), cwd=self.work)
        command("git", "push", "-u", "origin", "main", cwd=self.work)
        (self.work / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.1.0]\n\n- Generated entry.\n", encoding="utf-8"
        )
        (self.work / "mix.exs").write_text(
            'defmodule Fixture.MixProject do\n  @version "1.1.0"\nend\n',
            encoding="utf-8",
        )
        command("git", "add", "CHANGELOG.md", "mix.exs", cwd=self.work)
        command("git", "commit", "-m", "chore: release 1.1.0", cwd=self.work)
        self.release = command("git", "rev-parse", "HEAD", cwd=self.work)
        command("git", "tag", "-a", "v1.1.0", "-m", "v1.1.0", cwd=self.work)
        self.tag_object = command("git", "rev-parse", "refs/tags/v1.1.0", cwd=self.work)

    def close(self) -> None:
        self.temporary.cleanup()


class FakeGitHub:
    def __init__(self, fixture: RepositoryFixture) -> None:
        self.fixture = fixture
        self.dispatches: list[tuple[str, str, dict[str, object]]] = []
        self.artifact = False
        self.default = "main"
        self.job_reads = 0
        self.workflow_run = {
            "id": 101,
            "workflow_id": 55,
            "event": "workflow_dispatch",
            "head_sha": fixture.release,
            "head_branch": "release/gitops/1.1.0",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "repository": {"full_name": "owner/repo"},
            "actor": {"login": "github-actions[bot]"},
            "path": ".github/workflows/ci.yml@release/gitops/1.1.0",
        }
        self.jobs_response = [
            {
                "id": 501,
                "run_id": 101,
                "run_attempt": 1,
                "name": "CI",
                "head_sha": fixture.release,
                "status": "completed",
                "conclusion": "success",
            }
        ]

    def artifact_exists(self, _name: str) -> bool:
        return self.artifact

    def workflow(self, workflow: str) -> dict[str, object]:
        return {"id": 55, "path": f".github/workflows/{workflow}"}

    def default_branch(self) -> str:
        return self.default

    def dispatch(
        self, workflow: str, ref: str, inputs: dict[str, object]
    ) -> dict[str, object]:
        self.dispatches.append((workflow, ref, inputs))
        run_id = 100 + len(self.dispatches)
        return {"run_id": run_id, "run_url": f"https://example.test/runs/{run_id}"}

    def run(self, _run_id: int) -> dict[str, object]:
        return copy.deepcopy(self.workflow_run)

    def jobs(self, _run_id: int, _attempt: int) -> list[dict[str, object]]:
        self.job_reads += 1
        return copy.deepcopy(self.jobs_response)

class ReleaseStateTest(unittest.TestCase):
    owner_token = "a" * 64
    source_identity = "101:1:release"

    def setUp(self) -> None:
        self.fixture = RepositoryFixture()
        self.original_cwd = Path.cwd()
        os.chdir(self.fixture.work)
        self.github = FakeGitHub(self.fixture)
        self.github_patch = mock.patch.object(
            release_state, "GitHub", return_value=self.github
        )
        self.github_patch.start()
        self.token_patch = mock.patch.object(
            release_state.secrets, "token_hex", return_value=self.owner_token
        )
        self.token_patch.start()
        self.required_checks_identity: str | None = None
        self.release_policy_identity: str | None = None

    def tearDown(self) -> None:
        self.token_patch.stop()
        self.github_patch.stop()
        os.chdir(self.original_cwd)
        self.fixture.close()

    def prepare_args(self) -> SimpleNamespace:
        return SimpleNamespace(
            repository="owner/repo",
            target_branch="main",
            default_branch="main",
            staging_prefix="release/gitops/",
            tag="v1.1.0",
            parent=self.fixture.parent,
            required_checks='["CI"]',
            release_changed_files='["CHANGELOG.md","mix.exs"]',
            generated_release_command=".github/scripts/validate-generated-release.sh",
            source_identity=self.source_identity,
            validation_workflow="ci.yml",
            publish_workflow="release.yml",
            state_dir=self.fixture.state.as_posix(),
            remote="origin",
        )

    @property
    def manifest(self) -> Path:
        return self.fixture.state / "manifest.json"

    def prepare(self) -> None:
        release_state.cmd_prepare(self.prepare_args())
        manifest = release_state.read_manifest(self.manifest)
        self.required_checks_identity = manifest["required_checks_sha256"]
        self.release_policy_identity = manifest["release_policy_sha256"]

    def state_args(self, *, owner: bool = False, timeout: int | None = None) -> SimpleNamespace:
        assert self.required_checks_identity is not None
        assert self.release_policy_identity is not None
        values: dict[str, object] = {
            "manifest": self.manifest.as_posix(),
            "required_checks_sha256": self.required_checks_identity,
            "release_policy_sha256": self.release_policy_identity,
        }
        if owner:
            values.update(
                staging_owner_token=self.owner_token,
                source_identity=self.source_identity,
            )
        if timeout is not None:
            values["timeout"] = timeout
        return SimpleNamespace(**values)

    def stage(self) -> None:
        release_state.cmd_stage(self.state_args(owner=True))

    def dispatch_and_validate(self) -> None:
        release_state.cmd_dispatch_validation(
            self.state_args()
        )
        release_state.cmd_wait_validation(self.state_args(timeout=60))

    def remote_ref(self, ref: str) -> str | None:
        result = subprocess.run(
            ["git", "--git-dir", self.fixture.remote.as_posix(), "rev-parse", "--verify", ref],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def test_exact_staging_atomic_promotion_and_dispatch(self) -> None:
        self.prepare()
        self.assertTrue((self.fixture.state / "release.bundle").is_file())
        self.stage()
        manifest = release_state.read_manifest(self.manifest)
        self.assertTrue(manifest["staging_push_attempted"])
        self.assertTrue(manifest["staging_cleanup_owned"])
        self.assertEqual(manifest["tag_target_type"], "commit")
        self.assertEqual(manifest["tag_target_sha"], self.fixture.release)
        self.assertEqual(manifest["tag_internal_name"], "v1.1.0")
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.parent)
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))

        command(
            "git",
            "branch",
            "other-owned-proof",
            self.fixture.parent,
            cwd=self.fixture.work,
        )
        command(
            "git",
            "push",
            "origin",
            "other-owned-proof:refs/heads/other-owned-proof",
            cwd=self.fixture.work,
        )

        self.dispatch_and_validate()
        release_state.cmd_promote(self.state_args())
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.release)
        self.assertEqual(self.remote_ref("refs/tags/v1.1.0"), self.fixture.tag_object)
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))
        self.assertEqual(self.remote_ref("refs/heads/other-owned-proof"), self.fixture.parent)
        self.assertEqual(self.github.job_reads, 2)
        peeled = command(
            "git",
            "--git-dir",
            self.fixture.remote.as_posix(),
            "rev-parse",
            "refs/tags/v1.1.0^{}",
        )
        self.assertEqual(peeled, self.fixture.release)

        release_state.cmd_dispatch_publish(self.state_args())
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_dispatch_publish(self.state_args())
        self.assertEqual(len(self.github.dispatches), 2)
        publish_inputs = self.github.dispatches[1][2]
        self.assertEqual(publish_inputs["tag_name"], "v1.1.0")
        self.assertEqual(publish_inputs["staged_prepare"], "true")

    def test_every_unsuccessful_check_state_is_rejected(self) -> None:
        self.prepare()
        self.stage()
        release_state.cmd_dispatch_validation(self.state_args())
        manifest = release_state.read_manifest(self.manifest)
        conclusions = [
            None,
            "action_required",
            "cancelled",
            "failure",
            "neutral",
            "skipped",
            "stale",
            "startup_failure",
            "timed_out",
        ]
        for conclusion in conclusions:
            with self.subTest(conclusion=conclusion):
                self.github.jobs_response[0]["status"] = (
                    "completed" if conclusion is not None else "in_progress"
                )
                self.github.jobs_response[0]["conclusion"] = conclusion
                with self.assertRaises(release_state.ReleaseError):
                    release_state.validate_required_jobs(
                        manifest, self.github, self.github.workflow_run
                    )
        self.github.jobs_response = []
        with self.assertRaises(release_state.ReleaseError):
            release_state.validate_required_jobs(
                manifest, self.github, self.github.workflow_run
            )

    def test_full_run_failure_is_rejected(self) -> None:
        self.prepare()
        manifest = release_state.read_manifest(self.manifest)
        manifest["validation_run_id"] = 101
        manifest["validation_run_attempt"] = 1
        failed = copy.deepcopy(self.github.workflow_run)
        failed["conclusion"] = "failure"
        with self.assertRaises(release_state.ReleaseError):
            release_state.validate_run(manifest, failed)

    def test_stale_main_blocks_promotion_and_cleanup_removes_owned_branch(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()
        clone = self.fixture.root / "other"
        command("git", "clone", self.fixture.remote.as_posix(), clone.as_posix())
        command("git", "config", "user.name", "Other", cwd=clone)
        command("git", "config", "user.email", "other@example.com", cwd=clone)
        (clone / "other.txt").write_text("main moved\n", encoding="utf-8")
        command("git", "add", "other.txt", cwd=clone)
        command("git", "commit", "-m", "chore: move main", cwd=clone)
        command("git", "push", "origin", "main", cwd=clone)
        moved_main = command("git", "rev-parse", "HEAD", cwd=clone)
        with mock.patch.object(release_state.time, "sleep") as sleep, self.assertRaises(
            release_state.ReleaseError
        ):
            release_state.cmd_promote(self.state_args())
        sleep.assert_not_called()
        self.assertEqual(self.remote_ref("refs/heads/main"), moved_main)
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))
        release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))

    def test_changed_staging_branch_cleanup_is_refused(self) -> None:
        self.prepare()
        self.stage()
        (self.fixture.work / "after.txt").write_text("changed\n", encoding="utf-8")
        command("git", "add", "after.txt", cwd=self.fixture.work)
        command("git", "commit", "-m", "chore: change staged branch", cwd=self.fixture.work)
        changed = command("git", "rev-parse", "HEAD", cwd=self.fixture.work)
        command(
            "git",
            "push",
            "origin",
            "HEAD:refs/heads/release/gitops/1.1.0",
            cwd=self.fixture.work,
        )
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), changed
        )

    def test_atomic_rejection_rolls_back_main_and_tag(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()
        hook = self.fixture.remote / "hooks/pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "while read old new ref; do\n"
            "  case \"$ref\" in refs/tags/*) exit 1 ;; esac\n"
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        with mock.patch.object(release_state.time, "sleep") as sleep, self.assertRaises(
            release_state.ReleaseError
        ):
            release_state.cmd_promote(self.state_args())
        sleep.assert_not_called()
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.parent)
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )

    def test_expected_required_check_rejection_is_revalidated_and_retried(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()
        marker = self.fixture.root / "reject-promotion-once"
        hook = self.fixture.remote / "hooks/pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            f"if test ! -e {marker.as_posix()}; then\n"
            f"  touch {marker.as_posix()}\n"
            "  echo 'GH006: Protected branch update failed for refs/heads/main.' >&2\n"
            "  echo 'Required status check \"CI\" is expected.' >&2\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        with mock.patch.object(release_state.time, "sleep") as sleep:
            release_state.cmd_promote(self.state_args())

        sleep.assert_called_once_with(release_state.PROMOTION_RETRY_SECONDS)
        self.assertEqual(self.github.job_reads, 3)
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.release)
        self.assertEqual(self.remote_ref("refs/tags/v1.1.0"), self.fixture.tag_object)
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))

    def test_changed_staging_branch_is_not_deleted_after_promotion(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()
        (self.fixture.work / "cleanup-race.txt").write_text(
            "changed after promotion\n", encoding="utf-8"
        )
        command("git", "add", "cleanup-race.txt", cwd=self.fixture.work)
        command("git", "commit", "-m", "chore: race cleanup", cwd=self.fixture.work)
        changed = command("git", "rev-parse", "HEAD", cwd=self.fixture.work)
        command(
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{changed}:refs/heads/cleanup-race-candidate",
            cwd=self.fixture.work,
        )
        command("git", "checkout", "--detach", self.fixture.release, cwd=self.fixture.work)

        real_run = release_state.run
        branch_ref = "refs/heads/release/gitops/1.1.0"

        def race_cleanup(arguments: list[str], **kwargs: object):
            if (
                arguments[:2] == ["git", "push"]
                and "--atomic" not in arguments
                and f":{branch_ref}" in arguments
            ):
                command(
                    "git",
                    "--git-dir",
                    self.fixture.remote.as_posix(),
                    "update-ref",
                    branch_ref,
                    changed,
                    self.fixture.parent,
                )
            return real_run(arguments, **kwargs)

        stderr = io.StringIO()
        with mock.patch.object(release_state, "run", side_effect=race_cleanup), mock.patch(
            "sys.stderr", stderr
        ):
            release_state.cmd_promote(self.state_args())

        manifest = release_state.read_manifest(self.manifest)
        self.assertTrue(manifest["promoted"])
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.release)
        self.assertEqual(self.remote_ref("refs/tags/v1.1.0"), self.fixture.tag_object)
        self.assertEqual(self.remote_ref(branch_ref), changed)
        self.assertIn("exact-leased staging cleanup was not confirmed", stderr.getvalue())

    def test_repeated_prepare_refuses_existing_branch_or_artifact(self) -> None:
        self.prepare()
        self.stage()
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_prepare(self.prepare_args())

        command(
            "git",
            "push",
            "--delete",
            "origin",
            "release/gitops/1.1.0",
            cwd=self.fixture.work,
        )
        self.github.artifact = True
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_prepare(self.prepare_args())

    def test_required_checks_are_revalidated_before_promotion(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()
        self.github.jobs_response[0]["conclusion"] = "failure"
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_promote(self.state_args())
        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.parent)
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))

    def test_stage_never_follows_annotated_tags_from_git_config(self) -> None:
        self.prepare()
        command("git", "config", "push.followTags", "true", cwd=self.fixture.work)
        self.stage()
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))

    def test_required_check_policy_identity_cannot_change_during_run(self) -> None:
        self.prepare()
        manifest = release_state.read_manifest(self.manifest)
        manifest["required_checks"] = []
        manifest["required_checks_sha256"] = release_state.required_checks_sha256([])
        release_state.write_manifest(self.manifest, manifest)
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_stage(self.state_args(owner=True))
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))

    def test_non_default_branch_is_refused_before_staging(self) -> None:
        arguments = self.prepare_args()
        arguments.target_branch = "feature-release"
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_prepare(arguments)
        self.assertFalse(self.manifest.exists())
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))

    def test_same_name_job_from_another_run_cannot_satisfy_policy(self) -> None:
        self.prepare()
        self.stage()
        release_state.cmd_dispatch_validation(self.state_args())
        manifest = release_state.read_manifest(self.manifest)
        self.github.jobs_response[0]["run_id"] = 999
        with self.assertRaises(release_state.ReleaseError):
            release_state.validate_required_jobs(
                manifest, self.github, self.github.workflow_run
            )
        self.assertEqual(self.github.job_reads, 1)

    def test_rerun_attempt_is_not_accepted_as_the_dispatched_attempt(self) -> None:
        self.prepare()
        self.stage()
        release_state.cmd_dispatch_validation(self.state_args())
        self.github.workflow_run["run_attempt"] = 2
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_wait_validation(self.state_args(timeout=60))

    def test_preexisting_same_sha_branch_is_not_owned_or_cleaned(self) -> None:
        self.prepare()
        command(
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{self.fixture.release}:refs/heads/release/gitops/1.1.0",
            cwd=self.fixture.work,
        )
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_stage(self.state_args(owner=True))
        manifest = release_state.read_manifest(self.manifest)
        self.assertFalse(manifest["staging_push_attempted"])
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )

    def test_accepted_push_with_lost_response_is_retained_as_unowned(self) -> None:
        self.prepare()
        real_run = release_state.run

        def uncertain_run(arguments, **kwargs):
            if arguments[:3] == ["git", "push", "--atomic"]:
                result = real_run(arguments, check=False)
                self.assertEqual(result.returncode, 0)
                raise release_state.ReleaseError("client lost the accepted response")
            return real_run(arguments, **kwargs)

        with mock.patch.object(release_state, "run", side_effect=uncertain_run):
            with self.assertRaises(release_state.ReleaseError):
                release_state.cmd_stage(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )
        manifest = release_state.read_manifest(self.manifest)
        self.assertTrue(manifest["staging_push_attempted"])
        self.assertFalse(manifest["staging_cleanup_owned"])
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )

    def test_same_sha_race_between_precheck_and_push_is_not_owned(self) -> None:
        self.prepare()
        branch_ref = "refs/heads/release/gitops/1.1.0"
        real_remote_ref = release_state.remote_ref
        raced = False

        def racing_remote_ref(remote: str, ref: str) -> str | None:
            nonlocal raced
            result = real_remote_ref(remote, ref)
            if ref == branch_ref and not raced:
                self.assertIsNone(result)
                raced = True
                command(
                    "git",
                    "push",
                    "--no-follow-tags",
                    "origin",
                    f"{self.fixture.release}:{branch_ref}",
                    cwd=self.fixture.work,
                )
                return None
            return result

        with mock.patch.object(
            release_state, "remote_ref", side_effect=racing_remote_ref
        ):
            with self.assertRaises(release_state.ReleaseError):
                release_state.cmd_stage(self.state_args(owner=True))

        manifest = release_state.read_manifest(self.manifest)
        self.assertTrue(manifest["staging_push_attempted"])
        self.assertFalse(manifest["staging_cleanup_owned"])
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(self.remote_ref(branch_ref), self.fixture.release)

    def test_tampered_staging_ownership_record_cannot_authorize_cleanup(self) -> None:
        self.prepare()
        command(
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{self.fixture.release}:refs/heads/release/gitops/1.1.0",
            cwd=self.fixture.work,
        )
        manifest = release_state.read_manifest(self.manifest)
        manifest["staging_push_attempted"] = True
        manifest["staging_cleanup_owned"] = True
        manifest["staging_ownership_proof"] = "0" * 64
        release_state.write_manifest(self.manifest, manifest)
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), self.fixture.release
        )

    def test_server_boundary_same_sha_stage_race_is_not_owned(self) -> None:
        self.prepare()
        branch_ref = "refs/heads/release/gitops/1.1.0"
        command(
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{self.fixture.release}:refs/heads/race-object",
            cwd=self.fixture.work,
        )
        command(
            "git",
            "push",
            "--delete",
            "origin",
            "race-object",
            cwd=self.fixture.work,
        )

        wrapper = self.fixture.root / "receive-pack-stage-race.py"
        wrapper.write_text(
            """#!/usr/bin/env python3
import os
import subprocess
import sys
import threading

repo = sys.argv[1]
child = subprocess.Popen(
    ["git-receive-pack", repo],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
assert child.stdin is not None and child.stdout is not None

def packet(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise SystemExit(91)
    length = int(header, 16)
    body = stream.read(length - 4) if length else b""
    return header + body, length

while True:
    frame, length = packet(child.stdout)
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    if length == 0:
        break

subprocess.run(
    [
        "git", "--git-dir", repo, "update-ref",
        "refs/heads/release/gitops/1.1.0",
        os.environ["B_RACE_SHA"],
        "0" * 40,
    ],
    check=True,
)

def client_to_server():
    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            break
        child.stdin.write(chunk)
        child.stdin.flush()
    child.stdin.close()

thread = threading.Thread(target=client_to_server)
thread.start()
while True:
    chunk = child.stdout.read(65536)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
thread.join()
raise SystemExit(child.wait())
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        command(
            "git",
            "config",
            "remote.origin.receivepack",
            wrapper.as_posix(),
            cwd=self.fixture.work,
        )
        trace = self.fixture.root / "stage-packet.trace"
        with mock.patch.dict(
            os.environ,
            {
                "B_RACE_SHA": self.fixture.release,
                "GIT_TRACE_PACKET": trace.as_posix(),
            },
            clear=False,
        ):
            with self.assertRaises(release_state.ReleaseError):
                release_state.cmd_stage(self.state_args(owner=True))

        manifest = release_state.read_manifest(self.manifest)
        self.assertTrue(manifest["staging_push_attempted"])
        self.assertFalse(manifest["staging_cleanup_owned"])
        self.assertEqual(self.remote_ref(branch_ref), self.fixture.release)
        self.assertIn(branch_ref, trace.read_text())
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(self.remote_ref(branch_ref), self.fixture.release)

    def test_server_boundary_branch_race_rejects_atomic_promotion(self) -> None:
        self.prepare()
        self.stage()
        self.dispatch_and_validate()

        (self.fixture.work / "race.txt").write_text("changed branch\n", encoding="utf-8")
        command("git", "add", "race.txt", cwd=self.fixture.work)
        command("git", "commit", "-m", "chore: race staging branch", cwd=self.fixture.work)
        changed = command("git", "rev-parse", "HEAD", cwd=self.fixture.work)
        command(
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{changed}:refs/heads/race-candidate",
            cwd=self.fixture.work,
        )
        command("git", "checkout", "--detach", self.fixture.release, cwd=self.fixture.work)

        wrapper = self.fixture.root / "receive-pack-race.py"
        wrapper.write_text(
            """#!/usr/bin/env python3
import os
import subprocess
import sys
import threading

repo = sys.argv[1]
child = subprocess.Popen(
    [\"git-receive-pack\", repo],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
assert child.stdin is not None and child.stdout is not None

def packet(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise SystemExit(91)
    length = int(header, 16)
    body = stream.read(length - 4) if length else b\"\"
    return header + body, length

while True:
    frame, length = packet(child.stdout)
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    if length == 0:
        break

commands = []
while True:
    frame, length = packet(sys.stdin.buffer)
    commands.append(frame)
    if length == 0:
        break

subprocess.run(
    [
        \"git\", \"--git-dir\", repo, \"update-ref\",
        \"refs/heads/release/gitops/1.1.0\",
        os.environ[\"B_RACE_SHA\"],
        os.environ[\"B_EXPECTED_SHA\"],
    ],
    check=True,
)
for frame in commands:
    child.stdin.write(frame)
child.stdin.flush()

def client_to_server():
    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            break
        child.stdin.write(chunk)
        child.stdin.flush()
    child.stdin.close()

thread = threading.Thread(target=client_to_server)
thread.start()
while True:
    chunk = child.stdout.read(65536)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
thread.join()
raise SystemExit(child.wait())
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        command(
            "git",
            "config",
            "remote.origin.receivepack",
            wrapper.as_posix(),
            cwd=self.fixture.work,
        )
        trace = self.fixture.root / "packet.trace"
        environment = {
            "B_RACE_SHA": changed,
            "B_EXPECTED_SHA": self.fixture.release,
            "GIT_TRACE_PACKET": trace.as_posix(),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(release_state.ReleaseError):
                release_state.cmd_promote(self.state_args())

        self.assertEqual(self.remote_ref("refs/heads/main"), self.fixture.parent)
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), changed
        )
        self.assertIn("refs/heads/release/gitops/1.1.0", trace.read_text())
        self.assertIn(self.fixture.release, trace.read_text())
        self.assertIn(self.fixture.parent, trace.read_text())
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_cleanup_before_promotion(self.state_args(owner=True))
        self.assertEqual(
            self.remote_ref("refs/heads/release/gitops/1.1.0"), changed
        )

    def test_hard_loss_repeat_is_refused_before_fake_generator_runs(self) -> None:
        self.prepare()
        self.stage()
        fresh = self.fixture.root / "fresh-runner"
        command("git", "clone", self.fixture.remote.as_posix(), fresh.as_posix())
        marker = self.fixture.root / "generator-ran"
        fake_bin = self.fixture.root / "fake-bin"
        fake_bin.mkdir()
        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            '#!/bin/sh\nprintf \'{"default_branch":"main"}\\n\'\n', encoding="utf-8"
        )
        fake_gh.chmod(0o755)
        generator = self.fixture.root / "fake-generator"
        generator.write_text(
            f"#!/bin/sh\ntouch {marker.as_posix()}\n", encoding="utf-8"
        )
        generator.chmod(0o755)
        workflow = self.fixture.root / "checked-flow"
        workflow.write_text(
            "#!/bin/sh\n"
            f"python3 {ROOT / '.github/scripts/release_state.py'} precheck-parent "
            "--repository owner/repo --target-branch main --default-branch main "
            "--staging-prefix release/gitops/ "
            f"--parent {self.fixture.parent} --remote origin && {generator}\n",
            encoding="utf-8",
        )
        workflow.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        result = subprocess.run(
            [workflow.as_posix()],
            cwd=fresh,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_parent_precheck_refuses_durable_state_before_planning(self) -> None:
        self.github.artifact = True
        arguments = SimpleNamespace(
            repository="owner/repo",
            target_branch="main",
            default_branch="main",
            staging_prefix="release/gitops/",
            parent=self.fixture.parent,
            remote="origin",
        )
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_precheck_parent(arguments)

    def test_initial_checked_release_records_one_empty_commit_and_annotated_tag(self) -> None:
        command("git", "tag", "-d", "v1.1.0", cwd=self.fixture.work)
        command("git", "reset", "--hard", self.fixture.parent, cwd=self.fixture.work)
        command(
            "git",
            "commit",
            "--allow-empty",
            "-m",
            "chore: release version v1.0.0",
            cwd=self.fixture.work,
        )
        initial_release = command("git", "rev-parse", "HEAD", cwd=self.fixture.work)
        command("git", "tag", "-a", "v1.0.0", "-m", "v1.0.0", cwd=self.fixture.work)
        arguments = self.prepare_args()
        arguments.tag = "v1.0.0"
        release_state.cmd_prepare(arguments)
        manifest = release_state.read_manifest(self.manifest)
        self.assertEqual(manifest["parent_sha"], self.fixture.parent)
        self.assertEqual(manifest["release_sha"], initial_release)
        self.assertEqual(
            command(
                "git", "rev-list", "--parents", "-n", "1", "HEAD", cwd=self.fixture.work
            ),
            f"{initial_release} {self.fixture.parent}",
        )
        self.assertEqual(command("git", "cat-file", "-t", "v1.0.0", cwd=self.fixture.work), "tag")
        self.assertEqual(
            command("git", "rev-parse", "v1.0.0^{commit}", cwd=self.fixture.work),
            initial_release,
        )

    def test_nested_annotated_release_tag_is_refused_before_staging(self) -> None:
        command("git", "tag", "-d", "v1.1.0", cwd=self.fixture.work)
        command(
            "git",
            "tag",
            "-a",
            "inner-release",
            "-m",
            "inner release",
            self.fixture.release,
            cwd=self.fixture.work,
        )
        command(
            "git",
            "tag",
            "-a",
            "v1.1.0",
            "-m",
            "outer release",
            "inner-release",
            cwd=self.fixture.work,
        )
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_prepare(self.prepare_args())
        self.assertFalse(self.manifest.exists())
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))

    def test_wrong_internal_annotated_tag_name_is_refused_before_staging(self) -> None:
        payload = (
            f"object {self.fixture.release}\n"
            "type commit\n"
            "tag v9.9.9\n"
            "tagger Fixture <fixture@example.com> 1700000000 +0000\n"
            "\n"
            "wrong internal version\n"
        )
        result = subprocess.run(
            ["git", "mktag"],
            cwd=self.fixture.work,
            input=payload,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        wrong_tag = result.stdout.strip()
        command(
            "git",
            "update-ref",
            "refs/tags/v1.1.0",
            wrong_tag,
            self.fixture.tag_object,
            cwd=self.fixture.work,
        )
        with self.assertRaises(release_state.ReleaseError):
            release_state.cmd_prepare(self.prepare_args())
        self.assertFalse(self.manifest.exists())
        self.assertIsNone(self.remote_ref("refs/heads/release/gitops/1.1.0"))
        self.assertIsNone(self.remote_ref("refs/tags/v1.1.0"))

    def test_input_validation_rejects_injection_and_traversal(self) -> None:
        invalid_prefixes = ["../release/", "/release/", "release\\gitops/", "release"]
        for prefix in invalid_prefixes:
            with self.subTest(prefix=prefix):
                with self.assertRaises(release_state.ReleaseError):
                    release_state.staging_branch(prefix, "v1.2.3")
        for workflow in ["../ci.yml", "ci.yml;echo", "123", "path/ci.yml"]:
            with self.subTest(workflow=workflow):
                with self.assertRaises(release_state.ReleaseError):
                    release_state.normalize_workflow(workflow, "workflow")
        with self.assertRaises(release_state.ReleaseError):
            release_state.parse_required_checks('["CI\\nforged"]')
        with self.assertRaises(release_state.ReleaseError):
            release_state.parse_required_checks('[{"name":"CI"}]')

    def test_dispatch_without_run_id_stops_after_one_attempt(self) -> None:
        response = subprocess.CompletedProcess(["gh"], 0, stdout="", stderr="")
        with mock.patch.object(release_state, "run", return_value=response) as api_run:
            with self.assertRaises(release_state.ReleaseError):
                RealGitHub("owner/repo").dispatch(
                    "ci.yml", "release/gitops/1.1.0", {"release_validation": "{}"}
                )
        self.assertEqual(api_run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
