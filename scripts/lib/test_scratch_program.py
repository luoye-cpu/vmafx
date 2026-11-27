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
from collections.abc import Callable, Iterator
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any
from unittest import mock

if sys.platform == "win32":
    import _winapi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.safe_subprocess import run as run_command  # noqa: E402
from scripts.lib.scratch_program import (  # noqa: E402
    ATTEMPTS,
    path_absent,
    remove_file,
    remove_program,
    remove_tree,
    walk_tree,
)

REAL_UNLINK = os.unlink
REAL_RMDIR = os.rmdir
MODULE = "scripts.lib.scratch_program"
MAX_SOURCE_BYTES = 1024 * 1024
MAX_AST_NODES = 100_000
SOURCE_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


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


def scratch_chmod(path: Path, mode: int, *, follow_symlinks: bool) -> None:
    if follow_symlinks or path.is_symlink():
        raise AssertionError(f"refusing to follow a link at {path}")
    path.chmod(mode)


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


class BoundedTreeWalk(unittest.TestCase):
    LIMIT = 8

    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tree = root / "tree"
        self.tree.mkdir()
        self.outside = root / "outside"
        self.outside.mkdir()
        self.marker = self.outside / "keep"
        self.marker.touch()
        self.enterContext(mock.patch(f"{MODULE}.MAX_TREE_ENTRIES", self.LIMIT))

    def test_walk_counts_entries_across_directories(self) -> None:
        child = self.tree / "child"
        child.mkdir()
        paths = [child / str(i) for i in range(self.LIMIT - 1)]
        for path in paths:
            path.touch()
        self.assertCountEqual(
            (Path(entry.path) for entry in walk_tree(self.tree, self.LIMIT)), paths
        )
        extra = self.tree / "extra"
        extra.touch()
        with self.assertRaises(OSError) as exc:
            walk_tree(self.tree, self.LIMIT)
        self.assertEqual(str(exc.exception), f"tree at {self.tree} exceeds {self.LIMIT} entries")
        self.assertTrue(all(path.is_file() for path in [*paths, extra]))

    def test_walk_bounds_pulls_before_using_entry(self) -> None:
        pulled = 0
        leaf = mock.Mock(spec=os.DirEntry)
        leaf.is_symlink.return_value = True
        overflow = mock.Mock(spec=os.DirEntry)
        overflow.is_symlink.side_effect = AssertionError("used over-limit entry")

        def entries() -> Iterator[mock.Mock]:
            nonlocal pulled
            for index in range(self.LIMIT + 50):
                pulled += 1
                yield leaf if index < self.LIMIT else overflow

        with (
            mock.patch(f"{MODULE}.os.scandir", return_value=contextlib.nullcontext(entries())),
            self.assertRaisesRegex(OSError, rf"exceeds {self.LIMIT} entries$"),
        ):
            walk_tree(self.tree, self.LIMIT)
        self.assertLessEqual(pulled, self.LIMIT + 1)
        self.assertEqual(overflow.mock_calls, [])

    def test_remove_tree_preserves_every_over_limit_entry(self) -> None:
        paths = [self.tree / str(i) for i in range(self.LIMIT + 1)]
        for path in paths:
            path.touch()
        with self.assertRaisesRegex(OSError, rf"exceeds {self.LIMIT} entries$"):
            remove_tree(self.tree)
        self.assertTrue(all(path.is_file() for path in paths))

    def assert_linked_root_refused(self, link: Path) -> None:
        with mock.patch(f"{MODULE}.os.scandir") as scan:
            for operation in (lambda: walk_tree(link, self.LIMIT), lambda: remove_tree(link)):
                with self.assertRaises(OSError) as exc:
                    operation()
                self.assertEqual(str(exc.exception), f"refusing to walk linked root: {link}")
            scan.assert_not_called()
        self.assertTrue(self.marker.is_file())

    def assert_link_entry_removed(self, link: Path) -> None:
        with mock.patch(f"{MODULE}.os.scandir", wraps=os.scandir) as scan:
            leaves = walk_tree(self.tree, self.LIMIT)
        scan.assert_called_once_with(self.tree)
        self.assertEqual([Path(entry.path) for entry in leaves], [link])
        remove_tree(self.tree)
        self.assertTrue(path_absent(self.tree))
        self.assertTrue(self.marker.is_file())

    def test_directory_symlink_as_root_and_entry(self) -> None:
        link = self.tree / "link"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create a directory symlink: {error}")
        self.assert_linked_root_refused(link)
        self.assert_link_entry_removed(link)

    def test_junction_entry_is_never_scanned(self) -> None:
        junction = mock.Mock(spec=os.DirEntry)
        junction.path = str(self.tree / "junction")
        junction.is_symlink.return_value = False
        junction.is_junction.return_value = True
        junction.is_dir.return_value = True
        with mock.patch(
            f"{MODULE}.os.scandir", side_effect=[contextlib.nullcontext(iter([junction]))]
        ) as scan:
            self.assertEqual(walk_tree(self.tree, self.LIMIT), [junction])
        scan.assert_called_once_with(self.tree)

    @unittest.skipUnless(sys.platform == "win32", "requires a Windows junction")
    def test_windows_junction_as_root_and_entry(self) -> None:
        link = self.tree / "junction"
        if sys.platform == "win32":
            _winapi.CreateJunction(str(self.outside), str(link))
        self.assert_linked_root_refused(link)
        self.assert_link_entry_removed(link)


