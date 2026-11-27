#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Contract tests for scripts/ci/git_clone_retry.py (issue 3392)."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - CompletedProcess is the mock return type only.
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "git_clone_retry", ROOT / "scripts/ci/git_clone_retry.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GitCloneRetryTests(unittest.TestCase):
    def result(self, status: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", "clone"], status)

    def test_second_attempt_returns_success(self) -> None:
        with mock.patch.object(
            MODULE.subprocess, "run", side_effect=[self.result(1), self.result(0)]
        ) as run:
            self.assertEqual(
                MODULE.main(["--attempts=3", "--delay=0", "--", "--depth=1", "url", "dest"]),
                0,
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0][1:],
            ["clone", "--depth=1", "url", "dest"],
        )

    def test_all_attempts_fail_with_the_last_status(self) -> None:
        with mock.patch.object(
            MODULE.subprocess, "run", side_effect=[self.result(1), self.result(2), self.result(3)]
        ) as run:
            self.assertEqual(
                MODULE.main(["--attempts=3", "--delay=0", "--", "url", "dest"]),
                3,
            )
        self.assertEqual(run.call_count, 3)

    def test_attempts_are_bounded(self) -> None:
        for value in ("0", "11", "not-a-number"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    MODULE.parse_args([f"--attempts={value}", "--", "url", "dest"])

    def test_timeout_is_bounded_and_returns_124(self) -> None:
        for value in ("0", "3601", "not-a-number"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    MODULE.parse_args([f"--timeout={value}", "--", "url", "dest"])
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["git", "clone"], 1),
        ) as run:
            self.assertEqual(
                MODULE.main(["--attempts=1", "--delay=0", "--timeout=1", "--", "url", "dest"]),
                124,
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 1)

    def test_timed_out_attempt_removes_the_destination_it_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "clone"
            attempts: list[object] = []

            def clone(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                attempts.append(args[0])
                if len(attempts) == 1:
                    destination.mkdir()
                    raise subprocess.TimeoutExpired(args[0], 1)
                return self.result(0)

            with mock.patch.object(MODULE.subprocess, "run", side_effect=clone):
                self.assertEqual(
                    MODULE.main(
                        [
                            "--attempts=2",
                            "--delay=0",
                            "--timeout=1",
                            "--",
                            "url",
                            str(destination),
                        ]
                    ),
                    0,
                )
            self.assertEqual(len(attempts), 2)
            self.assertFalse(destination.exists())

    def test_clone_arguments_need_no_separator(self) -> None:
        attempts, delay, timeout, arguments, config = MODULE.parse_args(
            ["--attempts", "4", "--branch", "v1.34.0", "url", "dest"]
        )
        self.assertEqual((attempts, delay, timeout), (4, 1, 600))
        self.assertEqual(arguments, ["--branch", "v1.34.0", "url", "dest"])
        self.assertEqual(config, [])

    def test_clone_arguments_are_preserved(self) -> None:
        attempts, delay, timeout, arguments, config = MODULE.parse_args(
            ["--attempts", "4", "--delay", "2", "--", "--filter=blob:none", "url", "dest"]
        )
        self.assertEqual((attempts, delay, timeout), (4, 2, 600))
        self.assertEqual(arguments, ["--filter=blob:none", "url", "dest"])
        self.assertEqual(config, [])

    def test_git_config_options_are_hoisted_in_front_of_clone(self) -> None:
        # The compat scripts write `git -c advice.detachedHead=false clone`;
        # the helper must keep those options where Git expects them (issue #3392).
        attempts, delay, timeout, arguments, config = MODULE.parse_args(
            ["-c", "advice.detachedHead=false", "clone", "--quiet", "url", "dest"]
        )
        self.assertEqual((attempts, delay, timeout), (3, 1, 600))
        self.assertEqual(arguments, ["--quiet", "url", "dest"])
        self.assertEqual(config, ["-c", "advice.detachedHead=false"])

    def test_worktree_option_is_hoisted_with_its_value(self) -> None:
        _, _, _, arguments, config = MODULE.parse_args(
            ["-C", "scratch/src", "clone", "--depth=1", "url"]
        )
        self.assertEqual(arguments, ["--depth=1", "url"])
        self.assertEqual(config, ["-C", "scratch/src"])

    def test_hoisted_options_repeat_and_interleave(self) -> None:
        _, _, _, arguments, config = MODULE.parse_args(
            ["-c", "a=b", "-C", "scratch/src", "-c", "c=d", "clone", "url"]
        )
        self.assertEqual(arguments, ["url"])
        self.assertEqual(config, ["-c", "a=b", "-C", "scratch/src", "-c", "c=d"])

    def test_trailing_option_without_a_value_is_a_usage_error(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.parse_args(["-c"])


if __name__ == "__main__":
    unittest.main()
