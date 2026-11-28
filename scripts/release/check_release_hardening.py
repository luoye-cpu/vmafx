#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Reject release ELF files that lack the documented hardening properties.

The release build sets the flags of docs/principles.md section 2.2 through
core/native/release-hardening.ini (issue 2748). This checker reads the built
artifacts and fails when a file does not carry the binary evidence of them,
so a release path that stops passing --native-file fails at verify time
instead of shipping with compiler-default protection only.

Per file (readelf subprocess, as the Linux CI and the release container
provide it; nothing here parses ELF bytes directly):

  * PIE              — the file is a position-independent executable: ELF type
                       ET_DYN with the dynamic PIE flag (DF_1_PIE). A shared
                       object (it declares a SONAME) needs no PIE flag.
  * Full RELRO       — a PT_GNU_RELRO segment and BIND_NOW (DT_BIND_NOW, or
                       DF_BIND_NOW in DT_FLAGS, or DF_1_NOW in DT_FLAGS_1).
  * Stack protector  — an undefined __stack_chk_fail/__stack_chk_guard import,
                       the canary machinery -fstack-protector-strong links in.
  * Fortify          — an undefined __*_chk import, the wrapper glibc's
                       _FORTIFY_SOURCE calls resolve to (the issue's own
                       baseline evidence: plain memcpy/snprintf, no _chk).
  * Run path hygiene — no DT_RPATH, and every RPATH/RUNPATH entry is a
                       $ORIGIN-relative path with no "..": the Meson build-tree
                       run path `$ORIGIN/../src` (v1.0.0-rc.1/rc.3 leftover)
                       and any absolute build-directory entry are rejected.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.lib.safe_subprocess import (  # noqa: E402
    CommandFailed,
    CommandOutputLimitExceeded,
    CommandTimedOut,
)
from scripts.lib.safe_subprocess import run as run_command  # noqa: E402

STACK_PROTECTOR_RE = re.compile(r"^__(?:stack_chk_fail(?:_local)?|stack_chk_guard)(?:@\S+)?$")
FORTIFY_RE = re.compile(r"^__\w+_chk(?:@\S+)?$")
ORIGIN_RELATIVE_RE = re.compile(r"^\$ORIGIN(?![A-Za-z0-9_])")
# readelf -W --dyn-syms: Num, Value, Size, Type, Bind, Vis, Ndx, Name.
_DYNSYM_COLUMNS = 8
_DYNSYM_NDX_COLUMN = 6


def _readelf(readelf: str, binary: Path, flags: list[str]) -> str:
    """Run one readelf query or raise with its stderr."""
    result = run_command(
        [readelf, *flags, "--", str(binary)],
        allowed_executables=(readelf,),
        capture_output=True,
        text=True,
        check=True,
        timeout_seconds=60,
    )
    return result.stdout


def _dynamic_value(section: str, tag: str) -> list[str]:
    """The [..] values of every (TAG) line, to their closing bracket."""
    return re.findall(rf"\({tag}\)[^[]*\[(.*?)\]\s*$", section, re.MULTILINE)


def _dynamic_flag(section: str, tag: str) -> bool:
    """True when a (TAG) entry line exists."""
    return bool(re.search(rf"\({tag}\)", section))


def _undefined_symbols(dynsyms: str) -> list[str]:
    """The names of the undefined dynamic symbols."""
    names = []
    for line in dynsyms.splitlines():
        columns = line.split()
        if len(columns) >= _DYNSYM_COLUMNS and columns[_DYNSYM_NDX_COLUMN] == "UND":
            # The name is always the eighth column; a versioned import carries
            # a symbol version index after it ("__stack_chk_fail@GLIBC_2.4 (3)"),
            # so the last column would be "(3)" (issue 2748 review; readelf
            # 2.41 in the release builder, binutils 2.47).
            names.append(columns[_DYNSYM_COLUMNS - 1])
    return names


