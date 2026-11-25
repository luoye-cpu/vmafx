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
file, so :func:`remove_file` and :func:`remove_tree` clear the read-only
bit and retry a refused unlink or rmdir once if lstat identifies a non-symlink.
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


def _retry_read_only(path: Path, operation: Callable[[], object]) -> bool:
    """Retry only a verified non-symlink, returning false if it cannot be verified.

    Use only on scratch trees since the public onexc callback supplies no dir_fd
    to guard against path replacement between lstat and chmod.
    """
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    if stat.S_ISLNK(mode):
        return False
    writable = stat.S_IREAD | stat.S_IWRITE
    chmod = os.chmod
    if chmod in os.supports_follow_symlinks:
        chmod(path, writable, follow_symlinks=False)
    else:
        chmod(path, writable)
    operation()
    return True


def remove_file(path: Path) -> None:
    """Unlink one file, retrying once after chmod on a permission failure."""
    try:
        path.unlink()
    except PermissionError:
        if not _retry_read_only(path, path.unlink):
            raise


class _CallbackError(Exception):
    """Carry the original error past rmtree's fd walker, which rewrites OSError.filename."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(error)
        self.error = error


def remove_tree(path: Path) -> None:
    """Retry a refused unlink or rmdir once after chmod and verify that the tree is gone."""
    first_error: BaseException | None = None

    def onexc(function: Callable[..., object], failed_path: str, error: BaseException) -> None:
        nonlocal first_error
        if first_error is not None:
            first_error.add_note(f"{function.__name__}({failed_path!r}): {error}")
            return
        if isinstance(error, PermissionError) and function in (os.unlink, os.rmdir):
            try:
                if _retry_read_only(Path(failed_path), lambda: function(failed_path)):
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
