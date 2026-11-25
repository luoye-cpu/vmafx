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
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.safe_subprocess import run as run_command  # noqa: E402
from scripts.lib.scratch_program import (  # noqa: E402
    ATTEMPTS,
    _can_retry,
    path_absent,
    remove_file,
    remove_program,
    remove_tree,
)

REAL_UNLINK = os.unlink
HAS_FD_WALKER = shutil.rmtree.avoids_symlink_attacks
HAS_FD_CHMOD = hasattr(os, "O_NOFOLLOW") and hasattr(os, "fchmod")
FD_CHMOD_REASON = "requires O_NOFOLLOW and fchmod"
FD_WALKER_REASON = "requires shutil's active fd-based rmtree walker"


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


@contextlib.contextmanager
def nonfollowing_chmod(**kwargs: Any) -> Iterator[mock.Mock]:
    with (
        mock.patch("os.chmod", new_callable=mock.Mock, **kwargs) as chmod,
        mock.patch.object(os, "supports_follow_symlinks", {chmod}),
    ):
        yield chmod


@contextlib.contextmanager
def fd_chmod() -> Iterator[mock.Mock]:
    with (
        mock.patch.object(os, "supports_follow_symlinks", set()),
        mock.patch.object(os, "O_NOFOLLOW", os.O_NOFOLLOW),
        mock.patch.object(os, "fchmod", wraps=os.fchmod) as fchmod,
    ):
        yield fchmod


