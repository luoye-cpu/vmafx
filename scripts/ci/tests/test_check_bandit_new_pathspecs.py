#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Pathspec filtering and full-tree default tests for new_scanner_findings.revision_modes."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.ci.tests.scanner_test_support import isolate_pre_commit_environment
from scripts.lib import git_env
from scripts.lib import new_scanner_findings as shared
from scripts.lib.safe_subprocess import run as run_command


class CheckRevisionModesPathspecs(unittest.TestCase):
    def setUp(self) -> None:
        """Create an isolated repository with files across multiple directories and modes."""
        isolate_pre_commit_environment(self)
        git = shutil.which("git")
        if git is None:
            raise unittest.SkipTest("git is not on PATH")
        self.git_bin = git
        temp = tempfile.TemporaryDirectory(prefix="vmafx-pathspec-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self._git("init", "-q", "-b", "master")
        for rel, content in (
            ("README.md", "root readme\n"),
            ("docs/guide.md", "doc text\n"),
            ("scripts/tool.sh", "#!/bin/sh\nexit 0\n"),
            ("core/src/lib.c", "int x = 0;\n"),
        ):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        self._git("add", ".")
        self._git("update-index", "--chmod=+x", "scripts/tool.sh")
        link_oid = self._git("hash-object", "-w", "--stdin", input_text="guide.md").strip()
        self._git("update-index", "--add", "--cacheinfo", f"120000,{link_oid},docs/link.md")
        self._git("commit", "-q", "-m", "initial")

    def _git(self, *args: str, input_text: str | None = None) -> str:
        """Run an isolated git command inside ``self.root`` and return stdout."""
        env = {
            **git_env.drop(),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        proc = run_command(
            [
                self.git_bin,
                "-c",
                "commit.gpgsign=false",
                "-c",
                f"core.hooksPath={self.root / 'no-hooks'}",
                *args,
            ],
            allowed_executables=(self.git_bin,),
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout_seconds=60,
            input_data=input_text,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_default_keeps_whole_tree(self) -> None:
        """Omitting pathspecs (or passing None) returns modes for the entire tree."""
        expected = {
            "README.md": "100644",
            "core/src/lib.c": "100644",
            "docs/guide.md": "100644",
            "docs/link.md": "120000",
            "scripts/tool.sh": "100755",
        }
        self.assertEqual(shared.revision_modes(self.git_bin, self.root, "HEAD"), expected)
        self.assertEqual(
            shared.revision_modes(self.git_bin, self.root, "HEAD", pathspecs=None), expected
        )

    def test_pathspecs_limit_records_from_tree_root(self) -> None:
        """Passing pathspecs limits entries and uses --full-tree even from a subdirectory."""
        subdir = self.root / "core" / "src"
        selected = shared.revision_modes(
            self.git_bin,
            subdir,
            "HEAD",
            pathspecs=("docs", "scripts/tool.sh", "absent.txt"),
        )
        self.assertEqual(
            selected,
            {
                "docs/guide.md": "100644",
                "docs/link.md": "120000",
                "scripts/tool.sh": "100755",
            },
        )
        entries = shared.revision_entries(self.git_bin, subdir, "HEAD", pathspecs=("README.md",))
        self.assertEqual(list(entries), ["README.md"])
        mode, oid = entries["README.md"]
        self.assertEqual(mode, "100644")
        self.assertEqual(oid, self._git("rev-parse", "HEAD:README.md").strip())

    def test_empty_and_invalid_pathspec_boundaries(self) -> None:
        """An empty pathspec tuple returns empty on valid refs; bad refs or blank specs fail."""
        self.assertEqual(
            shared.revision_modes(self.git_bin, self.root, "HEAD", pathspecs=()),
            {},
        )
        with self.assertRaises(shared.TreeListingError):
            shared.revision_modes(self.git_bin, self.root, "deadbeef" * 5, pathspecs=("docs",))
        with self.assertRaises(shared.TreeListingError):
            shared.revision_modes(self.git_bin, self.root, "deadbeef" * 5, pathspecs=())
        with self.assertRaises(shared.ToolError):
            shared.revision_modes(self.git_bin, self.root, "HEAD", pathspecs=("",))


if __name__ == "__main__":
    unittest.main()
