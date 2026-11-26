# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Remove caller-created, caller-owned scratch files and trees with no concurrent writer.

`remove_program` tries both program spellings (with and without an added .exe)
and waits a bounded time for a held executable. `remove_file` unlinks one file.
`remove_tree` finishes the walk, reports the first error with bounded notes,
and checks that the root is gone. `path_absent` checks even for dangling links.

On Windows only, a refused unlink or rmdir of a read-only directory or single-link
regular file gets one retry after a non-following chmod. Links and reparse points
are ineligible. A hard-linked read-only object keeps its refusal. POSIX never
retries read-only deletion. Concurrent replacement of a leaf or ancestor during
removal is outside the helpers' threat model.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

ATTEMPTS = 20
DELAY_SECONDS = 0.25  # 5 seconds in all, far beyond the scans seen in CI
# Dispatcher measurements (2026-11-26): ~/.cache/vmafx/ffmpeg-patch-stack/<key>/source.git
# had 36 entries (one tag, 3 pack files, 20 MiB). Two full FFmpeg .git directories
# had 5,865 and 1,305 entries. The limit is about 11 times the largest.
MAX_TREE_ENTRIES = 65_536


_RETRY_READ_ONLY = os.name == "nt"
_chmod = os.chmod
_MAX_NOTES = 32


def _unlink(path: Path, attempts: int, delay: float, unlink: Callable[[Path], None]) -> None:
    for attempt in range(attempts):
        try:
            unlink(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
        time.sleep(delay)


def remove_program(
    path: Path,
    *,
    attempts: int = ATTEMPTS,
    delay: float = DELAY_SECONDS,
    unlink: Callable[[Path], None] | None = None,
) -> None:
    """Delete ``path`` (or ``path.exe``), waiting for Windows to release it."""
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, not {attempts}")
    remove = unlink if unlink is not None else os.unlink
    for candidate in (path, path.with_name(path.name + ".exe")):
        if os.path.lexists(candidate):
            _unlink(candidate, attempts, delay, remove)


def path_absent(path: Path) -> bool:
    """Return true only for a missing directory entry. Propagate other lstat errors."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def _can_retry(st: os.stat_result) -> bool:
    mode = st.st_mode
    if (
        mode & stat.S_IWUSR
        or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        return False
    return stat.S_ISDIR(mode) or (stat.S_ISREG(mode) and st.st_nlink == 1)


def _retry_read_only(
    path: Path, operation: Callable[[], object], note: Callable[[str], None]
) -> bool:
    if not _RETRY_READ_ONLY:
        return False
    step = "lstat"
    try:
        st = os.lstat(path)
        if not _can_retry(st):
            return False
        step = "chmod"
        _chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IWUSR, follow_symlinks=False)
        step = "deletion"
        operation()
    except OSError as error:
        note(f"retry {step}({path!s}): {error}")
        return False
    return True


def remove_file(path: Path) -> None:
    """Unlink one file, preserving the original refusal if the retry fails."""
    try:
        path.unlink()
    except PermissionError as refusal:
        if not _retry_read_only(path, path.unlink, refusal.add_note):
            raise


def walk_tree(root: Path, limit: int) -> list[os.DirEntry[str]]:
    """Collect non-directory entries without following links or exceeding the entry limit."""
    # The path predicates suppress stat errors. Preserve a refused root lstat.
    mode = os.lstat(root).st_mode
    if root.is_symlink() or root.is_junction():
        raise OSError(f"refusing to walk linked root: {root}")
    if not stat.S_ISDIR(mode):
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(root))
    directories = [root]
    leaves: list[os.DirEntry[str]] = []
    count = 0
    while directories:
        with os.scandir(directories.pop()) as entries:
            for entry in entries:
                count += 1
                if count > limit:
                    raise OSError(f"tree at {root} exceeds {limit} entries")
                if (
                    not entry.is_symlink()
                    and not entry.is_junction()
                    and entry.is_dir(follow_symlinks=False)
                ):
                    directories.append(Path(entry.path))
                else:
                    leaves.append(entry)
    return leaves


def remove_tree(path: Path) -> None:
    """Remove the tree, raising the first failure after the walk finishes."""
    first_error: OSError | None = None
    note_count = 0

    def onexc(function: Callable[..., object], failed_path: str, error: BaseException) -> None:
        nonlocal first_error, note_count
        if not isinstance(error, OSError):
            raise error
        notes: list[str] = []
        if (
            isinstance(error, PermissionError)
            and function in (os.unlink, os.rmdir)
            and _retry_read_only(Path(failed_path), lambda: function(failed_path), notes.append)
        ):
            return
        if first_error is None:
            first_error = error
        else:
            notes.insert(0, f"{failed_path}: {error}")
        for note in notes:  # At most one walk error and one retry error.
            note_count += 1
            if note_count <= _MAX_NOTES:
                first_error.add_note(note)

    walk_tree(path, MAX_TREE_ENTRIES)
    shutil.rmtree(path, onexc=onexc)
    if first_error is not None:
        if note_count > _MAX_NOTES:
            first_error.add_note(f"{note_count - _MAX_NOTES} additional error(s) omitted")
        raise first_error
    if not path_absent(path):
        raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), str(path))
