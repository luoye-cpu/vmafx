# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Clean up programs and directory trees created by tests.

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
file, so :func:`remove_tree` clears the read-only bit and retries once.

Standard library only: suites import it as ``scripts.lib.scratch_program``
with the repository root on ``sys.path``.
"""

from __future__ import annotations

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


def remove_tree(path: Path) -> None:
    """Remove a scratch tree, retrying a refused operation once after chmod."""

    def retry(function: Callable[[str], object], failed_path: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(failed_path, stat.S_IREAD | stat.S_IWRITE)  # noqa: PTH101 -- rmtree supplies a str
        function(failed_path)

    shutil.rmtree(path, onexc=retry)
