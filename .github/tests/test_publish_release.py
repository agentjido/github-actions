#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
import subprocess
import urllib.error
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


publisher = load_module("publish_release", ROOT / ".github/scripts/publish_release.py")


def release() -> dict[str, object]:
    return {"tag_name": "v1.1.0", "draft": False, "html_url": "https://example.test"}


class PublishReleaseTest(unittest.TestCase):
    def arguments(self, *, has_key: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            repository="owner/repo",
            package="fixture",
            version="1.1.0",
            tag="v1.1.0",
            notes_mode="changelog",
            changelog="CHANGELOG.md",
            hex_api_url="https://hex.example/api",
            dry_run=False,
            hex_dry_run=False,
            has_hex_api_key=has_key,
        )

    @mock.patch.object(publisher, "create_github_release")
    @mock.patch.object(publisher, "wait_for_hex")
    @mock.patch.object(publisher, "publish_hex")
    @mock.patch.object(publisher, "github_release")
    @mock.patch.object(publisher, "hex_release_exists")
    def test_hex_absent_and_release_absent_publishes_then_creates_release(
        self, hex_exists, github_release, publish_hex, wait_for_hex, create_release
    ) -> None:
        hex_exists.return_value = False
        github_release.side_effect = [None, release()]
        publisher.publish(self.arguments())
        publish_hex.assert_called_once_with()
        wait_for_hex.assert_called_once_with(
            "https://hex.example/api", "fixture", "1.1.0"
        )
        create_release.assert_called_once()

    @mock.patch.object(publisher, "create_github_release")
    @mock.patch.object(publisher, "publish_hex")
    @mock.patch.object(publisher, "github_release")
    @mock.patch.object(publisher, "hex_release_exists")
    def test_hex_present_and_release_absent_skips_upload_and_creates_release(
        self, hex_exists, github_release, publish_hex, create_release
    ) -> None:
        hex_exists.return_value = True
        github_release.side_effect = [None, release()]
        publisher.publish(self.arguments(has_key=False))
        publish_hex.assert_not_called()
        create_release.assert_called_once()

    @mock.patch.object(publisher, "create_github_release")
    @mock.patch.object(publisher, "publish_hex")
    @mock.patch.object(publisher, "github_release", return_value=release())
    @mock.patch.object(publisher, "hex_release_exists", return_value=True)
    def test_hex_present_and_release_present_is_noop(
        self, _hex_exists, _github_release, publish_hex, create_release
    ) -> None:
        publisher.publish(self.arguments(has_key=False))
        publish_hex.assert_not_called()
        create_release.assert_not_called()

    @mock.patch.object(publisher, "create_github_release")
    @mock.patch.object(publisher, "publish_hex")
    @mock.patch.object(publisher, "github_release", return_value=release())
    @mock.patch.object(publisher, "hex_release_exists", return_value=False)
    def test_release_present_and_hex_absent_is_inconsistent(
        self, _hex_exists, _github_release, publish_hex, create_release
    ) -> None:
        with self.assertRaises(publisher.PublishError):
            publisher.publish(self.arguments())
        publish_hex.assert_not_called()
        create_release.assert_not_called()

    @mock.patch.object(publisher, "github_release", return_value=None)
    @mock.patch.object(publisher, "hex_release_exists", return_value=False)
    def test_absent_hex_requires_token(self, _hex_exists, _github_release) -> None:
        with self.assertRaises(publisher.PublishError):
            publisher.publish(self.arguments(has_key=False))

    @mock.patch.object(publisher, "create_github_release")
    @mock.patch.object(publisher, "wait_for_hex")
    @mock.patch.object(
        publisher, "publish_hex", side_effect=publisher.PublishError("uncertain")
    )
    @mock.patch.object(publisher, "github_release", return_value=None)
    @mock.patch.object(publisher, "hex_release_exists", return_value=False)
    def test_uncertain_hex_publish_is_not_retried(
        self, _hex_exists, _github_release, publish_hex, wait_for_hex, create_release
    ) -> None:
        with self.assertRaises(publisher.PublishError):
            publisher.publish(self.arguments())
        publish_hex.assert_called_once_with()
        wait_for_hex.assert_not_called()
        create_release.assert_not_called()

    def test_fake_hex_http_present_and_absent_responses(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(publisher.urllib.request, "urlopen", return_value=response):
            self.assertTrue(
                publisher.hex_release_exists(
                    "https://hex.example/api", "fixture", "1.1.0"
                )
            )
        error = urllib.error.HTTPError(
            "https://hex.example", 404, "Not Found", {}, io.BytesIO()
        )
        with mock.patch.object(publisher.urllib.request, "urlopen", side_effect=error):
            self.assertFalse(
                publisher.hex_release_exists(
                    "https://hex.example/api", "fixture", "1.1.0"
                )
            )

    def test_fake_github_release_present_and_absent_responses(self) -> None:
        present = subprocess.CompletedProcess(
            ["gh"], 0, stdout='{"tag_name":"v1.1.0","draft":false}', stderr=""
        )
        with mock.patch.object(publisher, "run", return_value=present):
            self.assertIsNotNone(publisher.github_release("owner/repo", "v1.1.0"))
        absent = subprocess.CompletedProcess(
            ["gh"], 1, stdout="", stderr="gh: Not Found (HTTP 404)"
        )
        with mock.patch.object(publisher, "run", return_value=absent):
            self.assertIsNone(publisher.github_release("owner/repo", "v1.1.0"))


if __name__ == "__main__":
    unittest.main()
