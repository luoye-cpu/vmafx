# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Clean up scratch programs, files and directory trees.

Microsoft Defender's real-time protection stays on on the Windows ARM64
runner image (Tamper Protection keeps the image build from turning it off;
the x64 images turn it off). For a short time after a freshly built program
has run and exited, deleting it fails with ``PermissionError`` (WinError 5,
Access is denied). ``TemporaryDirectory.cleanup()`` stops at the first such
error, so a test whose assertions had all passed failed in its cleanup.

:func:`remove_program` deletes the program before the directory is cleaned
up. It retries only a ``PermissionError`` on that one file, at most
``attempts`` times, ``delay`` seconds apart, and raises the last error. Any
other error is raised at once. Windows toolchains append ``.exe`` to an
output name without an extension, so both spellings are tried. Everything
else in the directory is left to the normal cleanup, whose errors still fail
the test.

Git writes object files read-only. Windows refuses to unlink a read-only
file, so :func:`remove_file` and :func:`remove_tree` can retry a refused
unlink or rmdir once after a non-following chmod (see :func:`_retry_read_only`).
Tree removal also checks that the root is gone.

Standard library only: suites import it as ``scripts.lib.scratch_program``
with the repository root on ``sys.path``.
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


def path_absent(path: Path) -> bool:
    """Return true only for a missing directory entry. Propagate other lstat errors."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


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
        if not path_absent(candidate):
            _unlink(candidate, attempts, delay, remove)


def _can_retry(st: os.stat_result, path: Path, root: str | None) -> bool:
    mode = st.st_mode
    if (
        stat.S_ISLNK(mode)
        or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        return False
    if mode & stat.S_IWUSR or not (stat.S_ISDIR(mode) or (stat.S_ISREG(mode) and st.st_nlink == 1)):
        return False
    if root is None:
        return True
    parent = os.path.realpath(path.parent, strict=True)
    return os.path.commonpath((root, parent)) == root


def _chmod_read_only(path: Path, st: os.stat_result) -> None:
    mode = stat.S_IMODE(st.st_mode) | stat.S_IWUSR
    chmod = os.chmod
    if chmod in os.supports_follow_symlinks:
        chmod(path, mode, follow_symlinks=False)
        return
    if not hasattr(os, "O_NOFOLLOW"):
        raise NotImplementedError("no non-following chmod available")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        if not os.path.samestat(st, os.fstat(fd)):
            raise OSError("opened object differs from lstat")
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def _retry_read_only(
    path: Path, operation: Callable[[], object], refusal: PermissionError, root: str | None = None
) -> bool:
    """Retry a read-only, non-link, non-reparse directory or single-link regular file only
    with a non-following chmod and, for tree removal, a parent inside the resolved root.

    Use only for scratch trees because the public onexc callback has no dir_fd to detect
    an ancestor replaced between the confinement check and chmod.
    """
    try:
        st = os.lstat(path)
    except OSError as error:
        refusal.add_note(f"retry lstat({path!s}): {error}")
        return False
    step = "confinement check"
    try:
        if not _can_retry(st, path, root):
            return False
        step = "chmod"
        _chmod_read_only(path, st)
        step = "deletion"
        operation()
    except Exception as error:
        refusal.add_note(f"retry {step}({path!s}): {error}")
        return False
    return True


def remove_file(path: Path) -> None:
    """Unlink one file, retrying once after chmod on a permission failure."""
    try:
        path.unlink()
    except PermissionError as refusal:
        if not _retry_read_only(path, path.unlink, refusal):
            raise


class _CallbackError(Exception):
    """Carry the original error past rmtree's fd walker, which rewrites OSError.filename."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(error)
        self.error = error


def remove_tree(path: Path) -> None:
    """Retry a refused unlink or rmdir once after chmod and verify that the tree is gone."""
    root = os.path.realpath(path, strict=os.path.ALLOW_MISSING)
    first_error: BaseException | None = None

    def onexc(function: Callable[..., object], failed_path: str, error: BaseException) -> None:
        nonlocal first_error
        if first_error is not None:
            first_error.add_note(f"{function.__name__}({failed_path!r}): {error}")
            return
        if isinstance(error, PermissionError) and function in (os.unlink, os.rmdir):
            try:
                if _retry_read_only(Path(failed_path), lambda: function(failed_path), error, root):
                    return
            except BaseException as retry_error:
                error = retry_error
        first_error = error
        raise _CallbackError(error)

    try:
        shutil.rmtree(path, onexc=onexc)
    except _CallbackError as wrapped:
        first_error = wrapped.error
    if first_error is not None:
        raise first_error
    if not path_absent(path):
        raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), str(path))
