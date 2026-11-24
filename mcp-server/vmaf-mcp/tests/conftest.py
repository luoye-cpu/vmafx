# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Suite-wide pytest setup for the vmaf-mcp tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib import vmaftest  # noqa: E402 - needs the repository root on sys.path

# The vmaf CLI under test, or None (see scripts/lib/vmaftest.py).
VMAF_UNDER_TEST = vmaftest.find()
_NOT_BUILT = _REPO_ROOT / vmaftest.BUILD_DIRS[0] / "tools" / vmaftest.executable_name()


@pytest.fixture(scope="session")
def vmaf_under_test() -> Path | None:
    """The CLI resolved before the autouse fixture replaces ``VMAF_BIN``.

    Tests that need a built CLI should consume this instead of calling
    ``vmaftest.find()`` after the autouse fixture ran: that fixture deliberately
    points ``VMAF_BIN`` at the missing build path when nothing is built, so a
    second ``find()`` raises rather than returning ``None``.
    """
    return VMAF_UNDER_TEST


@pytest.fixture(autouse=True)
def _server_runs_the_vmaf_under_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the server at the build under test before every test.

    ``server._vmaf_binary()`` is runtime discovery for users: ``VMAF_BIN``,
    then the installed binary under ``/usr/local/bin``. A tool a test calls
    must not reach that install, so ``VMAF_BIN`` names the vmaf under test,
    or, when nothing is built, the missing CLI of the first build directory.
    A test of the discovery order unsets it itself.
    """
    target = VMAF_UNDER_TEST or _NOT_BUILT
    monkeypatch.setenv("VMAF_BIN", str(target))
