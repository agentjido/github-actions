#!/usr/bin/env python3
"""Validate trusted workflow-dispatch metadata for one generated release commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
WORKFLOW_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.(?:yml|yaml)$")
EXPECTED_KEYS = {
    "schema",
    "repository",
    "ref",
    "release_sha",
    "parent_sha",
    "tree_sha",
    "changelog_before_sha",
    "changelog_after_sha",
    "validation_workflow",
    "validation_workflow_sha",
    "release_changed_files",
    "release_changed_files_sha256",
    "generated_release_command_sha256",
    "required_checks_sha256",
    "release_policy_sha256",
    "tag",
    "actor",
}


class ValidationError(RuntimeError):
    """An invalid generated-release dispatch."""


def fail(message: str) -> None:
    raise ValidationError(message)


def git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        fail(result.stderr.strip() or f"Git command failed: {' '.join(arguments)}")
    return result.stdout.strip()


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"Release validation metadata has duplicate key: {key}.")
        result[key] = value
    return result


def parse_metadata(value: str) -> dict[str, Any]:
    if len(value.encode("utf-8")) > 16_384:
        fail("Release validation metadata is too large.")
    try:
        parsed = json.loads(value, object_pairs_hook=no_duplicate_keys)
    except json.JSONDecodeError as error:
        fail(f"Release validation metadata is invalid JSON: {error.msg}")
    if not isinstance(parsed, dict) or set(parsed) != EXPECTED_KEYS:
        fail("Release validation metadata has missing or unsupported fields.")
    if parsed.get("schema") != 1:
        fail("Release validation metadata has an unsupported schema.")
    return parsed


def require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        fail(f"{name} must be a full lowercase Git SHA.")
    return value


def require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        fail(f"{name} must be a full lowercase SHA-256 value.")
    return value


def canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_policy_identity(metadata: dict[str, Any]) -> None:
    required_checks_identity = require_sha256(
        metadata["required_checks_sha256"], "required_checks_sha256"
    )
    changed_files_identity = require_sha256(
        metadata["release_changed_files_sha256"],
        "release_changed_files_sha256",
    )
    command_identity = require_sha256(
        metadata["generated_release_command_sha256"],
        "generated_release_command_sha256",
    )
    policy_identity = require_sha256(
        metadata["release_policy_sha256"], "release_policy_sha256"
    )
    expected = canonical_sha256(
        {
            "required_checks_sha256": required_checks_identity,
            "release_changed_files_sha256": changed_files_identity,
            "generated_release_command_sha256": command_identity,
        }
    )
    if policy_identity != expected:
        fail("The release validation policy does not match its trusted identity.")


def validate_changed_file_policy(metadata: dict[str, Any], parent: str, release: str) -> None:
    raw_paths = metadata["release_changed_files"]
    if not isinstance(raw_paths, list) or not raw_paths:
        fail("Release changed-file metadata must be a nonempty array.")
    paths: list[str] = []
    for item in raw_paths:
        if not isinstance(item, str) or not item or len(item) > 300:
            fail("Release changed-file metadata contains an invalid path.")
        path = PurePosixPath(item)
        if (
            path.is_absolute()
            or str(path) != item
            or ".." in path.parts
            or "\\" in item
            or any(character in item for character in "\r\n\0")
            or path.parts[0] == ".github"
            or "scripts" in path.parts
            or path.suffix
            in {".bash", ".ex", ".js", ".php", ".py", ".rb", ".sh", ".ts"}
            or (path.suffix == ".exs" and item != "mix.exs")
        ):
            fail("Release changed-file metadata contains an unsafe path.")
        paths.append(item)
    if paths != sorted(set(paths)):
        fail("Release changed-file metadata must be sorted and unique.")
    identity = canonical_sha256(paths)
    if metadata["release_changed_files_sha256"] != identity:
        fail("Release changed-file metadata does not match its identity.")

    output = git("diff", "--name-only", "-z", parent, release)
    changed = output.split("\0")
    if changed and changed[-1] == "":
        changed.pop()
    unexpected = sorted(set(changed) - set(paths))
    if unexpected:
        fail(f"Generated release changed paths outside the trusted allowlist: {unexpected}.")


def validate_environment(metadata: dict[str, Any]) -> None:
    expected_repository = os.environ.get("GITHUB_REPOSITORY", "")
    expected_ref = os.environ.get("GITHUB_REF", "")
    expected_sha = os.environ.get("GITHUB_SHA", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    actor = os.environ.get("GITHUB_ACTOR", "")

    if not REPOSITORY_RE.fullmatch(expected_repository):
        fail("GITHUB_REPOSITORY is invalid.")
    if event_name != "workflow_dispatch":
        fail("Generated-release validation requires workflow_dispatch.")
    if metadata["repository"] != expected_repository:
        fail("Dispatch repository does not match release validation metadata.")
    if metadata["ref"] != expected_ref or not expected_ref.startswith("refs/heads/"):
        fail("Dispatch ref does not match the recorded staging ref.")
    if metadata["release_sha"] != expected_sha:
        fail("github.sha does not match the generated release commit.")
    if metadata["actor"] != "github-actions[bot]" or actor != metadata["actor"]:
        fail("Generated-release validation requires a github-actions[bot] dispatch.")


def validate_repository(metadata: dict[str, Any]) -> dict[str, str]:
    validate_policy_identity(metadata)
    release = require_sha(metadata["release_sha"], "release_sha")
    parent = require_sha(metadata["parent_sha"], "parent_sha")
    tree = require_sha(metadata["tree_sha"], "tree_sha")
    old_changelog = require_sha(metadata["changelog_before_sha"], "changelog_before_sha")
    new_changelog = require_sha(metadata["changelog_after_sha"], "changelog_after_sha")
    workflow_sha = require_sha(metadata["validation_workflow_sha"], "validation_workflow_sha")
    workflow = metadata["validation_workflow"]
    tag = metadata["tag"]
    if not isinstance(workflow, str) or not WORKFLOW_RE.fullmatch(workflow):
        fail("Validation workflow path is invalid.")
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        fail("Release tag is invalid.")
    if git("rev-parse", "HEAD") != release:
        fail("The checked-out commit does not match github.sha and the generated release.")
    if git("rev-list", "--parents", "-n", "1", release).split() != [release, parent]:
        fail("The generated release is not one commit from the exact recorded parent.")
    if git("rev-parse", f"{release}^{{tree}}") != tree:
        fail("The generated release tree does not match dispatch metadata.")
    if git("rev-parse", f"{parent}:CHANGELOG.md") != old_changelog:
        fail("The parent changelog blob does not match dispatch metadata.")
    if git("rev-parse", f"{release}:CHANGELOG.md") != new_changelog:
        fail("The generated changelog blob does not match dispatch metadata.")
    if git("rev-parse", f"{parent}:{workflow}") != workflow_sha:
        fail("The parent validation workflow does not match dispatch metadata.")
    if git("rev-parse", f"{release}:{workflow}") != workflow_sha:
        fail("The release commit changed the selected validation workflow.")
    changed_controls = git(
        "diff",
        "--name-only",
        parent,
        release,
        "--",
        ".github/workflows",
        ".github/actions",
    )
    if changed_controls:
        fail("The generated release commit changed workflow control files.")
    validate_changed_file_policy(metadata, parent, release)
    return {
        "RELEASE_PARENT_SHA": parent,
        "RELEASE_SHA": release,
        "RELEASE_TREE_SHA": tree,
        "RELEASE_TAG": tag,
        "RELEASE_CHANGELOG_BEFORE_SHA": old_changelog,
        "RELEASE_CHANGELOG_AFTER_SHA": new_changelog,
        "RELEASE_VALIDATION_WORKFLOW": workflow,
    }


def run_caller_command(
    command: str, release_environment: dict[str, str], expected_sha256: str
) -> None:
    if not command or len(command.encode("utf-8")) > 4096 or "\0" in command:
        fail("A nonempty bounded generated_release_command is required.")
    command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()
    expected = require_sha256(expected_sha256, "generated_release_command_sha256")
    if command_sha256 != expected:
        fail("generated_release_command does not match trusted prepare metadata.")
    environment = os.environ.copy()
    environment.update(release_environment)
    environment["GENERATED_RELEASE_COMMAND_SHA256"] = command_sha256
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        fail("The caller's generated-release validation command failed.")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--metadata", required=True)
    argument_parser.add_argument("--command", required=True)
    return argument_parser


def main() -> int:
    try:
        arguments = parser().parse_args()
        metadata = parse_metadata(arguments.metadata)
        validate_environment(metadata)
        release_environment = validate_repository(metadata)
        run_caller_command(
            arguments.command,
            release_environment,
            metadata["generated_release_command_sha256"],
        )
        print("Trusted generated-release metadata and caller output are valid.")
        return 0
    except ValidationError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
