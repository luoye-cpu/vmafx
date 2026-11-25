#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Exercise scratch cleanup under Windows file deletion rules.

The Windows failure is planted with a mock: an ``os.unlink`` that raises
``PermissionError`` for the program a given number of times, as Windows does
while Defender still holds a program that has just run.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import os
import shutil
import stat
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.safe_subprocess import run as run_command  # noqa: E402
from scripts.lib.scratch_program import (  # noqa: E402
    ATTEMPTS,
    _retry_deletion,
    remove_file,
    remove_program,
    remove_tree,
)

REAL_UNLINK = os.unlink


def held_for(name: str, times: int) -> tuple[Callable[..., None], list[str]]:
    """An unlink that refuses files called ``name`` ``times`` times, then deletes."""
    calls: list[str] = []

    def unlink(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        base = Path(os.fspath(path)).name
        calls.append(base)
        if base == name and calls.count(base) <= times:
            raise PermissionError(13, "Access is denied", base)
        REAL_UNLINK(path, *args, **kwargs)

    return unlink, calls


class RemoveProgram(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)

    def program(self, name: str = "harness") -> Path:
        path = self.dir / name
        path.write_bytes(b"MZ")
        return path

    def test_a_program_held_a_few_times_is_removed(self) -> None:
        path = self.program()
        unlink, calls = held_for("harness", 3)
        remove_program(path, delay=0, unlink=unlink)
        self.assertFalse(path.exists())
        self.assertEqual(calls, ["harness"] * 4)

    def test_a_program_held_longer_than_the_bound_raises(self) -> None:
        path = self.program()
        unlink, calls = held_for("harness", ATTEMPTS)
        with self.assertRaises(PermissionError):
            remove_program(path, delay=0, unlink=unlink)
        self.assertEqual(len(calls), ATTEMPTS)
        self.assertTrue(path.exists())

    def test_another_error_is_not_retried(self) -> None:
        path = self.program()
        refuse = mock.Mock(side_effect=OSError(30, "Read-only file system"))
        with self.assertRaises(OSError):
            remove_program(path, delay=0, unlink=refuse)
        self.assertEqual(refuse.call_count, 1)

    def test_the_windows_spelling_is_removed(self) -> None:
        path = self.program("harness.exe")
        remove_program(self.dir / "harness", delay=0)
        self.assertFalse(path.exists())

    def test_a_missing_program_is_nothing_to_do(self) -> None:
        refuse = mock.Mock(side_effect=AssertionError("unlink called"))
        remove_program(self.dir / "never-built", delay=0, unlink=refuse)
        refuse.assert_not_called()

    def test_a_bound_below_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            remove_program(self.program(), attempts=0)


def windows_rmtree() -> contextlib.AbstractContextManager[object]:
    """Make ``shutil.rmtree`` take the path it takes on Windows.

    On Linux ``rmtree`` walks with directory file descriptors, and
    ``tempfile``'s error handler then retries a refused file until it goes.
    Windows has no such functions: ``rmtree`` unlinks by path, and the
    handler tries once more, then raises. That is the CI failure.
    """
    internals = vars(shutil)  # private names typeshed does not declare
    if "_rmtree_impl" in internals:  # Python 3.14
        return mock.patch.object(shutil, "_rmtree_impl", internals["_rmtree_unsafe"])
    return mock.patch.object(shutil, "_use_fd_functions", False)


class TemporaryDirectoryCleanup(unittest.TestCase):
    """The CI failure, reproduced on any host, and the order that avoids it."""

    def test_cleanup_alone_fails_on_a_held_program(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        (Path(scratch.name) / "harness").write_bytes(b"MZ")
        unlink, _ = held_for("harness", 2)
        with mock.patch("os.unlink", unlink), windows_rmtree():
            with self.assertRaises(PermissionError):
                scratch.cleanup()
        scratch.cleanup()

    def test_removing_the_program_first_lets_cleanup_finish(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        (Path(scratch.name) / "harness").write_bytes(b"MZ")
        (Path(scratch.name) / "main.cpp").write_text("int main() {}\n")
        unlink, calls = held_for("harness", 2)
        with mock.patch("os.unlink", unlink), windows_rmtree():
            remove_program(Path(scratch.name) / "harness", delay=0)
            scratch.cleanup()
        self.assertFalse(Path(scratch.name).exists())
        self.assertEqual(calls.count("harness"), 3)


class ReadOnlyTreeRemoval(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tree = Path(scratch.name) / "repo" / ".git"
        self.pack = self.tree / "objects" / "pack" / "pack-1.pack"
        self.loose = self.tree / "objects" / "2f" / "b242"
        for path in (self.pack, self.loose):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"object")
            path.chmod(0o444)
        (self.tree / "HEAD").write_text("ref: refs/heads/master\n", encoding="ascii")
        self.enterContext(windows_rmtree())

    @staticmethod
    def windows_unlink(path: str) -> None:
        if not Path(path).stat().st_mode & stat.S_IWUSR:
            raise PermissionError(13, "Access is denied", path)
        REAL_UNLINK(path)

    def test_plain_rmtree_fails_on_a_read_only_object(self) -> None:
        """Negative: plain rmtree fails on a read-only Git object on Windows."""
        with (
            mock.patch("os.unlink", self.windows_unlink),
            self.assertRaises(PermissionError) as exc,
        ):
            shutil.rmtree(self.tree)
        self.assertIn(Path(exc.exception.filename), (self.pack, self.loose))

    def test_remove_tree_clears_the_read_only_bit(self) -> None:
        """Positive: remove both read-only objects and the writable file."""
        with mock.patch("os.unlink", self.windows_unlink):
            remove_tree(self.tree)
        self.assertFalse(self.tree.exists())

    def test_a_file_that_stays_refused_is_raised(self) -> None:
        """Boundary: a second permission failure reaches the caller."""
        refused = mock.Mock(side_effect=PermissionError(13, "Access is denied", str(self.pack)))

        def unlink(path: str) -> None:
            if Path(path) == self.pack:
                refused(path)
            self.windows_unlink(path)

        with mock.patch("os.unlink", unlink), self.assertRaises(PermissionError) as exc:
            remove_tree(self.tree)
        self.assertEqual(Path(exc.exception.filename), self.pack)
        self.assertEqual(refused.call_count, 2)

    def test_non_deletion_permission_errors_are_raised(self) -> None:
        """Negative: only deletion callbacks may chmod and retry."""
        for function in (os.lstat, os.open, os.scandir, os.path.islink, os.close):
            with self.subTest(function=function.__name__), mock.patch("os.chmod") as chmod:
                refused = PermissionError(errno.EACCES, "Access is denied", str(self.tree))
                with self.assertRaises(PermissionError) as exc:
                    _retry_deletion(function, str(self.tree), refused)
                self.assertIs(exc.exception, refused)
                chmod.assert_not_called()

    def test_refused_root_lstat_reaches_the_caller(self) -> None:
        """Negative: a refused root lstat stops the walk before deletion."""
        real_lstat = os.lstat
        refused = PermissionError(errno.EACCES, "Access is denied", str(self.tree))
        refused_once = False

        def lstat(path: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
            nonlocal refused_once
            if Path(path) == self.tree and not refused_once:
                refused_once = True
                raise refused
            return real_lstat(path, *args, **kwargs)

        with mock.patch("os.lstat", lstat), mock.patch("os.chmod") as chmod:
            with self.assertRaises(PermissionError) as exc:
                remove_tree(self.tree)
        self.assertIs(exc.exception, refused)
        self.assertTrue(self.tree.is_dir())
        chmod.assert_not_called()

    def test_child_vanishing_during_unlink_allows_removal(self) -> None:
        """Boundary: a child gone before unlink completes does not block removal."""
        self.loose.chmod(stat.S_IREAD | stat.S_IWRITE)

        def unlink(path: str) -> None:
            if Path(path) == self.loose:
                REAL_UNLINK(path)
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
            self.windows_unlink(path)

        with mock.patch("os.unlink", unlink):
            remove_tree(self.tree)
        self.assertFalse(self.tree.exists())

    def test_rmtree_leaving_the_tree_raises_enotempty(self) -> None:
        """Boundary: an incomplete walk names the remaining root."""
        with mock.patch.object(shutil, "rmtree"), self.assertRaises(OSError) as exc:
            remove_tree(self.tree)
        self.assertEqual(exc.exception.errno, errno.ENOTEMPTY)
        self.assertEqual(exc.exception.filename, str(self.tree))

    def test_missing_tree_raises(self) -> None:
        """Boundary: a missing root reaches the caller."""
        with self.assertRaises(FileNotFoundError):
            remove_tree(self.tree / "absent")

    def test_other_errors_are_not_retried(self) -> None:
        """Boundary: a transient busy error still reaches the caller on its first occurrence."""
        busy = OSError(errno.EBUSY, "Device or resource busy", str(self.pack))
        attempts = mock.Mock(side_effect=[busy, None])

        def unlink(path: str) -> None:
            if Path(path) == self.pack:
                attempts(path)
            self.windows_unlink(path)

        with mock.patch("os.unlink", unlink), self.assertRaises(OSError) as exc:
            remove_tree(self.tree)
        self.assertIs(exc.exception, busy)
        attempts.assert_called_once_with(str(self.pack))

    def test_remove_file_clears_the_read_only_bit(self) -> None:
        """Positive: remove the read-only pack and leave the loose object in place."""
        with mock.patch("os.unlink", self.windows_unlink):
            remove_file(self.pack)
        self.assertFalse(self.pack.exists())
        self.assertTrue(self.loose.is_file())

    def test_plain_unlink_fails_on_a_read_only_object(self) -> None:
        """Negative: Windows refuses a plain unlink of the read-only pack."""
        with mock.patch("os.unlink", self.windows_unlink), self.assertRaises(PermissionError):
            self.pack.unlink()

    def test_remove_file_raises_the_second_permission_error(self) -> None:
        """Boundary: a file refused twice raises the second permission error."""
        still_refused = PermissionError(errno.EACCES, "Access is denied", str(self.pack))
        with (
            mock.patch("os.unlink", side_effect=[PermissionError(), still_refused]) as unlink,
            self.assertRaises(PermissionError) as exc,
        ):
            remove_file(self.pack)
        self.assertIs(exc.exception, still_refused)
        self.assertEqual(unlink.call_args_list, [mock.call(self.pack), mock.call(self.pack)])

    def test_remove_file_raises_busy_on_first_attempt(self) -> None:
        """Boundary: the first busy error reaches the caller without a retry."""
        busy = OSError(errno.EBUSY, "Device or resource busy", str(self.pack))
        with (
            mock.patch("os.unlink", side_effect=busy) as unlink,
            self.assertRaises(OSError) as exc,
        ):
            remove_file(self.pack)
        self.assertIs(exc.exception, busy)
        unlink.assert_called_once_with(self.pack)

    def test_remove_file_raises_for_a_missing_file(self) -> None:
        """Boundary: a missing file raises the first unlink error without chmod."""
        path = self.pack.with_name("missing.pack")
        missing = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))
        with (
            mock.patch("os.unlink", side_effect=missing) as unlink,
            mock.patch("os.chmod") as chmod,
            self.assertRaises(FileNotFoundError) as exc,
        ):
            remove_file(path)
        self.assertIs(exc.exception, missing)
        unlink.assert_called_once_with(path)
        chmod.assert_not_called()


def bare_git_rmtree_calls(source: str, name: str) -> list[str]:
    """Report rmtree calls with .git, *.git or objects literals in the first argument or path=."""
    findings = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if not isinstance(node, ast.Call):
            continue
        match node.func:
            case ast.Name(id="rmtree") | ast.Attribute(value=ast.Name(id="shutil"), attr="rmtree"):
                paths = node.args[:1] + [kw.value for kw in node.keywords if kw.arg == "path"]
                if any(
                    isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and (n.value.endswith(".git") or n.value == "objects")
                    for path in paths
                    for n in ast.walk(path)
                ):
                    findings.append(f"{name}:{node.lineno}")
    return findings


def tracked_python_files() -> list[str]:
    tracked = run_command(
        ["git", "ls-files", "-z", "--", "*.py"],
        allowed_executables=("git",),
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        cwd=ROOT,
        capture_output=True,
        max_output_bytes=1024 * 1024,
        check=True,
    )
    return [os.fsdecode(entry) for entry in tracked.stdout.split(b"\0") if entry]


class GitDirectoryRemovalScan(unittest.TestCase):
    def test_listing_ignores_an_inherited_git_index(self) -> None:
        """Negative: a foreign Git index and directory cannot redirect the listing."""
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index"
            index.touch()
            with mock.patch.dict(
                os.environ, {"GIT_INDEX_FILE": str(index), "GIT_DIR": str(Path(tmp) / "missing")}
            ):
                self.assertIn("scripts/lib/test_scratch_program.py", tracked_python_files())

    def test_no_bare_rmtree_of_a_git_directory(self) -> None:
        """Positive: tracked Python files use read-only-aware cleanup for literal Git paths."""
        findings = []
        for name in tracked_python_files():
            findings.extend(bare_git_rmtree_calls((ROOT / name).read_text(encoding="utf-8"), name))
        self.assertFalse(findings, "\n".join(findings))

    def test_the_scan_flags_a_planted_call(self) -> None:
        """Negative and boundary: flag both rmtree spellings and leave unrelated calls alone."""
        source = """shutil.rmtree(repo / ".git")
remove_tree(repo / ".git")
shutil.rmtree(tmp, ignore_errors=True)
rmtree(
    repo / '.git')
shutil.rmtree(path=repo / ".git")
shutil.rmtree(cache / "source.git")
shutil.rmtree(cache / "objects")
shutil.rmtree(tmp / "build")
"""
        self.assertEqual(
            bare_git_rmtree_calls(source, "plant.py"),
            ["plant.py:1", "plant.py:4", "plant.py:6", "plant.py:7", "plant.py:8"],
        )


if __name__ == "__main__":
    unittest.main()
