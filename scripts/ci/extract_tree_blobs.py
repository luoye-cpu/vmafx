#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Extract raw regular blobs from a git revision without archive attributes or filters."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.new_scanner_findings import (  # noqa: E402
    ToolError,
    TreeListingError,
    revision_entries,
    tool,
)
from scripts.lib.safe_subprocess import (  # noqa: E402
    CommandOutputLimitExceeded,
    CommandTimedOut,
    CommandValidationError,
)
from scripts.lib.safe_subprocess import run as run_command  # noqa: E402

# Measured surface at 4b5e5c2e10 is 5,228 regular blobs (5,219 mode 100644 + 9 mode 100755,
# 52,684,924 B total); 16,384 gives >3x headroom for docs/ADR growth while bounding work.
DEFAULT_MAX_ENTRIES = 16_384
# Scalar upper bounds for HISS-02 across caller-supplied entry limits, pathspecs, and depth.
MAX_ENTRIES_CAP = 1_048_576
MAX_PATHSPECS = 1024
MAX_PATH_COMPONENTS = 256
# ~5x headroom over the measured 50.24 MiB surface payload at 4b5e5c2e10.
MAX_BATCH_OUTPUT_BYTES = 256 * 1_048_576
BATCH_TIMEOUT_SECONDS = 120.0
REGULAR_BLOB_MODES = frozenset({"100644", "100755"})
EXECUTABLE_BLOB_MODE = "100755"
BATCH_HEADER_FIELDS = 3
MISSING_HEADER_FIELDS = 2
EXIT_EXTRACT_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_LIST_ERROR = 3

BlobSpec = tuple[str, tuple[str, ...], str, str]
BlobPayload = tuple[tuple[str, ...], str, memoryview]


class ExtractError(RuntimeError):
    """Tree validation, batch object reading, or file writing failed."""


class _SingleLineParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as one stderr line."""

    def error(self, message: str) -> NoReturn:
        _emit_error(f"usage error: {message}")
        raise SystemExit(EXIT_USAGE_ERROR)


def _emit_error(message: str) -> None:
    """Write one newline-free diagnostic line to stderr."""
    clean = " ".join(message.replace("\r", " ").replace("\n", " ").split())
    print(f"extract_tree_blobs: {clean}", file=sys.stderr)


def _clean_git_env() -> dict[str, str]:
    """Drop inherited GIT_* overrides so cwd alone selects the repository."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _is_bad_component(part: str) -> bool:
    """Reject empty, current/parent-directory, and .git path components."""
    return part in {"", ".", ".."} or part.lower() == ".git"


def _validate_relpath(relpath: str) -> tuple[str, ...]:
    """Validate a relative git tree path and return its path components."""
    if not relpath or "\0" in relpath or relpath.startswith("/") or Path(relpath).is_absolute():
        raise ExtractError(f"unsafe tree path: {relpath!r}")
    parts = tuple(relpath.split("/"))
    if len(parts) > MAX_PATH_COMPONENTS:
        raise ExtractError(f"path exceeds {MAX_PATH_COMPONENTS} components: {relpath!r}")
    if any(_is_bad_component(part) for part in parts) or any(
        _is_bad_component(part) for part in Path(relpath).parts
    ):
        raise ExtractError(f"unsafe path component in {relpath!r}")
    return parts


def _select_regular_blobs(
    entries: dict[str, tuple[str, str]],
    max_entries: int,
) -> list[BlobSpec]:
    """Verify the entry bound, regular-blob modes, and relative paths."""
    if max_entries < 0 or max_entries > MAX_ENTRIES_CAP:
        raise ExtractError(f"max_entries={max_entries} outside 0..{MAX_ENTRIES_CAP}")
    if len(entries) > max_entries:
        raise ExtractError(f"entry count {len(entries)} exceeds bound {max_entries}")
    specs: list[BlobSpec] = []
    for relpath, (mode, oid) in entries.items():
        if mode not in REGULAR_BLOB_MODES:
            raise ExtractError(f"unsupported tree entry mode {mode} at {relpath}")
        specs.append((relpath, _validate_relpath(relpath), mode, oid))
    return specs


def _parse_batch_header(header: bytes, relpath: str, expected_oid: str) -> int:
    """Validate one ``<oid> blob <size>`` header line and return the byte size."""
    fields = header.split(b" ")
    if len(fields) == MISSING_HEADER_FIELDS and fields[1] == b"missing":
        raise ExtractError(f"missing git object {expected_oid} for {relpath}")
    if len(fields) != BATCH_HEADER_FIELDS:
        raise ExtractError(f"malformed batch header for {relpath}")
    got_oid, obj_type, size_bytes = fields
    if got_oid != expected_oid.encode("ascii"):
        raise ExtractError(f"batch object mismatch for {relpath}")
    if obj_type != b"blob":
        kind = obj_type.decode("ascii", errors="replace")
        raise ExtractError(f"expected blob for {relpath}, got {kind}")
    if not size_bytes.isdigit():
        raise ExtractError(f"invalid blob size for {relpath}")
    return int(size_bytes)


