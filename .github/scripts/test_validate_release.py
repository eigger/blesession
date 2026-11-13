"""Publishing must reject invalid tags, versions and source commits."""

from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import validate_release


class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        source = patch.object(Path, "read_text", return_value='__version__ = "1.2.3"')
        self.addCleanup(source.stop)
        source.start()

    def test_valid_release(self):
        with (
            patch.object(validate_release, "git", side_effect=["commit", "commit"]),
            patch.object(
                validate_release.subprocess, "run", return_value=CompletedProcess([], 0)
            ) as run,
        ):
            validate_release.main("v1.2.3")
        run.assert_called_once_with(["git", "merge-base", "--is-ancestor", "commit", "origin/main"])

    def test_invalid_tag(self):
        for tag in ("main", "v1.2.3rc1", "v1.2.3;echo unsafe", "v1.2.3\n"):
            with self.subTest(tag=tag), self.assertRaisesRegex(SystemExit, "Release tag must"):
                validate_release.main(tag)

    def test_version_mismatch(self):
        with self.assertRaisesRegex(SystemExit, "package version"):
            validate_release.main("v1.2.4")

    def test_checkout_must_match_tag(self):
        with (
            patch.object(validate_release, "git", side_effect=["tag-commit", "other-commit"]),
            self.assertRaisesRegex(SystemExit, "Checked-out commit"),
        ):
            validate_release.main("v1.2.3")

    def test_commit_must_be_on_main(self):
        with (
            patch.object(validate_release, "git", side_effect=["commit", "commit"]),
            patch.object(validate_release.subprocess, "run", return_value=CompletedProcess([], 1)),
            self.assertRaisesRegex(SystemExit, "not on main"),
        ):
            validate_release.main("v1.2.3")


if __name__ == "__main__":
    unittest.main()