@contextlib.contextmanager
def vanish_before_chmod(child: Path) -> Iterator[PermissionError]:
    """Refuse the child's unlink, then remove it just before chmod."""
    refused = PermissionError(errno.EACCES, "Access is denied", str(child))
    child.chmod(0o444)

    def chmod(path: Path, mode: int, *, follow_symlinks: bool) -> None:
        REAL_UNLINK(path)
        raise FileNotFoundError(errno.ENOENT, "vanished before chmod", str(child))

    with mock.patch("os.unlink", side_effect=refused), nonfollowing_chmod(side_effect=chmod):
        yield refused


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

    @staticmethod
    def windows_unlink(path: str) -> None:
        if not Path(path).stat().st_mode & stat.S_IWUSR:
            raise PermissionError(13, "Access is denied", path)
        REAL_UNLINK(path)

    def test_plain_rmtree_fails_on_a_read_only_object(self) -> None:
        """Negative: plain rmtree fails on a read-only Git object on Windows."""
        with (
            windows_rmtree(),
            mock.patch("os.unlink", self.windows_unlink),
            self.assertRaises(PermissionError) as exc,
        ):
            shutil.rmtree(self.tree)
        self.assertIn(Path(exc.exception.filename), (self.pack, self.loose))

    def test_remove_tree_clears_the_read_only_bit(self) -> None:
        """Positive: remove both read-only objects and the writable file."""
        mode = stat.S_IMODE(self.pack.stat().st_mode) | stat.S_IWUSR
        with (
            windows_rmtree(),
            mock.patch("os.unlink", self.windows_unlink),
            nonfollowing_chmod(wraps=os.chmod) as chmod,
        ):
            remove_tree(self.tree)
        chmod.assert_has_calls(
            [mock.call(p, mode, follow_symlinks=False) for p in (self.pack, self.loose)],
            any_order=True,
        )
        self.assertEqual(chmod.call_count, 2)
        self.assertFalse(self.tree.exists())

    @unittest.skipUnless(HAS_FD_CHMOD, FD_CHMOD_REASON)
    def test_fd_chmod_removes_read_only_objects(self) -> None:
        for remove, target in ((remove_file, self.pack), (remove_tree, self.loose.parent)):
            with self.subTest(remove=remove.__name__):
                leaf = self.pack if remove is remove_file else self.loose
                mode = stat.S_IMODE(leaf.stat().st_mode) | stat.S_IWUSR
                with (
                    windows_rmtree(),
                    mock.patch("os.unlink", self.windows_unlink),
                    fd_chmod() as fchmod,
                    mock.patch("os.open", wraps=os.open) as open_fd,
                    mock.patch("os.fstat", wraps=os.fstat) as fstat,
                    mock.patch("os.close", wraps=os.close) as close,
                ):
                    remove(target)
                open_fd.assert_called_once_with(
                    leaf, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                )
                fd = fstat.call_args.args[0]
                fchmod.assert_called_once_with(fd, mode)
                close.assert_called_once_with(fd)
                self.assertFalse(target.exists())

    def test_remove_tree_raises_the_first_permission_error(self) -> None:
        first = PermissionError(errno.EACCES, "first refusal", str(self.pack))
        second = PermissionError(errno.EACCES, "second refusal", "other name")
        with (
            windows_rmtree(),
            mock.patch("os.unlink", side_effect=[first, second]) as unlink,
            nonfollowing_chmod(wraps=os.chmod),
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
            with self.subTest(function=function.__name__), nonfollowing_chmod() as chmod:
                with (
                    mock.patch.object(shutil, "rmtree", fail_walk),
                    self.assertRaises(PermissionError) as exc,
                ):
                    remove_tree(self.tree)
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

        with windows_rmtree(), mock.patch("os.lstat", lstat), nonfollowing_chmod() as chmod:
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

        with windows_rmtree(), mock.patch("os.unlink", unlink):
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

    @unittest.skipUnless(HAS_FD_WALKER, FD_WALKER_REASON)
    def test_fd_child_vanished_before_chmod(self) -> None:
        with (
            vanish_before_chmod(self.loose) as refused,
            self.assertRaises(PermissionError) as exc,
        ):
            remove_tree(self.loose.parent)
        self.assertIs(exc.exception, refused)
        self.assertEqual(exc.exception.filename, str(self.loose))
        self.assertIn("vanished before chmod", refused.__notes__[0])

    @unittest.skipUnless(HAS_FD_WALKER, FD_WALKER_REASON)
    def test_fd_child_busy(self) -> None:
        busy = OSError(errno.EBUSY, os.strerror(errno.EBUSY), str(self.loose))
        with (
            mock.patch("os.unlink", side_effect=busy) as unlink,
            nonfollowing_chmod() as chmod,
            self.assertRaises(OSError) as exc,
        ):
            remove_tree(self.loose.parent)
        self.assertIs(exc.exception, busy)
        self.assertEqual(exc.exception.filename, str(self.loose))
        unlink.assert_called_once_with(self.loose.name, dir_fd=mock.ANY)
        chmod.assert_not_called()

    def test_postcondition_propagates_lstat_eacces(self) -> None:
        refused = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self.tree))
        with contextlib.ExitStack() as patches:

            def finished(*args: Any, **kwargs: Any) -> None:
                patches.enter_context(mock.patch("os.lstat", side_effect=refused))

            patches.enter_context(mock.patch.object(shutil, "rmtree", finished))
            with self.assertRaises(PermissionError) as exc:
                remove_tree(self.tree)
        self.assertIs(exc.exception, refused)

    def test_refused_rmdir_retries_once_after_chmod(self) -> None:
        for walker in ("windows", "fd"):
            with self.subTest(walker=walker):
                if walker == "fd" and not HAS_FD_WALKER:
                    self.skipTest(FD_WALKER_REASON)
                root = self.tree / walker
                directory = root / "child"
                directory.mkdir(parents=True)
                directory.chmod(0o555)
                mode = stat.S_IMODE(directory.stat().st_mode) | stat.S_IWUSR
                refused = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(directory))
                calls = mock.Mock(wraps=os)
                calls.rmdir.side_effect = [refused, mock.DEFAULT, mock.DEFAULT]
                with (
                    windows_rmtree() if walker == "windows" else contextlib.nullcontext(),
                    mock.patch("os.rmdir", calls.rmdir),
                    nonfollowing_chmod(wraps=os.chmod) as chmod,
                ):
                    calls.attach_mock(chmod, "chmod")
                    remove_tree(root)
                self.assertEqual(
                    [call[0] for call in calls.mock_calls], ["rmdir", "chmod", "rmdir", "rmdir"]
                )
                chmod.assert_called_once_with(directory, mode, follow_symlinks=False)
                self.assertFalse(directory.exists())

    def test_other_errors_are_not_retried(self) -> None:
        """Boundary: a transient busy error still reaches the caller on its first occurrence."""
        busy = OSError(errno.EBUSY, "Device or resource busy", str(self.pack))
        attempts = mock.Mock(side_effect=[busy, None])

        def unlink(path: str) -> None:
            if Path(path) == self.pack:
                attempts(path)
            self.windows_unlink(path)

        with windows_rmtree(), mock.patch("os.unlink", unlink), self.assertRaises(OSError) as exc:
            remove_tree(self.tree)
        self.assertIs(exc.exception, busy)
        attempts.assert_called_once_with(str(self.pack))

    def test_remove_file_clears_the_read_only_bit(self) -> None:
        """Positive: remove the read-only pack and leave the loose object in place."""
        mode = stat.S_IMODE(self.pack.stat().st_mode) | stat.S_IWUSR
        with (
            mock.patch("os.unlink", self.windows_unlink),
            nonfollowing_chmod(wraps=os.chmod) as chmod,
        ):
            remove_file(self.pack)
        chmod.assert_called_once_with(self.pack, mode, follow_symlinks=False)
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

        with mock.patch("os.unlink", unlink), self.assertRaises(PermissionError) as exc:
            remove(link)
        self.assertIs(exc.exception, refused)
        self.assertEqual(exc.exception.filename, str(link))
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o444)
        self.assertEqual(attempts, 1)

    def test_remove_tree_preserves_refused_symlink(self) -> None:
        for walker in ("windows", "fd"):
            with self.subTest(walker=walker):
                if walker == "fd" and not HAS_FD_WALKER:
                    self.skipTest(FD_WALKER_REASON)
                with windows_rmtree() if walker == "windows" else contextlib.nullcontext():
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
                    windows_rmtree(),
                    mock.patch("os.unlink", side_effect=first) as unlink,
                    nonfollowing_chmod(wraps=os.chmod) as chmod,
                    self.assertRaises(PermissionError) as exc,
                ):
                    remove(target)
                self.assertIs(exc.exception, first)
                self.assertFalse(outside.stat().st_mode & stat.S_IWUSR)
                chmod.assert_not_called()
                unlink.assert_called_once()

    @unittest.skipUnless(HAS_FD_CHMOD, FD_CHMOD_REASON)
    def test_fd_leaf_swap_preserves_outside_file(self) -> None:
        outside = self.tree.parent / "outside"
        outside.touch()
        outside.chmod(0o444)
        replacement = self.pack.with_suffix(".new")
        try:
            replacement.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"cannot create a symlink: {error}")
        first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
        real_open = os.open

        def swap_leaf(path: Path, flags: int) -> int:
            replacement.replace(path)
            return real_open(path, flags)

        with (
            fd_chmod() as fchmod,
            mock.patch("os.open", swap_leaf),
            mock.patch("os.unlink", side_effect=first),
            self.assertRaises(PermissionError) as exc,
        ):
            remove_file(self.pack)
        self.assertIs(exc.exception, first)
        self.assertFalse(outside.stat().st_mode & stat.S_IWUSR)
        self.assertIn("chmod", first.__notes__[0])
        fchmod.assert_not_called()

    @unittest.skipUnless(HAS_FD_CHMOD, FD_CHMOD_REASON)
    def test_fd_identity_mismatch_closes_without_chmod(self) -> None:
        real_open = os.open
        first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))

        def swap_leaf(path: Path, flags: int) -> int:
            self.loose.replace(path)
            return real_open(path, flags)

        with (
            fd_chmod() as fchmod,
            mock.patch("os.open", swap_leaf),
            mock.patch("os.close", wraps=os.close) as close,
            mock.patch("os.unlink", side_effect=first),
            self.assertRaises(PermissionError) as exc,
        ):
            remove_file(self.pack)
        self.assertIs(exc.exception, first)
        self.assertIn("differs from lstat", first.__notes__[0])
        self.assertFalse(self.pack.stat().st_mode & stat.S_IWUSR)
        fchmod.assert_not_called()
        close.assert_called_once()

    def test_parent_symlink_outside_root_refuses_retry(self) -> None:
        outside = self.tree.parent / "outside"
        outside.mkdir()
        child = outside / "child"
        child.touch()
        child.chmod(0o444)
        link = self.tree / "redirect"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create a directory symlink: {error}")
        failed = link / child.name
        self.assertFalse(_can_retry(failed.lstat(), failed, os.path.realpath(self.tree)))
        self.assertFalse(child.stat().st_mode & stat.S_IWUSR)

    def test_reparse_directory_refuses_retry(self) -> None:
        st = mock.Mock(
            st_mode=stat.S_IFDIR | 0o555, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        self.assertFalse(_can_retry(st, self.pack.parent, None))
        st.st_file_attributes = 0
        self.assertTrue(_can_retry(st, self.pack.parent, None))

    def test_writable_files_and_special_files_refuse_retry(self) -> None:
        for mode in (stat.S_IFREG | 0o644, stat.S_IFIFO | 0o444):
            with self.subTest(mode=mode):
                st = mock.Mock(st_mode=mode, st_nlink=1, st_file_attributes=0)
                self.assertFalse(_can_retry(st, self.pack, None))

    def test_no_nonfollowing_chmod_keeps_refusal(self) -> None:
        first = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
        with (
            mock.patch.object(os, "supports_follow_symlinks", set()),
            mock.patch.object(os, "O_NOFOLLOW", create=True),
            mock.patch("os.chmod") as chmod,
            mock.patch("os.fchmod", create=True) as fchmod,
            mock.patch("os.unlink", side_effect=first) as unlink,
            self.assertRaises(PermissionError) as exc,
        ):
            delattr(os, "O_NOFOLLOW")
            remove_file(self.pack)
        self.assertIs(exc.exception, first)
        self.assertIn("no non-following chmod", first.__notes__[0])
        chmod.assert_not_called()
        fchmod.assert_not_called()
        unlink.assert_called_once()

    def test_chmod_base_exception_propagates_unchanged(self) -> None:
        class Stop(BaseException):
            pass

        for remove, target in ((remove_file, self.pack), (remove_tree, self.pack.parent)):
            with self.subTest(remove=remove.__name__):
                stop = Stop()
                with (
                    windows_rmtree(),
                    mock.patch("os.unlink", side_effect=PermissionError()),
                    nonfollowing_chmod(side_effect=stop),
                    self.assertRaises(Stop) as exc,
                ):
                    remove(target)
                self.assertIs(exc.exception, stop)
                self.assertFalse(hasattr(stop, "__notes__"))

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
                windows_rmtree(),
                mock.patch("os.unlink", unlink),
                self.assertRaises(PermissionError) as exc,
            ):
                remove(target)
            self.assertIs(exc.exception, first)
            self.assertIn("retry lstat", first.__notes__[0])
            self.assertIn(str(failed), first.__notes__[0])

        check(remove_file, self.pack)
        check(remove_tree, self.pack.parent)

    @unittest.skipUnless(HAS_FD_WALKER, FD_WALKER_REASON)
    def test_fd_close_errors_annotate_first_failure(self) -> None:
        class _RetryFailure(BaseException):
            pass

        root = self.tree / "isolated"
        child = root / "sub" / "object"
        child.parent.mkdir(parents=True)
        child.touch()
        child.chmod(0o444)
        failure = _RetryFailure()
        close_error = OSError(errno.EIO, os.strerror(errno.EIO))
        real_open, real_close = os.open, os.close
        pending: set[int] = set()

        def open_dir(path: str, flags: int, *, dir_fd: int | None) -> int:
            fd = real_open(path, flags, dir_fd=dir_fd)
            pending.add(fd)
            return fd

        def close(fd: int) -> None:
            real_close(fd)
            pending.remove(fd)
            raise close_error

        try:
            with (
                mock.patch("os.open", open_dir),
                mock.patch("os.close", close),
                mock.patch("os.unlink", side_effect=PermissionError()),
                nonfollowing_chmod(side_effect=failure),
                self.assertRaises(_RetryFailure) as exc,
            ):
                remove_tree(root)
            self.assertIs(exc.exception, failure)
            self.assertFalse(pending)
            self.assertEqual(len(failure.__notes__), 2)
            for note, directory in zip(failure.__notes__, (child.parent, root), strict=True):
                self.assertIn("close", note)
                self.assertIn(str(directory), note)
                self.assertIn(str(close_error), note)
        finally:
            for fd in pending:
                real_close(fd)

    def test_chmod_failure_keeps_explicit_cause(self) -> None:
        cause = ValueError("cause of unlink refusal")
        refused = PermissionError(errno.EACCES, "unlink refused", str(self.pack))

        def unlink(path: str) -> None:
            raise refused from cause

        with (
            windows_rmtree(),
            mock.patch("os.unlink", unlink),
            nonfollowing_chmod(side_effect=OSError("chmod failed")),
            self.assertRaises(PermissionError) as exc,
        ):
            remove_tree(self.pack.parent)
        self.assertIs(exc.exception, refused)
        self.assertIs(refused.__cause__, cause)
        self.assertIn("chmod failed", refused.__notes__[0])

    def test_chmod_failure_keeps_caller_context(self) -> None:
        for walker in ("windows", "fd"):
            with self.subTest(walker=walker):
                if walker == "fd" and not HAS_FD_WALKER:
                    self.skipTest(FD_WALKER_REASON)
                refused = PermissionError(errno.EACCES, "unlink refused", str(self.pack))
                try:
                    raise RuntimeError("corrupt cache")
                except RuntimeError as caller:
                    with (
                        windows_rmtree() if walker == "windows" else contextlib.nullcontext(),
                        mock.patch("os.unlink", side_effect=refused),
                        nonfollowing_chmod(side_effect=OSError("chmod failed")),
                        self.assertRaises(PermissionError) as exc,
                    ):
                        remove_tree(self.pack.parent)
                    self.assertIs(exc.exception, refused)
                    self.assertIs(refused.__context__, caller)
                    self.assertIn("chmod failed", refused.__notes__[0])

    def test_plain_unlink_fails_on_a_read_only_object(self) -> None:
        """Negative: Windows refuses a plain unlink of the read-only pack."""
        with mock.patch("os.unlink", self.windows_unlink), self.assertRaises(PermissionError):
            self.pack.unlink()

    def test_remove_file_raises_the_first_permission_error(self) -> None:
        first = PermissionError(errno.EACCES, "first refusal", "first name")
        still_refused = PermissionError(errno.EACCES, "Access is denied", str(self.pack))
        with (
            mock.patch("os.unlink", side_effect=[first, still_refused]) as unlink,
            nonfollowing_chmod(wraps=os.chmod),
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
            nonfollowing_chmod() as chmod,
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