class PosixTreeRemoval(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "requires the native POSIX rmtree walker")
    def test_preserves_unlink_refusal_and_its_filename(self) -> None:
        tree = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tree / "object").touch()
        marker = "filename from unlink"
        refused = PermissionError(errno.EACCES, "unlink refused", marker)
        with (
            mock.patch(f"{MODULE}._RETRY_READ_ONLY", False),
            mock.patch("os.unlink", side_effect=refused),
            self.assertRaises(PermissionError) as exc,
        ):
            remove_tree(tree)
        self.assertIs(exc.exception, refused)
        self.assertEqual(exc.exception.filename, marker)


class ReadOnlyTreeRemoval(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.enterContext(mock.patch(f"{MODULE}._RETRY_READ_ONLY", True))
        self.enterContext(windows_rmtree())
        self.chmod = self.enterContext(mock.patch(f"{MODULE}._chmod", side_effect=scratch_chmod))
        self.tree = Path(scratch.name) / "repo" / ".git"
        self.pack = self.tree / "objects" / "pack" / "pack-1.pack"
        self.loose = self.tree / "objects" / "2f" / "b242"
        for path in (self.pack, self.loose):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"object")
            path.chmod(0o444)
        (self.tree / "HEAD").write_text("ref: refs/heads/master\n", encoding="ascii")

    @staticmethod
    def windows_unlink(path: str) -> None:
        if not Path(path).lstat().st_mode & stat.S_IWUSR:
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
        directory = self.tree / "empty"
        directory.mkdir()
        directory.chmod(0o555)
        directory_mode = stat.S_IMODE(directory.stat().st_mode) | stat.S_IWUSR

        def rmdir(path: str) -> None:
            if not Path(path).lstat().st_mode & stat.S_IWUSR:
                raise PermissionError(errno.EACCES, "read-only directory", path)
            REAL_RMDIR(path)

        mode = stat.S_IMODE(self.pack.stat().st_mode) | stat.S_IWUSR
        with (
            mock.patch("os.rmdir", rmdir),
            mock.patch("os.unlink", self.windows_unlink),
        ):
            remove_tree(self.tree)
        self.chmod.assert_has_calls(
            [mock.call(p, mode, follow_symlinks=False) for p in (self.pack, self.loose)],
            any_order=True,
        )
        self.assertIn(
            mock.call(directory, directory_mode, follow_symlinks=False), self.chmod.call_args_list
        )
        self.assertEqual(self.chmod.call_count, 3)
        self.assertFalse(self.tree.exists())

    def test_remove_tree_raises_the_first_permission_error(self) -> None:
        first = PermissionError(errno.EACCES, "first refusal", str(self.pack))
        second = PermissionError(errno.EACCES, "second refusal", "other name")
        with (
            mock.patch("os.unlink", side_effect=[first, second]) as unlink,
            self.assertRaises(PermissionError) as exc,
        ):
            remove_tree(self.pack.parent)
        self.assertIs(exc.exception, first)
        self.assertEqual(first.filename, str(self.pack))
        self.assertIn(str(second), first.__notes__[0])
        self.assertIn("deletion", first.__notes__[0])
        self.assertEqual(unlink.call_count, 2)

    def test_non_deletion_permission_errors_are_raised(self) -> None:
        """Negative: only deletion callbacks may chmod and retry."""
        refused = PermissionError(errno.EACCES, "Access is denied", str(self.tree))

        def fail_walk(path: Path, *, onexc: Callable[..., None]) -> None:
            onexc(function, str(path), refused)

        for function in (os.lstat, os.open, os.scandir, os.path.islink, os.close):
            with self.subTest(function=function.__name__):
                with (
                    mock.patch.object(shutil, "rmtree", fail_walk),
                    self.assertRaises(PermissionError) as exc,
                ):
                    remove_tree(self.tree)
                self.assertIs(exc.exception, refused)
                self.chmod.assert_not_called()

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

        with mock.patch("os.lstat", lstat):
            with self.assertRaises(PermissionError) as exc:
                remove_tree(self.tree)
        self.assertIs(exc.exception, refused)
        self.assertTrue(self.tree.is_dir())
        self.chmod.assert_not_called()

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
        missing = self.tree / "absent"
        with self.assertRaises(FileNotFoundError) as exc:
            remove_tree(missing)
        self.assertEqual(Path(exc.exception.filename), missing)

    def test_postcondition_propagates_lstat_eacces(self) -> None:
        refused = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self.tree))
        with contextlib.ExitStack() as patches:

            def finished(*args: Any, **kwargs: Any) -> None:
                patches.enter_context(mock.patch("os.lstat", side_effect=refused))

            patches.enter_context(mock.patch.object(shutil, "rmtree", finished))
            with self.assertRaises(PermissionError) as exc:
                remove_tree(self.tree)
        self.assertIs(exc.exception, refused)

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
        mode = stat.S_IMODE(self.pack.stat().st_mode) | stat.S_IWUSR
        with (mock.patch("os.unlink", self.windows_unlink),):
            remove_file(self.pack)
        self.chmod.assert_called_once_with(self.pack, mode, follow_symlinks=False)
        self.assertFalse(self.pack.exists())
        self.assertTrue(self.loose.is_file())

    def assert_refused_symlink(self, remove: Callable[[Path], None]) -> None:
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        outside = scratch / "outside.txt"
        outside.touch()
        outside.chmod(0o444)
        link = scratch / "tree" / "sub" / "link"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"cannot create a symlink: {error}")
        refused = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(link))
        attempts = 0

        def unlink(path: str | Path, *, dir_fd: int | None = None) -> None:
            nonlocal attempts
            if Path(path).name == link.name:
                attempts += 1
                raise refused
            REAL_UNLINK(path, dir_fd=dir_fd)

        with (
            mock.patch("os.unlink", unlink),
            self.assertRaises(PermissionError) as exc,
        ):
            remove(link)
        self.chmod.assert_not_called()
        self.assertIs(exc.exception, refused)
        self.assertEqual(exc.exception.filename, str(link))
        self.assertFalse(outside.stat().st_mode & stat.S_IWUSR)
        self.assertEqual(attempts, 1)

    def test_remove_tree_preserves_refused_symlink(self) -> None:
        self.assert_refused_symlink(lambda link: remove_tree(link.parent.parent))

    def test_remove_file_preserves_refused_symlink(self) -> None:
        self.assert_refused_symlink(remove_file)

    def test_hard_link_keeps_outside_file_read_only(self) -> None:
        outside = self.tree.parent / "outside"
        try:
            os.link(self.pack, outside)
        except OSError as error:
            self.skipTest(f"cannot create a hard link: {error}")
        for remove, target in ((remove_file, self.pack), (remove_tree, self.pack.parent)):
            with self.subTest(remove=remove.__name__):
                first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
                with (
                    mock.patch("os.unlink", side_effect=first) as unlink,
                    self.assertRaises(PermissionError) as exc,
                ):
                    remove(target)
                self.assertIs(exc.exception, first)
                self.assertFalse(outside.stat().st_mode & stat.S_IWUSR)
                self.chmod.assert_not_called()
                unlink.assert_called_once()

    def test_reparse_writable_and_special_entries_keep_their_refusal(self) -> None:
        for mode, attributes in (
            (stat.S_IFDIR | 0o555, stat.FILE_ATTRIBUTE_REPARSE_POINT),
            (stat.S_IFREG | 0o644, 0),
            (stat.S_IFIFO | 0o444, 0),
        ):
            first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
            st = mock.Mock(st_mode=mode, st_file_attributes=attributes, st_nlink=1)
            with (
                self.subTest(mode=mode),
                mock.patch("os.lstat", return_value=st),
                mock.patch("os.unlink", side_effect=first) as unlink,
                self.assertRaises(PermissionError) as exc,
            ):
                remove_file(self.pack)
            self.assertIs(exc.exception, first)
            self.chmod.assert_not_called()
            unlink.assert_called_once()

    def test_gate_off_preserves_refusal(self) -> None:
        for remove, target in ((remove_file, self.pack), (remove_tree, self.pack.parent)):
            first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
            with (
                self.subTest(remove=remove.__name__),
                mock.patch(f"{MODULE}._RETRY_READ_ONLY", False),
                mock.patch("os.unlink", side_effect=first) as unlink,
                self.assertRaises(PermissionError) as exc,
            ):
                remove(target)
            self.assertIs(exc.exception, first)
            self.chmod.assert_not_called()
            unlink.assert_called_once()

    def test_chmod_keyboard_interrupt_propagates_unchanged(self) -> None:
        for remove, target in ((remove_file, self.pack), (remove_tree, self.pack.parent)):
            stop = KeyboardInterrupt()
            with (
                self.subTest(remove=remove.__name__),
                mock.patch("os.unlink", side_effect=PermissionError()),
                mock.patch(f"{MODULE}._chmod", side_effect=stop),
                self.assertRaises(KeyboardInterrupt) as exc,
            ):
                remove(target)
            self.assertIs(exc.exception, stop)

    def test_walk_visits_siblings_after_refusal(self) -> None:
        root = self.tree / "siblings"
        root.mkdir()
        for name in ("left", "right", "removable"):
            (root / name).touch()
        refusals: list[PermissionError] = []

        def unlink(path: str) -> None:
            if Path(path).name == "removable":
                REAL_UNLINK(path)
                return
            error = PermissionError(errno.EACCES, "refused sibling", path)
            refusals.append(error)
            raise error

        with mock.patch("os.unlink", unlink), self.assertRaises(PermissionError) as exc:
            remove_tree(root)
        self.assertEqual({Path(e.filename).name for e in refusals}, {"left", "right"})
        self.assertIs(exc.exception, refusals[0])
        self.assertIn(str(refusals[1]), exc.exception.__notes__[0])
        self.assertFalse((root / "removable").exists())

    def test_later_error_notes_are_capped(self) -> None:
        def check(later: int, closing: list[str]) -> None:
            errors = [OSError(f"failure {i}") for i in range(later + 1)]

            def walk(path: Path, *, onexc: Callable[..., None]) -> None:
                for error in errors:
                    onexc(os.scandir, str(path), error)

            with mock.patch.object(shutil, "rmtree", walk), self.assertRaises(OSError) as exc:
                remove_tree(self.tree)
            self.assertIs(exc.exception, errors[0])
            expected = [f"{self.tree}: {error}" for error in errors[1:33]]
            self.assertEqual(exc.exception.__notes__, expected + closing)

        check(32, [])
        check(33, ["1 additional error(s) omitted"])

    def test_path_absent_counts_a_dangling_symlink_as_present(self) -> None:
        target = self.tree / "missing"
        self.assertTrue(path_absent(target))
        link = self.tree / "link"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"cannot create a symlink: {error}")
        self.assertFalse(path_absent(link))

    def test_failed_retry_lstat_preserves_unlink_refusal(self) -> None:
        def check(remove: Callable[[Path], None], target: Path) -> None:
            first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
            failed = PermissionError(errno.EACCES, "lstat refused", str(self.pack))

            def unlink(path: str | Path) -> None:
                stack.enter_context(mock.patch("os.lstat", side_effect=failed))
                raise first

            with (
                contextlib.ExitStack() as stack,
                mock.patch("os.unlink", unlink),
                self.assertRaises(PermissionError) as exc,
            ):
                remove(target)
            self.assertIs(exc.exception, first)
            self.assertIn("retry lstat", first.__notes__[0])
            self.assertIn(str(failed), first.__notes__[0])

        check(remove_file, self.pack)
        check(remove_tree, self.pack.parent)

    def test_plain_unlink_fails_on_a_read_only_object(self) -> None:
        """Negative: Windows refuses a plain unlink of the read-only pack."""
        with mock.patch("os.unlink", self.windows_unlink), self.assertRaises(PermissionError):
            self.pack.unlink()

    def test_remove_file_raises_the_first_permission_error(self) -> None:
        first = PermissionError(errno.EACCES, "first refusal", "first name")
        still_refused = PermissionError(errno.EACCES, "Access is denied", str(self.pack))
        with (
            mock.patch("os.unlink", side_effect=[first, still_refused]) as unlink,
            self.assertRaises(PermissionError) as exc,
        ):
            remove_file(self.pack)
        self.assertIs(exc.exception, first)
        self.assertEqual(first.filename, "first name")
        self.assertIn(str(still_refused), first.__notes__[0])
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
            self.assertRaises(FileNotFoundError) as exc,
        ):
            remove_file(path)
        self.assertIs(exc.exception, missing)
        unlink.assert_called_once_with(path)
        self.chmod.assert_not_called()