def _parse_batch_stream(raw: bytes, specs: Sequence[BlobSpec]) -> list[BlobPayload]:
    """Slice raw ``git cat-file --batch`` output into validated blob views."""
    view = memoryview(raw)
    total = len(raw)
    offset = 0
    blobs: list[BlobPayload] = []
    for relpath, parts, mode, oid in specs:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise ExtractError(f"short read in batch header for {relpath}")
        size = _parse_batch_header(raw[offset:newline], relpath, oid)
        start = newline + 1
        end = start + size
        if end + 1 > total:
            raise ExtractError(f"short read in batch payload for {relpath}")
        if raw[end : end + 1] != b"\n":
            raise ExtractError(f"unterminated batch payload for {relpath}")
        blobs.append((parts, mode, view[start:end]))
        offset = end + 1
    if offset != total:
        raise ExtractError("unexpected trailing bytes in batch output")
    return blobs


def _read_batch_blobs(git: str, repo: Path, specs: Sequence[BlobSpec]) -> list[BlobPayload]:
    """Read all regular blobs from one raw ``git cat-file --batch`` call."""
    if not specs:
        return []
    stdin_bytes = "".join(f"{oid}\n" for _, _, _, oid in specs).encode("ascii")
    proc = run_command(
        [git, "cat-file", "--batch"],
        allowed_executables=(git,),
        cwd=repo,
        env=_clean_git_env(),
        input_data=stdin_bytes,
        capture_output=True,
        text=False,
        timeout_seconds=BATCH_TIMEOUT_SECONDS,
        max_output_bytes=MAX_BATCH_OUTPUT_BYTES,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {err}" if err else ""
        raise ExtractError(f"git cat-file --batch exited {proc.returncode}{detail}")
    return _parse_batch_stream(proc.stdout, specs)


def _ensure_safe_dir(root_resolved: Path, directory: Path) -> None:
    """Create ``directory`` and verify it resolves inside ``root_resolved``."""
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.resolve().is_relative_to(root_resolved):
        raise ExtractError(f"destination directory escapes root: {directory}")


def _write_blobs(dest: Path, blobs: Sequence[BlobPayload]) -> None:
    """Write validated raw blobs under ``dest``, keeping ``100755`` blobs executable."""
    dest.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or not dest.is_dir():
        raise ExtractError(f"destination is not a directory: {dest}")
    root_resolved = dest.resolve(strict=True)
    checked_dirs: set[Path] = {dest}
    for parts, mode, payload in blobs:
        target = dest.joinpath(*parts)
        parent = target.parent
        if parent not in checked_dirs:
            _ensure_safe_dir(root_resolved, parent)
            checked_dirs.add(parent)
        if target.is_symlink() or target.exists():
            raise ExtractError(f"refusing to overwrite existing path: {target}")
        target.write_bytes(payload)
        if mode == EXECUTABLE_BLOB_MODE:
            target.chmod(target.stat().st_mode | stat.S_IXUSR)


def extract_tree_blobs(
    repo: Path,
    rev: str,
    dest: Path,
    pathspecs: Sequence[str],
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> int:
    """Extract raw regular blobs matching ``pathspecs`` at ``rev`` into ``dest``."""
    if not pathspecs or len(pathspecs) > MAX_PATHSPECS:
        raise ExtractError(f"expected 1..{MAX_PATHSPECS} pathspecs, got {len(pathspecs)}")
    git = tool("git")
    entries = revision_entries(git, repo, rev, pathspecs=pathspecs)
    specs = _select_regular_blobs(entries, max_entries)
    blobs = _read_batch_blobs(git, repo, specs)
    _write_blobs(dest, blobs)
    return len(blobs)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse CLI flags and positional pathspecs."""
    parser = _SingleLineParser(prog="extract_tree_blobs.py", add_help=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--rev", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    parser.add_argument("pathspecs", nargs="+")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Run CLI extraction; return 0 on pass, 3 on ls-tree failure, 1/2 otherwise."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        extract_tree_blobs(
            args.repo,
            args.rev,
            args.dest,
            args.pathspecs,
            max_entries=args.max_entries,
        )
    except TreeListingError as error:
        _emit_error(str(error))
        return EXIT_LIST_ERROR
    except (
        ExtractError,
        ToolError,
        CommandValidationError,
        CommandTimedOut,
        CommandOutputLimitExceeded,
        OSError,
    ) as error:
        _emit_error(str(error))
        return EXIT_EXTRACT_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
