#!/usr/bin/env python3
# Copyright 2026 luoye-cpu
# SPDX-License-Identifier: EUPL-1.2
"""The lefthook commit-msg stage validates the message through the framework.

Issue #3638: lefthook.yml declares commit-msg and framework-hooks.sh runs the
framework's commit-msg stage on the message file Git handed over, the same
hook-impl entry the ADR-1241 dispatcher uses. These tests plant a
subject-convention hook as a stand-in for conventional-pre-commit (which would
need its repository cloned) and drive the real pre-commit through
framework-hooks.sh: a non-conventional subject must refuse the message and a
conventional subject must pass.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.lib.safe_subprocess import TextCommandResult
from scripts.lib.safe_subprocess import run as run_command

ROOT = Path(__file__).resolve().parents[3]
HOOK_CONFIG = """repos:
  - repo: local
    hooks:
      - id: subject-convention
        name: subject follows Conventional Commits
        entry: bash .hooks/check-subject.sh
        language: system
        stages: [commit-msg]
        always_run: true
"""
# A stand-in for conventional-pre-commit: same input (the message file Git
# handed to the stage), same verdict shape (nonzero plus a named complaint).
CHECK_SUBJECT = """#!/bin/sh
subject=$(sed -n '1p' "$1")
case "$subject" in
  feat*|fix*|perf*|refactor*|docs*|test*|build*|ci*|chore*|revert*|port*|sycl*|cuda*|simd*)
    exit 0
    ;;
  *)
    echo "not a Conventional Commit subject: $subject" >&2
    exit 1
    ;;
esac
"""
TOOLS = ("git", "bash", "pre-commit")


class CommitMsgHookTests(unittest.TestCase):
    def setUp(self) -> None:
        missing = [tool for tool in TOOLS if shutil.which(tool) is None]
        if missing:
            self.skipTest(f"needs {', '.join(missing)} on PATH; the contract is not exercised")
        self.temporary = tempfile.TemporaryDirectory(prefix="vmafx-commit-msg-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.bash = shutil.which("bash") or "/bin/bash"
        self.git = shutil.which("git") or "/usr/bin/git"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "PRE_COMMIT_"))
        }
        self.env.pop("SKIP", None)
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            PRE_COMMIT_HOME=str(self.repo / ".pre-commit-home"),
        )
        self.git_run("init", "-b", "master")
        (self.repo / ".hooks").mkdir()
        (self.repo / ".hooks/check-subject.sh").write_text(CHECK_SUBJECT, newline="\n")
        (self.repo / ".pre-commit-config.yaml").write_text(HOOK_CONFIG, newline="\n")
        self.git_run("add", ".")
        self.git_run(
            "-c", "user.name=Hook Test", "-c", "user.email=hook-test@example.invalid",
            "commit", "-m", "test: fixture",
        )  # fmt: skip

    def git_run(self, *args: str, check: bool = True) -> TextCommandResult:
        executable = self.git
        result = run_command(
            (executable, *args),
            allowed_executables=(executable,),
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            timeout_seconds=300,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def stage_message(self, subject: str) -> None:
        (self.repo / ".git" / "COMMIT_EDITMSG").write_text(subject + "\n", newline="\n")

    def run_commit_msg_stage(self) -> TextCommandResult:
        return run_command(
            (
                self.bash,
                str(ROOT / "scripts/git-hooks/framework-hooks.sh"),
                "commit-msg",
                ".git/COMMIT_EDITMSG",
            ),
            allowed_executables=(self.bash,),
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            timeout_seconds=300,
        )

    def test_non_conventional_subject_is_refused(self) -> None:
        self.stage_message("update stuff")
        result = self.run_commit_msg_stage()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        # pre-commit reports the failed hook's output on its own stdout.
        self.assertIn("not a Conventional Commit subject", result.stdout + result.stderr)

    def test_conventional_subject_passes(self) -> None:
        self.stage_message("ci: add the lefthook commit-msg stage")
        result = self.run_commit_msg_stage()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