def is_git_path_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value.endswith(".git") or node.value == "objects")
    )


def bare_git_rmtree_calls(source: bytes, name: str) -> list[str]:
    """Report rmtree calls with .git, *.git or objects literals in the first argument or path=."""
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError(f"{name} exceeds {MAX_SOURCE_BYTES} bytes")
    tree = ast.parse(source.decode("utf-8"), filename=name)
    nodes = list(islice(ast.walk(tree), MAX_AST_NODES + 1))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError(f"{name} exceeds {MAX_AST_NODES} nodes")
    findings = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        match node.func:
            case ast.Name(id="rmtree") | ast.Attribute(value=ast.Name(id="shutil"), attr="rmtree"):
                paths = node.args[:1] + [kw.value for kw in node.keywords if kw.arg == "path"]
                # Each argument is a subtree of the already bounded module.
                if any(is_git_path_literal(n) for path in paths for n in ast.walk(path)):
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


def _reject_linked_source_path(root: Path, name: str) -> None:
    """Refuse a tracked source whose path escapes or traverses a link."""
    relative = PurePosixPath(name)
    parts = relative.parts
    if not parts or relative.is_absolute() or ".." in parts:
        raise ValueError(f"{name}: source path escapes the repository")
    path = root
    for index, part in enumerate(parts):
        path = path / part
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"{name}: cannot inspect source: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise ValueError(f"{name}: source path traverses a symlink")
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError(f"{name}: source path component is not a directory")