def _check_pie(elf_type: str, dynamic: str) -> str | None:
    """PIE: an executable is ET_DYN with the DF_1_PIE flag."""
    if elf_type not in ("DYN", "EXEC"):
        return f"unexpected ELF type {elf_type}"
    if elf_type == "EXEC":
        return "ELF type EXEC: not a position-independent executable"
    shared_object = bool(_dynamic_value(dynamic, "SONAME"))
    pie_flagged = _dynamic_flag(dynamic, "FLAGS_1") and bool(
        re.search(r"FLAGS_1[^\n]*\bPIE\b", dynamic)
    )
    if not shared_object and not pie_flagged:
        return "no SONAME and no PIE flag: an executable without -fPIE/-pie"
    return None


def _check_relro(segments: str, dynamic: str) -> str | None:
    """Full RELRO: the PT_GNU_RELRO segment plus BIND_NOW."""
    if not re.search(r"GNU_RELRO", segments):
        return "no PT_GNU_RELRO segment (-Wl,-z,relro)"
    bind_now = (
        _dynamic_flag(dynamic, "BIND_NOW")
        or bool(re.search(r"\(FLAGS\)[^\n]*\bBIND_NOW\b", dynamic))
        or bool(re.search(r"\(FLAGS_1\)[^\n]*\bNOW\b", dynamic))
    )
    if not bind_now:
        return "RELRO without BIND_NOW (-Wl,-z,now)"
    return None


def _check_imports(undefined: list[str]) -> str | None:
    """Stack protector and fortify leave their imports in the dynamic table."""
    if not any(STACK_PROTECTOR_RE.match(name) for name in undefined):
        return "no __stack_chk_fail import (-fstack-protector-strong)"
    if not any(FORTIFY_RE.match(name) for name in undefined):
        return "no __*_chk import (_FORTIFY_SOURCE)"
    return None


def _check_runpaths(dynamic: str) -> str | None:
    """No DT_RPATH, and every RUNPATH entry is $ORIGIN-relative."""
    if _dynamic_flag(dynamic, "RPATH"):
        return "carries DT_RPATH, which the loader keeps beside RUNPATH"
    for runpath in _dynamic_value(dynamic, "RUNPATH"):
        for raw_entry in runpath.split(":"):
            entry = raw_entry.strip()
            if not entry:
                continue
            if ".." in entry:
                return f"RUNPATH entry escapes its directory: [{entry}]"
            if not ORIGIN_RELATIVE_RE.match(entry):
                return f"RUNPATH entry is not $ORIGIN-relative: [{entry}]"
    return None


def check_file(readelf: str, binary: Path) -> str | None:
    """Report the first hardening property the file lacks, or None."""
    try:
        header = _readelf(readelf, binary, ["-h"])
        segments = _readelf(readelf, binary, ["-l", "-W"])
        dynamic = _readelf(readelf, binary, ["-d", "-W"])
        dynsyms = _readelf(readelf, binary, ["-W", "--dyn-syms"])
    except (OSError, CommandFailed, CommandOutputLimitExceeded, CommandTimedOut) as error:
        return f"readelf failed: {error}"

    match = re.search(r"^\s*Type:\s+(\S+)", header, re.MULTILINE)
    if match is None:
        return "not an ELF file (no header type)"

    for problem in (
        _check_pie(match.group(1), dynamic),
        _check_relro(segments, dynamic),
        _check_imports(_undefined_symbols(dynsyms)),
        _check_runpaths(dynamic),
    ):
        if problem is not None:
            return problem
    return None


def main(argv: list[str]) -> int:
    """Check every supplied ELF file with readelf."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="release ELF files")
    args = parser.parse_args(argv)
    readelf = shutil.which("readelf")
    if readelf is None:
        print("ERROR: readelf is not on PATH", file=sys.stderr)
        return 1
    failed = False
    for binary in args.files:
        problem = check_file(readelf, binary)
        if problem:
            print(f"ERROR: {binary}: {problem}", file=sys.stderr)
            failed = True
        else:
            print(
                f"OK: {binary}: PIE, full RELRO (BIND_NOW), stack protector, "
                "fortified, no build-tree run path"
            )
    return int(failed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
