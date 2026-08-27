#!/usr/bin/env python3
"""Manage checked release staging without changing repository protection."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any


API_VERSION = "2026-03-10"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(?:yml|yaml)$")
SOURCE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class ReleaseError(RuntimeError):
    """A stopped release state transition."""


def fail(message: str) -> None:
    raise ReleaseError(message)


def run(
    command: list[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"Command failed ({shlex.join(command)}): {detail}")
    return result


def git(*arguments: str, check: bool = True) -> str:
    return run(["git", *arguments], check=check).stdout.strip()


def require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        fail(f"{name} must be a full lowercase Git SHA.")
    return value


def require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        fail(f"{name} must be a full lowercase SHA-256 value.")
    return value


def require_repository(value: Any) -> str:
    if not isinstance(value, str) or not REPOSITORY_RE.fullmatch(value):
        fail("Repository must have the form owner/name.")
    return value


def require_tag(value: Any) -> str:
    if not isinstance(value, str) or not TAG_RE.fullmatch(value):
        fail("Release tag must be a v-prefixed SemVer tag.")
    return value


def normalize_workflow(value: Any, name: str) -> str:
    if not isinstance(value, str):
        fail(f"{name} must be a workflow file name ending in .yml or .yaml.")
    candidate = value
    if candidate.startswith(".github/workflows/"):
        candidate = candidate.removeprefix(".github/workflows/")
    if not WORKFLOW_RE.fullmatch(candidate):
        fail(f"{name} must be a workflow file name ending in .yml or .yaml.")
    return candidate


def require_branch(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or any(
        character in value for character in "\r\n\0"
    ):
        fail(f"{name} is not a valid branch name.")
    result = run(["git", "check-ref-format", "--branch", value], check=False)
    if result.returncode != 0:
        fail(f"{name} is not a valid branch name.")
    return value


def staging_branch(prefix: str, tag: str) -> str:
    if not prefix.endswith("/") or prefix.startswith("/"):
        fail("Staging branch prefix must be relative and end with a slash.")
    if any(token in prefix for token in ("..", "@{", "\\", "\r", "\n", "\0")):
        fail("Staging branch prefix contains an unsafe sequence.")
    return require_branch(f"{prefix}{tag.removeprefix('v')}", "Staging branch")


def parse_required_checks(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        fail(f"required_checks is not valid JSON: {error.msg}")
    if not isinstance(parsed, list):
        fail("required_checks must be a JSON array.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 200
            or any(character in item for character in "\r\n\0")
        ):
            fail("Each required check name must be a nonempty single-line string.")
        if item in seen:
            fail(f"Duplicate required check: {item}.")
        seen.add(item)
        normalized.append(item)
    return sorted(normalized)


def required_checks_sha256(required_checks: list[str]) -> str:
    canonical = json.dumps(required_checks, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_changed_file_allowlist(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        fail(f"release_changed_files is not valid JSON: {error.msg}")
    if not isinstance(parsed, list) or not parsed:
        fail("release_changed_files must be a nonempty JSON array.")
    normalized: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item or len(item) > 300:
            fail("Each allowed release path must be a nonempty bounded string.")
        path = PurePosixPath(item)
        if (
            path.is_absolute()
            or str(path) != item
            or ".." in path.parts
            or "\\" in item
            or any(character in item for character in "\r\n\0")
        ):
            fail("Each allowed release path must be a normalized repository path.")
        if (
            path.parts[0] == ".github"
            or "scripts" in path.parts
            or path.suffix
            in {".bash", ".ex", ".js", ".php", ".py", ".rb", ".sh", ".ts"}
            or (path.suffix == ".exs" and item != "mix.exs")
        ):
            fail("Release workflow, validator, and script paths cannot be allowed changes.")
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        fail("release_changed_files contains a duplicate path.")
    return sorted(normalized)


def canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def release_policy_sha256(
    required_checks_identity: str,
    changed_files_identity: str,
    command_identity: str,
) -> str:
    return canonical_sha256(
        {
            "required_checks_sha256": required_checks_identity,
            "release_changed_files_sha256": changed_files_identity,
            "generated_release_command_sha256": command_identity,
        }
    )


def require_generated_command(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or "\0" in value
    ):
        fail("A nonempty bounded generated_release_command is required.")
    return value


def require_source_identity(value: Any) -> str:
    if not isinstance(value, str) or not SOURCE_IDENTITY_RE.fullmatch(value):
        fail("Release source identity is invalid.")
    return value


def changed_paths(parent: str, release: str) -> list[str]:
    output = run(["git", "diff", "--name-only", "-z", parent, release]).stdout
    paths = output.split("\0")
    if paths and paths[-1] == "":
        paths.pop()
    if len(set(paths)) != len(paths):
        fail("The generated release diff returned a duplicate path.")
    return sorted(paths)


def assert_changed_file_policy(
    parent: str, release: str, allowed_paths: list[str]
) -> None:
    unexpected = sorted(set(changed_paths(parent, release)) - set(allowed_paths))
    if unexpected:
        fail(f"The generated release changed paths outside the allowlist: {unexpected}.")


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def read_manifest(
    path: Path, expected_required_checks_sha256: str | None = None
) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot read release manifest {path}: {error}")
    if manifest.get("schema") != 1:
        fail("Release manifest has an unsupported schema.")
    target_branch = require_branch(manifest.get("target_branch", ""), "Target branch")
    default_branch = require_branch(manifest.get("default_branch", ""), "Default branch")
    if target_branch != default_branch:
        fail("Checked staging can promote only the recorded repository default branch.")
    required_checks = parse_required_checks(json.dumps(manifest.get("required_checks")))
    recorded_identity = require_sha256(
        manifest.get("required_checks_sha256", ""), "required_checks_sha256"
    )
    if recorded_identity != required_checks_sha256(required_checks):
        fail("The required-check policy does not match its recorded identity.")
    if expected_required_checks_sha256 is not None:
        expected_identity = require_sha256(
            expected_required_checks_sha256, "Expected required_checks_sha256"
        )
        if recorded_identity != expected_identity:
            fail("The required-check policy changed during the release run.")
    manifest["required_checks"] = required_checks
    allowed_paths = parse_changed_file_allowlist(
        json.dumps(manifest.get("release_changed_files"))
    )
    if manifest.get("release_changed_files_sha256") != canonical_sha256(allowed_paths):
        fail("The release changed-file policy does not match its recorded identity.")
    command = require_generated_command(manifest.get("generated_release_command", ""))
    command_identity = hashlib.sha256(
        command.encode("utf-8")
    ).hexdigest()
    if manifest.get("generated_release_command_sha256") != command_identity:
        fail("The generated-release command does not match its recorded identity.")
    policy_identity = release_policy_sha256(
        recorded_identity,
        manifest["release_changed_files_sha256"],
        command_identity,
    )
    if manifest.get("release_policy_sha256") != policy_identity:
        fail("The release validation policy does not match its recorded identity.")
    release_sha = require_sha(manifest.get("release_sha", ""), "release_sha")
    if manifest.get("tag_target_type") != "commit":
        fail("The recorded annotated tag target type is invalid.")
    if require_sha(manifest.get("tag_target_sha", ""), "tag_target_sha") != release_sha:
        fail("The recorded annotated tag direct target changed.")
    if require_tag(manifest.get("tag_internal_name", "")) != require_tag(
        manifest.get("tag", "")
    ):
        fail("The recorded annotated tag internal name changed.")
    require_source_identity(manifest.get("source_identity", ""))
    require_sha256(
        manifest.get("staging_owner_token_sha256", ""),
        "staging_owner_token_sha256",
    )
    attempted = manifest.get("staging_push_attempted")
    cleanup_owned = manifest.get("staging_cleanup_owned")
    proof = manifest.get("staging_ownership_proof")
    if (
        not isinstance(attempted, bool)
        or not isinstance(cleanup_owned, bool)
        or not isinstance(proof, str)
    ):
        fail("The staging ownership transition record is invalid.")
    if cleanup_owned and not attempted:
        fail("Staging cleanup ownership requires a recorded push attempt.")
    if cleanup_owned != bool(SHA256_RE.fullmatch(proof)):
        fail("The staging ownership transition record is inconsistent.")
    manifest["release_changed_files"] = allowed_paths
    return manifest


def read_command_manifest(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(args.manifest)
    manifest = read_manifest(manifest_path, args.required_checks_sha256)
    expected_policy = require_sha256(
        args.release_policy_sha256, "Expected release_policy_sha256"
    )
    if manifest["release_policy_sha256"] != expected_policy:
        fail("The release validation policy changed during the release run.")
    return manifest_path, manifest


def require_staging_owner(
    manifest: dict[str, Any], token: str, source_identity: str
) -> bytes:
    source = require_source_identity(source_identity)
    if source != manifest["source_identity"]:
        fail("The staging owner source does not match this release run.")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        fail("The staging owner token is invalid.")
    token_bytes = token.encode("ascii")
    if not hmac.compare_digest(
        hashlib.sha256(token_bytes).hexdigest(),
        manifest["staging_owner_token_sha256"],
    ):
        fail("The staging owner token does not match this release run.")
    return token_bytes


def staging_ownership_proof(manifest: dict[str, Any], token: bytes) -> str:
    payload = {
        "repository": manifest["repository"],
        "remote": manifest["remote"],
        "source_identity": manifest["source_identity"],
        "target_branch": manifest["target_branch"],
        "default_branch": manifest["default_branch"],
        "parent_sha": manifest["parent_sha"],
        "release_sha": manifest["release_sha"],
        "tag": manifest["tag"],
        "tag_object_sha": manifest["tag_object_sha"],
        "tag_target_type": manifest["tag_target_type"],
        "tag_target_sha": manifest["tag_target_sha"],
        "tag_internal_name": manifest["tag_internal_name"],
        "staging_branch": manifest["staging_branch"],
        "required_checks_sha256": manifest["required_checks_sha256"],
        "release_changed_files_sha256": manifest["release_changed_files_sha256"],
        "generated_release_command_sha256": manifest[
            "generated_release_command_sha256"
        ],
        "release_policy_sha256": manifest["release_policy_sha256"],
        "transition": "staging-cleanup-owned",
    }
    message = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def require_owned_staging_attempt(
    manifest: dict[str, Any], token: str, source_identity: str
) -> None:
    token_bytes = require_staging_owner(manifest, token, source_identity)
    if not manifest.get("staging_cleanup_owned"):
        fail("This release run did not prove staging branch creation; cleanup is refused.")
    expected = staging_ownership_proof(manifest, token_bytes)
    if not hmac.compare_digest(manifest.get("staging_ownership_proof", ""), expected):
        fail("The staging ownership record is invalid; cleanup is refused.")


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(path)


class GitHub:
    def __init__(self, repository: str):
        self.repository = require_repository(repository)

    def api(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        command = [
            "gh",
            "api",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {API_VERSION}",
            endpoint,
        ]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload, separators=(",", ":"))
        result = run(command, check=False, input_text=input_text)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            if allow_not_found and re.search(r"(?:HTTP )?404\b", detail):
                return None
            fail(f"GitHub API request failed for {endpoint}: {detail}")
        if not result.stdout.strip():
            return {}
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            fail(f"GitHub API returned invalid JSON for {endpoint}: {error.msg}")
        if not isinstance(response, dict):
            fail(f"GitHub API returned an unexpected response for {endpoint}.")
        return response

    def workflow(self, workflow: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(workflow, safe="")
        response = self.api(f"repos/{self.repository}/actions/workflows/{encoded}")
        assert response is not None
        workflow_id = response.get("id")
        path = response.get("path")
        state = response.get("state")
        if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
            fail("The selected validation workflow has no numeric workflow ID.")
        expected_path = f".github/workflows/{workflow}"
        if path != expected_path or state != "active":
            fail(f"The selected workflow must be active at {expected_path}.")
        return {"id": workflow_id, "path": path}

    def default_branch(self) -> str:
        response = self.api(f"repos/{self.repository}")
        assert response is not None
        value = response.get("default_branch")
        if not isinstance(value, str):
            fail("Repository metadata returned no default branch.")
        return require_branch(value, "Repository default branch")

    def artifact_exists(self, name: str) -> bool:
        encoded = urllib.parse.quote(name, safe="")
        for page in range(1, 101):
            response = self.api(
                f"repos/{self.repository}/actions/artifacts?name={encoded}"
                f"&per_page=100&page={page}"
            )
            assert response is not None
            artifacts = response.get("artifacts")
            if not isinstance(artifacts, list):
                fail("GitHub returned an invalid artifact list.")
            if any(
                isinstance(artifact, dict)
                and artifact.get("name") == name
                and artifact.get("expired") is not True
                for artifact in artifacts
            ):
                return True
            if len(artifacts) < 100:
                return False
        fail("Artifact pagination exceeded the safety limit.")

    def dispatch(
        self, workflow: str, ref: str, inputs: dict[str, str | bool]
    ) -> dict[str, Any]:
        encoded = urllib.parse.quote(workflow, safe="")
        response = self.api(
            f"repos/{self.repository}/actions/workflows/{encoded}/dispatches",
            method="POST",
            payload={
                "ref": ref,
                "inputs": inputs,
            },
        )
        if not response:
            fail(
                "Workflow dispatch did not return a workflow run ID. It might have "
                "succeeded. Do not retry it automatically; use recorded recovery state."
            )
        run_id = response.get("workflow_run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            fail(
                "Workflow dispatch returned no valid workflow_run_id. The dispatch might "
                "have succeeded. Do not retry it automatically."
            )
        return {
            "run_id": run_id,
            "run_url": response.get("html_url") or response.get("run_url", ""),
        }

    def run(self, run_id: int) -> dict[str, Any]:
        response = self.api(f"repos/{self.repository}/actions/runs/{run_id}")
        assert response is not None
        return response

    def jobs(self, run_id: int, attempt: int) -> list[dict[str, Any]]:
        all_jobs: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.api(
                f"repos/{self.repository}/actions/runs/{run_id}/attempts/{attempt}/jobs"
                f"?per_page=100&page={page}"
            )
            assert response is not None
            page_jobs = response.get("jobs")
            if not isinstance(page_jobs, list):
                fail("GitHub returned an invalid workflow-jobs response.")
            all_jobs.extend(page_jobs)
            if len(page_jobs) < 100:
                job_ids = [job.get("id") for job in all_jobs if isinstance(job, dict)]
                if len(job_ids) != len(all_jobs) or len(set(job_ids)) != len(job_ids):
                    fail("GitHub returned missing or duplicate workflow job IDs.")
                return all_jobs
            page += 1
            if page > 100:
                fail("Workflow-job pagination exceeded the safety limit.")

def remote_ref(remote: str, ref: str) -> str | None:
    result = run(["git", "ls-remote", "--refs", remote, ref], check=False)
    if result.returncode != 0:
        fail(f"Cannot read remote ref {ref}: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        fail(f"Remote returned more than one value for {ref}.")
    fields = lines[0].split("\t", 1)
    if len(fields) != 2:
        fail(f"Remote returned an invalid value for {ref}.")
    sha, returned_ref = fields
    if returned_ref != ref:
        fail(f"Remote returned the wrong ref while reading {ref}.")
    return require_sha(sha, ref)


def remote_tag(remote: str, tag: str) -> tuple[str | None, str | None]:
    ref = f"refs/tags/{tag}"
    result = run(["git", "ls-remote", remote, ref, f"{ref}^{{}}"], check=False)
    if result.returncode != 0:
        fail(f"Cannot read remote tag {tag}: {result.stderr.strip()}")
    object_sha = None
    peeled_sha = None
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2:
            fail(f"Remote returned an invalid value for tag {tag}.")
        sha, returned_ref = fields
        if returned_ref == ref:
            object_sha = require_sha(sha, ref)
        elif returned_ref == f"{ref}^{{}}":
            peeled_sha = require_sha(sha, f"{ref}^{{}}")
        else:
            fail(f"Remote returned an unexpected ref for tag {tag}.")
    return object_sha, peeled_sha


def annotated_tag_target(tag_ref: str) -> tuple[str, str, str]:
    if git("cat-file", "-t", tag_ref) != "tag":
        fail("The release tag must be annotated.")
    content = git("cat-file", "-p", tag_ref)
    object_values: list[str] = []
    type_values: list[str] = []
    tag_values: list[str] = []
    for line in content.splitlines():
        if not line:
            break
        key, separator, value = line.partition(" ")
        if separator and key == "object":
            object_values.append(value)
        elif separator and key == "type":
            type_values.append(value)
        elif separator and key == "tag":
            tag_values.append(value)
    if len(object_values) != 1 or len(type_values) != 1 or len(tag_values) != 1:
        fail("The annotated release tag has invalid direct-target metadata.")
    return (
        require_sha(object_values[0], "Annotated tag direct target"),
        type_values[0],
        require_tag(tag_values[0]),
    )


def assert_direct_release_tag(
    tag_ref: str,
    release: str,
    tag_name: str,
    expected_tag_object: str | None = None,
) -> None:
    if expected_tag_object is not None and git("rev-parse", tag_ref) != expected_tag_object:
        fail("The annotated tag object changed after it was recorded.")
    target_sha, target_type, internal_name = annotated_tag_target(tag_ref)
    if target_type != "commit" or target_sha != release:
        fail("The annotated release tag must target the release commit directly.")
    if internal_name != tag_name:
        fail("The annotated release tag internal name must match its version ref.")


def assert_local_release(manifest: dict[str, Any]) -> None:
    parent = require_sha(manifest["parent_sha"], "parent_sha")
    release = require_sha(manifest["release_sha"], "release_sha")
    tag = require_tag(manifest["tag"])
    if git("rev-parse", "HEAD") != release:
        fail("The checkout no longer matches the generated release commit.")
    parents = git("rev-list", "--parents", "-n", "1", release).split()
    if parents != [release, parent]:
        fail("The generated release must be one commit with the exact recorded parent.")
    if git("rev-parse", f"{release}^{{tree}}") != manifest["tree_sha"]:
        fail("The generated release tree changed after it was recorded.")
    tag_ref = f"refs/tags/{tag}"
    assert_direct_release_tag(tag_ref, release, tag, manifest["tag_object_sha"])
    if manifest["tag_target_type"] != "commit" or manifest["tag_target_sha"] != release:
        fail("The recorded annotated tag direct target is invalid.")
    if manifest["tag_internal_name"] != tag:
        fail("The recorded annotated tag internal name is invalid.")


def validation_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": 1,
        "repository": manifest["repository"],
        "ref": f"refs/heads/{manifest['staging_branch']}",
        "release_sha": manifest["release_sha"],
        "parent_sha": manifest["parent_sha"],
        "tree_sha": manifest["tree_sha"],
        "changelog_before_sha": manifest["changelog_before_sha"],
        "changelog_after_sha": manifest["changelog_after_sha"],
        "validation_workflow": f".github/workflows/{manifest['validation_workflow']}",
        "validation_workflow_sha": manifest["validation_workflow_sha"],
        "release_changed_files": manifest["release_changed_files"],
        "release_changed_files_sha256": manifest["release_changed_files_sha256"],
        "generated_release_command_sha256": manifest[
            "generated_release_command_sha256"
        ],
        "required_checks_sha256": manifest["required_checks_sha256"],
        "release_policy_sha256": manifest["release_policy_sha256"],
        "tag": manifest["tag"],
        "actor": "github-actions[bot]",
    }


def assert_prepare_absent(
    *,
    github: GitHub,
    remote: str,
    target_branch: str,
    staging_branch_name: str,
    tag: str,
    parent_sha: str,
    artifact_name: str,
) -> None:
    if remote_ref(remote, f"refs/heads/{target_branch}") != parent_sha:
        fail("The remote default branch is not the exact release parent.")
    if remote_ref(remote, f"refs/heads/{staging_branch_name}") is not None:
        fail("The deterministic staging branch already exists. Refusing repeated prepare.")
    tag_object, tag_peeled = remote_tag(remote, tag)
    if tag_object is not None or tag_peeled is not None:
        fail("The release tag already exists remotely. Refusing repeated prepare.")
    if github.artifact_exists(artifact_name):
        fail("A non-expired checked-release state artifact already exists for this parent.")


def prepared_artifact_name(parent: str) -> str:
    return f"checked-release-parent-{require_sha(parent, 'Parent')}-prepared"


def cmd_precheck_parent(args: argparse.Namespace) -> None:
    repository = require_repository(args.repository)
    target = require_branch(args.target_branch, "Target branch")
    default_branch = require_branch(args.default_branch, "Default branch")
    if target != default_branch:
        fail("Checked staging must prepare the repository default branch.")
    parent = require_sha(args.parent, "Parent")
    prefix_probe = staging_branch(args.staging_prefix, "v0.0.0")
    branch_prefix = prefix_probe.removesuffix("0.0.0")
    github = GitHub(repository)
    if github.default_branch() != default_branch:
        fail("Trusted event metadata does not match the repository default branch.")
    if remote_ref(args.remote, f"refs/heads/{target}") != parent:
        fail("The remote default branch is not the exact release parent.")
    staged = run(
        [
            "git",
            "ls-remote",
            "--heads",
            args.remote,
            f"refs/heads/{branch_prefix}*",
        ],
        check=False,
    )
    if staged.returncode != 0:
        fail(f"Cannot read remote staging refs: {staged.stderr.strip()}")
    if staged.stdout.strip():
        fail("A checked-release staging branch already exists. Refusing regeneration.")
    artifact_name = prepared_artifact_name(parent)
    if github.artifact_exists(artifact_name):
        fail("A checked release from this exact parent already has durable state.")
    write_output("artifact_name", artifact_name)


def cmd_precheck(args: argparse.Namespace) -> None:
    repository = require_repository(args.repository)
    target = require_branch(args.target_branch, "Target branch")
    default_branch = require_branch(args.default_branch, "Default branch")
    if target != default_branch:
        fail("Checked staging must prepare the repository default branch.")
    tag = require_tag(args.tag)
    parent = require_sha(args.parent, "Parent")
    branch = staging_branch(args.staging_prefix, tag)
    artifact_name = prepared_artifact_name(parent)
    github = GitHub(repository)
    if github.default_branch() != default_branch:
        fail("Trusted event metadata does not match the repository default branch.")
    assert_prepare_absent(
        github=github,
        remote=args.remote,
        target_branch=target,
        staging_branch_name=branch,
        tag=tag,
        parent_sha=parent,
        artifact_name=artifact_name,
    )
    write_output("tag", tag)
    write_output("staging_branch", branch)
    write_output("artifact_name", artifact_name)


def cmd_prepare(args: argparse.Namespace) -> None:
    repository = require_repository(args.repository)
    target = require_branch(args.target_branch, "Target branch")
    default_branch = require_branch(args.default_branch, "Default branch")
    if target != default_branch:
        fail("Checked staging must prepare the repository default branch.")
    tag = require_tag(args.tag)
    parent = require_sha(args.parent, "Parent")
    release = require_sha(git("rev-parse", "HEAD"), "Release commit")
    validation_workflow = normalize_workflow(args.validation_workflow, "Validation workflow")
    publish_workflow = normalize_workflow(args.publish_workflow, "Publish workflow")
    required_checks = parse_required_checks(args.required_checks)
    required_checks_identity = required_checks_sha256(required_checks)
    release_changed_files = parse_changed_file_allowlist(args.release_changed_files)
    release_changed_files_identity = canonical_sha256(release_changed_files)
    generated_release_command = require_generated_command(
        args.generated_release_command
    )
    generated_release_command_identity = hashlib.sha256(
        generated_release_command.encode("utf-8")
    ).hexdigest()
    release_policy_identity = release_policy_sha256(
        required_checks_identity,
        release_changed_files_identity,
        generated_release_command_identity,
    )
    source_identity = require_source_identity(args.source_identity)
    branch = staging_branch(args.staging_prefix, tag)
    artifact_name = prepared_artifact_name(parent)

    if git("rev-list", "--parents", "-n", "1", release).split() != [release, parent]:
        fail("Release preparation must create exactly one commit from the recorded parent.")
    assert_changed_file_policy(parent, release, release_changed_files)
    tree_sha = require_sha(git("rev-parse", f"{release}^{{tree}}"), "Tree")
    tag_ref = f"refs/tags/{tag}"
    tag_object = require_sha(git("rev-parse", tag_ref), "Tag object")
    assert_direct_release_tag(tag_ref, release, tag, tag_object)

    before_blob = require_sha(git("rev-parse", f"{parent}:CHANGELOG.md"), "Old changelog blob")
    after_blob = require_sha(git("rev-parse", f"{release}:CHANGELOG.md"), "New changelog blob")
    workflow_path = f".github/workflows/{validation_workflow}"
    parent_workflow_sha = require_sha(
        git("rev-parse", f"{parent}:{workflow_path}"), "Validation workflow blob"
    )
    release_workflow_sha = require_sha(
        git("rev-parse", f"{release}:{workflow_path}"), "Validation workflow blob"
    )
    if parent_workflow_sha != release_workflow_sha:
        fail("The generated release commit must not change the validation workflow.")
    changed_control_files = git(
        "diff",
        "--name-only",
        parent,
        release,
        "--",
        ".github/workflows",
        ".github/actions",
    )
    if changed_control_files:
        fail("The generated release commit must not change workflow control files.")

    github = GitHub(repository)
    if github.default_branch() != default_branch:
        fail("Trusted event metadata does not match the repository default branch.")
    assert_prepare_absent(
        github=github,
        remote=args.remote,
        target_branch=target,
        staging_branch_name=branch,
        tag=tag,
        parent_sha=parent,
        artifact_name=artifact_name,
    )
    workflow = github.workflow(validation_workflow)

    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = state_dir / "release.bundle"
    run(
        ["git", "bundle", "create", str(bundle_path), "HEAD", tag_ref],
        check=True,
    )
    bundle_path.chmod(0o600)
    manifest_path = state_dir / "manifest.json"
    manifest = {
        "schema": 1,
        "phase": "prepared",
        "repository": repository,
        "remote": args.remote,
        "target_branch": target,
        "default_branch": default_branch,
        "staging_branch": branch,
        "staging_branch_prefix": args.staging_prefix,
        "tag": tag,
        "parent_sha": parent,
        "release_sha": release,
        "tree_sha": tree_sha,
        "tag_object_sha": tag_object,
        "tag_target_type": "commit",
        "tag_target_sha": release,
        "tag_internal_name": tag,
        "changelog_before_sha": before_blob,
        "changelog_after_sha": after_blob,
        "validation_workflow": validation_workflow,
        "validation_workflow_id": workflow["id"],
        "validation_workflow_sha": release_workflow_sha,
        "publish_workflow": publish_workflow,
        "required_checks": required_checks,
        "required_checks_sha256": required_checks_identity,
        "release_changed_files": release_changed_files,
        "release_changed_files_sha256": release_changed_files_identity,
        "generated_release_command": generated_release_command,
        "generated_release_command_sha256": generated_release_command_identity,
        "release_policy_sha256": release_policy_identity,
        "source_identity": source_identity,
        "staging_owner_token_sha256": "",
        "staging_push_attempted": False,
        "staging_cleanup_owned": False,
        "staging_ownership_proof": "",
        "artifact_name": artifact_name,
        "validation_dispatch_attempted": False,
        "publish_dispatch_attempted": False,
        "promoted": False,
    }
    write_manifest(manifest_path, manifest)
    write_output("manifest", str(manifest_path))
    write_output("state_dir", str(state_dir))
    write_output("staging_branch", branch)
    write_output("release_sha", release)
    write_output("tag_object_sha", tag_object)
    write_output("required_checks_sha256", required_checks_identity)
    write_output("release_policy_sha256", release_policy_identity)
    owner_token = secrets.token_hex(32)
    manifest["staging_owner_token_sha256"] = hashlib.sha256(
        owner_token.encode("ascii")
    ).hexdigest()
    write_manifest(manifest_path, manifest)
    print(f"::add-mask::{owner_token}")
    write_output("staging_owner_token", owner_token)
    write_output("artifact_name", artifact_name)


def cmd_stage(args: argparse.Namespace) -> None:
    manifest_path, manifest = read_command_manifest(args)
    if manifest["phase"] != "prepared":
        fail("The release manifest is not in prepared state.")
    assert_local_release(manifest)
    remote = manifest["remote"]
    branch_ref = f"refs/heads/{manifest['staging_branch']}"
    if remote_ref(remote, branch_ref) is not None:
        fail("The staging branch appeared before its owned push.")
    owner_token = require_staging_owner(
        manifest, args.staging_owner_token, args.source_identity
    )
    manifest["staging_push_attempted"] = True
    manifest["phase"] = "staging-push-attempted"
    write_manifest(manifest_path, manifest)
    push = run(
        [
            "git",
            "push",
            "--atomic",
            "--porcelain",
            "--no-follow-tags",
            f"--force-with-lease={branch_ref}:",
            remote,
            f"{manifest['release_sha']}:{branch_ref}",
        ],
        check=False,
    )
    if push.returncode != 0:
        detail = push.stderr.strip() or push.stdout.strip()
        fail(
            "The staging push did not return a successful ownership result. "
            f"The branch is retained because ownership is unknown: {detail}"
        )
    expected_result = (
        f"*\t{manifest['release_sha']}:{branch_ref}\t[new branch]"
    )
    up_to_date_result = (
        f"=\t{manifest['release_sha']}:{branch_ref}\t[up to date]"
    )
    if up_to_date_result in push.stdout.splitlines():
        fail(
            "The staging branch was already at the release commit. "
            "This run did not create it and does not own cleanup."
        )
    if expected_result not in push.stdout.splitlines():
        fail(
            "The staging push did not prove that this run created the branch. "
            "An up-to-date branch is pre-existing and is not owned."
        )
    manifest["staging_cleanup_owned"] = True
    manifest["staging_ownership_proof"] = staging_ownership_proof(
        manifest, owner_token
    )
    write_manifest(manifest_path, manifest)
    if remote_ref(remote, branch_ref) != manifest["release_sha"]:
        fail("The staging branch does not point to the generated release commit.")
    tag_object, tag_peeled = remote_tag(remote, manifest["tag"])
    if tag_object is not None or tag_peeled is not None:
        fail("The release tag was exposed before promotion.")
    manifest["phase"] = "staged"
    write_manifest(manifest_path, manifest)


def cmd_dispatch_validation(args: argparse.Namespace) -> None:
    manifest_path, manifest = read_command_manifest(args)
    if manifest["phase"] != "staged" or manifest["validation_dispatch_attempted"]:
        fail("Validation dispatch is not allowed in the current release state.")
    assert_local_release(manifest)
    branch_ref = f"refs/heads/{manifest['staging_branch']}"
    if remote_ref(manifest["remote"], branch_ref) != manifest["release_sha"]:
        fail("The staging branch changed before validation dispatch.")
    manifest["validation_dispatch_attempted"] = True
    manifest["phase"] = "validation-dispatch-attempted"
    write_manifest(manifest_path, manifest)

    metadata = json.dumps(validation_metadata(manifest), separators=(",", ":"))
    response = GitHub(manifest["repository"]).dispatch(
        manifest["validation_workflow"],
        manifest["staging_branch"],
        {"release_validation": metadata},
    )
    manifest["validation_run_id"] = response["run_id"]
    manifest["validation_run_url"] = response["run_url"]
    manifest["validation_run_attempt"] = 1
    manifest["phase"] = "validation-dispatched"
    write_manifest(manifest_path, manifest)
    write_output("validation_run_id", str(response["run_id"]))
    write_output("validation_run_url", str(response["run_url"]))


def validate_run(manifest: dict[str, Any], workflow_run: dict[str, Any]) -> None:
    expected = {
        "id": manifest["validation_run_id"],
        "workflow_id": manifest["validation_workflow_id"],
        "event": "workflow_dispatch",
        "head_sha": manifest["release_sha"],
        "head_branch": manifest["staging_branch"],
        "run_attempt": manifest["validation_run_attempt"],
    }
    for key, value in expected.items():
        if workflow_run.get(key) != value:
            fail(f"Validation run {key} does not match the recorded release state.")
    repository = workflow_run.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != manifest["repository"]:
        fail("Validation run repository does not match the recorded release repository.")
    actor = workflow_run.get("actor")
    if not isinstance(actor, dict) or actor.get("login") != "github-actions[bot]":
        fail("Validation run actor is not github-actions[bot].")
    expected_path = f".github/workflows/{manifest['validation_workflow']}"
    path = workflow_run.get("path")
    if path != expected_path and not (
        isinstance(path, str) and path.startswith(f"{expected_path}@")
    ):
        fail("Validation run used a different workflow file.")
    if workflow_run.get("status") != "completed":
        fail("Validation run is not complete.")
    if workflow_run.get("conclusion") != "success":
        fail("The complete validation run must have literal success.")


def validate_required_jobs(
    manifest: dict[str, Any], github: GitHub, workflow_run: dict[str, Any]
) -> None:
    run_id = manifest["validation_run_id"]
    attempt = manifest["validation_run_attempt"]
    release_sha = manifest["release_sha"]
    evidence: list[dict[str, Any]] = []
    for job in github.jobs(run_id, attempt):
        if not isinstance(job, dict):
            fail("GitHub returned an invalid validation job.")
        if (
            isinstance(job.get("id"), bool)
            or not isinstance(job.get("id"), int)
            or job.get("run_id") != run_id
            or job.get("run_attempt") != attempt
            or job.get("head_sha") != release_sha
        ):
            fail("A validation job does not belong to the exact run and release SHA.")
        evidence.append(job)

    for required_name in manifest["required_checks"]:
        matches = [job for job in evidence if job.get("name") == required_name]
        if not matches:
            fail(
                f"Required job {required_name} is missing from validation run "
                f"{run_id} attempt {attempt}."
            )
        for job in matches:
            if job.get("status") != "completed" or job.get("conclusion") != "success":
                conclusion = job.get("conclusion") or job.get("status") or "missing"
                fail(
                    f"Required job {required_name} did not have literal success in "
                    f"validation run {run_id} attempt {attempt} ({conclusion})."
                )


def cmd_wait_validation(args: argparse.Namespace) -> None:
    manifest_path, manifest = read_command_manifest(args)
    if manifest["phase"] != "validation-dispatched":
        fail("The validation run was not recorded before waiting.")
    timeout = args.timeout
    if timeout < 60 or timeout > 21600:
        fail("Validation timeout must be between 60 and 21600 seconds.")
    github = GitHub(manifest["repository"])
    if github.default_branch() != manifest["default_branch"]:
        fail("The repository default branch changed after release preparation.")
    deadline = time.monotonic() + timeout
    while True:
        workflow_run = github.run(manifest["validation_run_id"])
        status = workflow_run.get("status")
        if status == "completed":
            break
        if status not in {
            "queued",
            "in_progress",
            "requested",
            "waiting",
            "pending",
        }:
            fail(f"Validation run returned unsupported status: {status!r}.")
        if time.monotonic() >= deadline:
            fail("Validation run timed out. It was not cancelled or retried automatically.")
        time.sleep(min(15, max(1, int(deadline - time.monotonic()))))

    validate_run(manifest, workflow_run)
    validate_required_jobs(manifest, github, workflow_run)
    manifest["phase"] = "validated"
    manifest["validation_conclusion"] = "success"
    write_manifest(manifest_path, manifest)


def cmd_promote(args: argparse.Namespace) -> None:
    manifest_path, manifest = read_command_manifest(args)
    if manifest["phase"] != "validated" or manifest["promoted"]:
        fail("Promotion is not allowed in the current release state.")
    assert_local_release(manifest)
    github = GitHub(manifest["repository"])
    if github.default_branch() != manifest["default_branch"]:
        fail("The repository default branch changed after release preparation.")

    remote = manifest["remote"]
    target_ref = f"refs/heads/{manifest['target_branch']}"
    branch_ref = f"refs/heads/{manifest['staging_branch']}"
    tag_ref = f"refs/tags/{manifest['tag']}"
    if remote_ref(remote, target_ref) != manifest["parent_sha"]:
        fail("The target branch moved after release preparation.")
    if remote_ref(remote, branch_ref) != manifest["release_sha"]:
        fail("The staging branch changed after validation.")
    tag_object, tag_peeled = remote_tag(remote, manifest["tag"])
    if tag_object is not None or tag_peeled is not None:
        fail("The release tag appeared before promotion.")

    workflow_run = github.run(manifest["validation_run_id"])
    validate_run(manifest, workflow_run)
    validate_required_jobs(manifest, github, workflow_run)

    push = run(
        [
            "git",
            "push",
            "--atomic",
            "--no-follow-tags",
            f"--force-with-lease={target_ref}:{manifest['parent_sha']}",
            f"--force-with-lease={branch_ref}:{manifest['release_sha']}",
            f"--force-with-lease={tag_ref}:",
            remote,
            f"{manifest['release_sha']}:{target_ref}",
            f"{tag_ref}:{tag_ref}",
            f":{branch_ref}",
        ],
        check=False,
    )
    if push.returncode != 0:
        current_target = remote_ref(remote, target_ref)
        current_tag, current_peeled = remote_tag(remote, manifest["tag"])
        if current_target != manifest["parent_sha"] or current_tag is not None or current_peeled is not None:
            fail("Atomic promotion failed and remote refs are inconsistent; stop immediately.")
        detail = push.stderr.strip() or push.stdout.strip()
        fail(f"Atomic promotion was rejected; main and tag are unchanged: {detail}")

    if remote_ref(remote, target_ref) != manifest["release_sha"]:
        fail("Promotion did not move the target branch to the release commit.")
    if remote_ref(remote, branch_ref) is not None:
        fail("Promotion did not atomically remove the owned staging branch.")
    tag_object, tag_peeled = remote_tag(remote, manifest["tag"])
    if tag_object != manifest["tag_object_sha"] or tag_peeled != manifest["release_sha"]:
        fail("Remote annotated tag verification failed after promotion.")
    manifest["phase"] = "promoted"
    manifest["promoted"] = True
    write_manifest(manifest_path, manifest)


def cmd_dispatch_publish(args: argparse.Namespace) -> None:
    manifest_path, manifest = read_command_manifest(args)
    if manifest["phase"] != "promoted" or manifest["publish_dispatch_attempted"]:
        fail("Publish dispatch is not allowed in the current release state.")
    assert_local_release(manifest)
    remote = manifest["remote"]
    tag_object, tag_peeled = remote_tag(remote, manifest["tag"])
    if tag_object != manifest["tag_object_sha"] or tag_peeled != manifest["release_sha"]:
        fail("The remote annotated tag changed before publish dispatch.")
    target_ref = f"refs/heads/{manifest['target_branch']}"
    if remote_ref(remote, target_ref) != manifest["release_sha"]:
        fail("The target branch changed before publish dispatch.")
    manifest["publish_dispatch_attempted"] = True
    manifest["phase"] = "publish-dispatch-attempted"
    write_manifest(manifest_path, manifest)

    response = GitHub(manifest["repository"]).dispatch(
        manifest["publish_workflow"],
        manifest["target_branch"],
        {
            "operation": "publish",
            "tag_name": manifest["tag"],
            "staged_prepare": "true",
            "staging_branch_prefix": manifest["staging_branch_prefix"],
            "dry_run": "false",
            "hex_dry_run": "false",
        },
    )
    manifest["publish_run_id"] = response["run_id"]
    manifest["publish_run_url"] = response["run_url"]
    manifest["phase"] = "publish-dispatched"
    write_manifest(manifest_path, manifest)
    write_output("publish_run_id", str(response["run_id"]))
    write_output("publish_run_url", str(response["run_url"]))


def delete_branch(remote: str, branch: str, expected: str) -> None:
    branch_ref = f"refs/heads/{branch}"
    current = remote_ref(remote, branch_ref)
    if current is None:
        return
    if current != expected:
        fail("Refusing to delete a staging branch that no longer has the owned release SHA.")
    run(
        [
            "git",
            "push",
            "--atomic",
            "--no-follow-tags",
            f"--force-with-lease={branch_ref}:{expected}",
            remote,
            f":{branch_ref}",
        ]
    )
    if remote_ref(remote, branch_ref) is not None:
        fail("The owned staging branch still exists after cleanup.")


def cmd_cleanup_before_promotion(args: argparse.Namespace) -> None:
    _, manifest = read_command_manifest(args)
    if manifest.get("promoted"):
        fail("Release is promoted. Use publish-only recovery; no prepare cleanup is allowed.")
    tag_object, tag_peeled = remote_tag(manifest["remote"], manifest["tag"])
    if tag_object is not None or tag_peeled is not None:
        fail("A remote release tag exists. Stop instead of running prepare cleanup.")
    require_owned_staging_attempt(
        manifest, args.staging_owner_token, args.source_identity
    )
    delete_branch(
        manifest["remote"], manifest["staging_branch"], manifest["release_sha"]
    )


def cmd_verify_publish_tag(args: argparse.Namespace) -> None:
    repository = require_repository(args.repository)
    tag = require_tag(args.tag)
    target = require_branch(args.target_branch, "Target branch")
    del repository  # Validation is done before remote checks.
    tag_ref = f"refs/tags/{tag}"
    object_sha, peeled_sha = remote_tag(args.remote, tag)
    if object_sha is None or peeled_sha is None:
        fail("Publish-only recovery requires an existing remote annotated tag.")
    if git("rev-parse", tag_ref) != object_sha:
        fail("The local and remote annotated tag objects differ.")
    assert_direct_release_tag(tag_ref, peeled_sha, tag, object_sha)
    if git("rev-parse", "HEAD") != peeled_sha:
        fail("The publish checkout is not the peeled release commit.")
    run(["git", "fetch", "--no-tags", args.remote, f"refs/heads/{target}"])
    ancestor = run(
        ["git", "merge-base", "--is-ancestor", peeled_sha, "FETCH_HEAD"], check=False
    )
    if ancestor.returncode != 0:
        fail("The tagged release commit is not reachable from the target branch.")
    write_output("release_sha", peeled_sha)
    write_output("tag_object_sha", object_sha)


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser()
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    precheck_parent = subparsers.add_parser("precheck-parent")
    precheck_parent.add_argument("--repository", required=True)
    precheck_parent.add_argument("--target-branch", required=True)
    precheck_parent.add_argument("--default-branch", required=True)
    precheck_parent.add_argument("--staging-prefix", required=True)
    precheck_parent.add_argument("--parent", required=True)
    precheck_parent.add_argument("--remote", default="origin")
    precheck_parent.set_defaults(function=cmd_precheck_parent)

    precheck = subparsers.add_parser("precheck")
    precheck.add_argument("--repository", required=True)
    precheck.add_argument("--target-branch", required=True)
    precheck.add_argument("--default-branch", required=True)
    precheck.add_argument("--staging-prefix", required=True)
    precheck.add_argument("--tag", required=True)
    precheck.add_argument("--parent", required=True)
    precheck.add_argument("--remote", default="origin")
    precheck.set_defaults(function=cmd_precheck)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--target-branch", required=True)
    prepare.add_argument("--default-branch", required=True)
    prepare.add_argument("--staging-prefix", required=True)
    prepare.add_argument("--tag", required=True)
    prepare.add_argument("--parent", required=True)
    prepare.add_argument("--required-checks", required=True)
    prepare.add_argument("--release-changed-files", required=True)
    prepare.add_argument("--generated-release-command", required=True)
    prepare.add_argument("--source-identity", required=True)
    prepare.add_argument("--validation-workflow", required=True)
    prepare.add_argument("--publish-workflow", required=True)
    prepare.add_argument("--state-dir", required=True)
    prepare.add_argument("--remote", default="origin")
    prepare.set_defaults(function=cmd_prepare)

    for name, function in (
        ("stage", cmd_stage),
        ("dispatch-validation", cmd_dispatch_validation),
        ("promote", cmd_promote),
        ("dispatch-publish", cmd_dispatch_publish),
        ("cleanup-before-promotion", cmd_cleanup_before_promotion),
    ):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--manifest", required=True)
        subparser.add_argument("--required-checks-sha256", required=True)
        subparser.add_argument("--release-policy-sha256", required=True)
        if name in {"stage", "cleanup-before-promotion"}:
            subparser.add_argument("--staging-owner-token", required=True)
            subparser.add_argument("--source-identity", required=True)
        subparser.set_defaults(function=function)

    wait = subparsers.add_parser("wait-validation")
    wait.add_argument("--manifest", required=True)
    wait.add_argument("--required-checks-sha256", required=True)
    wait.add_argument("--release-policy-sha256", required=True)
    wait.add_argument("--timeout", required=True, type=int)
    wait.set_defaults(function=cmd_wait_validation)

    verify = subparsers.add_parser("verify-publish-tag")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--tag", required=True)
    verify.add_argument("--target-branch", required=True)
    verify.add_argument("--remote", default="origin")
    verify.set_defaults(function=cmd_verify_publish_tag)

    return command_parser


def main() -> int:
    try:
        arguments = parser().parse_args()
        arguments.function(arguments)
        return 0
    except ReleaseError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