def read_tracked_source(root: Path, name: str) -> bytes:
    """Read one source file without following a link or blocking on a FIFO."""
    _reject_linked_source_path(root, name)
    path = root / name
    if not hasattr(os, "O_NOFOLLOW"):
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"{name}: cannot inspect source: {exc}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"{name}: source is not a regular file")
    try:
        descriptor = os.open(path, SOURCE_OPEN_FLAGS)
    except OSError as exc:
        raise ValueError(f"{name}: refusing to open source: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{name}: source is not a regular file")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            return source.read(MAX_SOURCE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
            findings.extend(bare_git_rmtree_calls(read_tracked_source(ROOT, name), name))
        self.assertFalse(findings, "\n".join(findings))

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(os, "O_NOFOLLOW"),
        "POSIX FIFO and O_NOFOLLOW only",
    )
    def test_tracked_symlink_to_fifo_is_refused(self) -> None:
        """A tracked Python symlink to a FIFO fails before open can block."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.mkfifo(root / "pipe")
            (root / "tracked.py").symlink_to(root / "pipe")
            with self.assertRaisesRegex(
                ValueError, "traverses a symlink|refusing to open source|not a regular file"
            ):
                read_tracked_source(root, "tracked.py")

    @unittest.skipUnless(
        hasattr(os, "symlink") and os.name != "nt",
        "planted directory symlinks need POSIX",
    )
    def test_tracked_source_rejects_a_symlinked_parent_directory(self) -> None:
        """A link in a parent component cannot redirect the tracked source."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "tracked.py").write_bytes(b"pass\n")
            (root / "pkg").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "traverses a symlink"):
                read_tracked_source(root, "pkg/tracked.py")

    def test_tracked_source_reads_an_ordinary_nested_path(self) -> None:
        """Ordinary directories below the root remain readable."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "tracked.py").write_bytes(b"pass\n")
            self.assertEqual(read_tracked_source(root, "pkg/tracked.py"), b"pass\n")

    def test_scan_refuses_oversized_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "bytes"):
            bare_git_rmtree_calls(b"#" * (MAX_SOURCE_BYTES + 1), "large.py")

    def test_scan_refuses_excess_ast_nodes(self) -> None:
        with (
            mock.patch.dict(globals(), MAX_AST_NODES=3),
            self.assertRaisesRegex(ValueError, "nodes"),
        ):
            bare_git_rmtree_calls(b"pass\n" * 3, "many.py")

    def test_the_scan_flags_a_planted_call(self) -> None:
        """Negative and boundary: flag both rmtree spellings and leave unrelated calls alone."""
        source = b"""shutil.rmtree(repo / ".git")
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
