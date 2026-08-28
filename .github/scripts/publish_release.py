#!/usr/bin/env python3
"""Publish one tagged package with idempotent publish-only recovery."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_VERSION = "2026-03-10"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)


class PublishError(RuntimeError):
    """A stopped publish-only transition."""


def fail(message: str) -> None:
    raise PublishError(message)


def run(
    command: list[str], *, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def github_release(repository: str, tag: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(tag, safe="")
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        f"X-GitHub-Api-Version: {API_VERSION}",
        f"repos/{repository}/releases/tags/{encoded}",
    ]
    response = run(command)
    if response.returncode != 0:
        detail = response.stderr.strip() or response.stdout.strip()
        if re.search(r"(?:HTTP )?404\b", detail):
            return None
        fail(f"Cannot read GitHub release state: {detail}")
    try:
        release = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        fail(f"GitHub returned invalid release JSON: {error.msg}")
    if not isinstance(release, dict):
        fail("GitHub returned an invalid release response.")
    if release.get("tag_name") != tag:
        fail("GitHub returned a release for a different tag.")
    if release.get("draft") is True:
        fail("A draft GitHub release is inconsistent with publish-only recovery.")
    return release


def hex_release_exists(api_url: str, package: str, version: str) -> bool:
    base = api_url.rstrip("/")
    package_path = urllib.parse.quote(package, safe="")
    version_path = urllib.parse.quote(version, safe="")
    request = urllib.request.Request(
        f"{base}/packages/{package_path}/releases/{version_path}",
        headers={"Accept": "application/json", "User-Agent": "jido-checked-release/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                fail(f"Hex returned unexpected HTTP status {response.status}.")
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        fail(f"Hex state check failed with HTTP status {error.code}.")
    except urllib.error.URLError as error:
        fail(f"Hex state check failed: {error.reason}")
    return False


def extract_changelog_notes(version: str, changelog: Path) -> str | None:
    if not changelog.is_file():
        return None
    lines = changelog.read_text(encoding="utf-8").splitlines()
    header = re.compile(r"^## \[v?[0-9]")
    wanted = (f"[{version}]", f"[v{version}]")
    output: list[str] = []
    found = False
    for line in lines:
        if header.match(line):
            if found:
                break
            if any(marker in line for marker in wanted):
                found = True
                continue
        elif found:
            output.append(line)
    notes = "\n".join(output).strip()
    return f"{notes}\n" if notes else None


def publish_hex() -> None:
    result = run(["mix", "hex.publish", "--yes"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(
            "Hex publish did not return success. It might have completed. Do not retry "
            f"automatically; run publish-only recovery after checking Hex. Detail: {detail}"
        )
    if result.stdout:
        print(result.stdout, end="")


def wait_for_hex(api_url: str, package: str, version: str, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if hex_release_exists(api_url, package, version):
            return
        if time.monotonic() >= deadline:
            fail(
                "Hex publish returned success, but the version is not visible. Do not "
                "publish again automatically; use publish-only recovery."
            )
        time.sleep(5)


def create_github_release(
    repository: str, tag: str, version: str, notes_mode: str, changelog: Path
) -> None:
    command = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repository,
        "--verify-tag",
        "--title",
        f"Release {tag}",
    ]
    notes = extract_changelog_notes(version, changelog) if notes_mode == "changelog" else None
    notes_path: Path | None = None
    if notes:
        notes_path = Path(os.environ.get("RUNNER_TEMP", ".")) / f"release-notes-{tag}.md"
        notes_path.write_text(notes, encoding="utf-8")
        command.extend(["--notes-file", str(notes_path)])
    else:
        command.append("--generate-notes")
    result = run(command)
    if notes_path:
        notes_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(
            "GitHub release creation did not return success. Do not retry it automatically "
            f"in this run; use publish-only recovery. Detail: {detail}"
        )


def publish(args: argparse.Namespace) -> None:
    if not REPOSITORY_RE.fullmatch(args.repository):
        fail("Repository must have the form owner/name.")
    if not PACKAGE_RE.fullmatch(args.package):
        fail("Hex package name is invalid.")
    if not VERSION_RE.fullmatch(args.version) or args.tag != f"v{args.version}":
        fail("Package version and release tag do not match.")
    if args.notes_mode not in {"changelog", "generated"}:
        fail("Release notes mode must be changelog or generated.")

    if args.dry_run:
        print("Dry run: Hex and GitHub release state were not changed.")
        return
    if args.hex_dry_run:
        environment = os.environ.copy()
        environment["HEX_API_KEY"] = environment.get("HEX_API_KEY") or "dry-run"
        result = run(
            ["mix", "hex.publish", "--dry-run", "--yes"], environment=environment
        )
        if result.returncode != 0:
            fail(result.stderr.strip() or "Hex dry run failed.")
        if result.stdout:
            print(result.stdout, end="")
        return

    hex_exists = hex_release_exists(args.hex_api_url, args.package, args.version)
    release = github_release(args.repository, args.tag)
    if release is not None and not hex_exists:
        fail(
            "GitHub release exists while the Hex version is absent. This state is "
            "inconsistent; no automatic publish is allowed."
        )
    if not hex_exists:
        if not args.has_hex_api_key:
            fail("Hex version is absent and publish requires HEX_API_KEY.")
        publish_hex()
        wait_for_hex(args.hex_api_url, args.package, args.version)
        hex_exists = True
    else:
        print(f"Hex already contains {args.package}@{args.version}; skipping upload.")

    if release is None:
        create_github_release(
            args.repository,
            args.tag,
            args.version,
            args.notes_mode,
            Path(args.changelog),
        )
        release = github_release(args.repository, args.tag)
        if release is None:
            fail("GitHub release is still absent after creation returned success.")
    else:
        print(f"GitHub release {args.tag} already exists; leaving it unchanged.")

    if not hex_exists or release is None:
        fail("Publish-only recovery did not reach the complete state.")
    print(f"Publish state is complete for {args.package}@{args.version} and {args.tag}.")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--repository", required=True)
    argument_parser.add_argument("--package", required=True)
    argument_parser.add_argument("--version", required=True)
    argument_parser.add_argument("--tag", required=True)
    argument_parser.add_argument("--notes-mode", required=True)
    argument_parser.add_argument("--changelog", default="CHANGELOG.md")
    argument_parser.add_argument(
        "--hex-api-url", default=os.environ.get("HEX_API_URL", "https://hex.pm/api")
    )
    argument_parser.add_argument("--dry-run", action="store_true")
    argument_parser.add_argument("--hex-dry-run", action="store_true")
    argument_parser.add_argument("--has-hex-api-key", action="store_true")
    return argument_parser


def main() -> int:
    try:
        publish(parser().parse_args())
        return 0
    except PublishError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
