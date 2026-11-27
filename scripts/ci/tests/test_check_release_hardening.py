# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""check-release-hardening.py: planted readelf reports and real compiled fixtures.

The synthetic cases feed canned `readelf` output to the checker, so the
accept/reject matrix runs on every host. The compiled cases build two tiny C
programs with the host `cc` — one with the release profile's flags and one
without — and require the checker to accept the first and refuse the second;
they skip when `cc` or `readelf` is unavailable (they never run on a host
without a Linux toolchain, e.g. the Windows development checkout).
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 -- argv-list calls to the host compiler and readelf only
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.release import check_release_hardening

FIXTURE_SOURCE = """\
#include <stdio.h>
#include <string.h>

/* A library fixture adds one exported function; the CLI fixture is main.
   `volatile` keeps the copy length unknown at compile time, so fortified
   glibc headers lower the memcpy to __memcpy_chk and the buffer keeps its
   stack-protector canary - the two imports the checker requires. */
int harden_fixture(void) {
    char buf[64];
    volatile size_t n = 24;
    memcpy(buf, "release-hardening-fixture-data--------", n);
    buf[n] = '\\0';
    printf("%s\\n", buf);
    return (int)n;
}

#ifdef CLI_MAIN
int main(void) {
    return harden_fixture() == 24 ? 0 : 1;
}
#endif
"""

HARDEN_FLAGS = [
    "-O2",
    "-D_FORTIFY_SOURCE=3",
    "-fstack-protector-strong",
    "-fPIE",
    "-pie",
    "-Wl,-z,relro,-z,now",
]


def _fake_readelf(header: str, segments: str, dynamic: str, dynsyms: str):
    """A subprocess.run stub answering each readelf query of check_file."""

    def run(cmd, **_kwargs):
        flags = cmd[1:-1]
        if "--dyn-syms" in flags:
            out = dynsyms
        elif "-h" in flags:
            out = header
        elif "-l" in flags:
            out = segments
        else:
            out = dynamic
        return subprocess.CompletedProcess(cmd, 0, out, "")

    return run


def _header(elf_type: str) -> str:
    return f"ELF Header:\n  Class:                             ELF64\n  Type:                              {elf_type}\n"


def _segments(relro: bool) -> str:
    return (
        "Program Headers:\n  Type           Offset             VirtAddr\n"
        + ("  GNU_RELRO      0x0000000000002bf0 0x0000000000000000\n" if relro else "")
        + "  INTERP         0x0000000000000318 0x0000000000000318\n"
        + "  LOAD           0x0000000000000000 0x0000000000000000\n"
    )


def _dynamic(
    soname: str | None,
    pie: bool,
    bind_now: bool,
    rpath: str | None = None,
    runpath: str | None = None,
) -> str:
    lines = [
        "Dynamic section at offset 0x2bd0 contains 12 entries:",
        "  Tag        Type                         Name/Value",
    ]
    lines.append(" 0x0000000000000001 (NEEDED) Shared library: [libvmaf.so.3]")
    if soname:
        lines.append(f" 0x000000000000000e (SONAME) Library soname: [{soname}]")
    if bind_now:
        lines.append(" 0x000000000000001e (FLAGS) Flags: BIND_NOW")
        lines.append(" 0x000000006ffffffb (FLAGS_1) Flags: NOW" + (" PIE" if pie else ""))
    else:
        lines.append(" 0x000000006ffffff9 (FLAGS_1) Flags: " + ("PIE" if pie else "0"))
    if rpath:
        lines.append(f" 0x000000000000000f (RPATH) Library rpath: [{rpath}]")
    if runpath:
        lines.append(f" 0x000000000000001d (RUNPATH) Library runpath: [{runpath}]")
    return "\n".join(lines) + "\n"


def _dynsyms(stack_protector: bool, fortify: bool) -> str:
    rows = [
        "Symbol table '.dynsym' contains 8 entries:",
        "   Num:    Value          Size Type    Bind   Vis      Ndx Name",
        "     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND ",
        "     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND memcpy@GLIBC_2.2.5",
    ]
    if stack_protector:
        rows.append(
            "     2: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __stack_chk_fail@GLIBC_2.4"
        )
    if fortify:
        rows.append(
            "     3: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __memcpy_chk@GLIBC_2.14"
        )
        rows.append(
            "     4: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __snprintf_chk@GLIBC_2.3.4"
        )
    return "\n".join(rows) + "\n"


def _stub(
    elf_type: str = "DYN",
    soname: str | None = None,
    relro: bool = True,
    pie: bool = False,
    bind_now: bool = True,
    stack_protector: bool = True,
    fortify: bool = True,
    rpath: str | None = None,
    runpath: str | None = "$ORIGIN",
):
    return _fake_readelf(
        _header(elf_type),
        _segments(relro),
        _dynamic(soname, pie, bind_now, rpath, runpath),
        _dynsyms(stack_protector, fortify),
    )


def _check_with(stub, binary: Path) -> str | None:
    with patch.object(check_release_hardening.subprocess, "run", stub):
        return check_release_hardening.check_file(Path("readelf"), binary)


def test_accepts_a_hardened_cli(tmp_path: Path) -> None:
    problem = _check_with(_stub(elf_type="DYN", pie=True, runpath="$ORIGIN"), tmp_path / "vmaf")
    assert problem is None


def test_accepts_a_hardened_shared_library(tmp_path: Path) -> None:
    problem = _check_with(
        _stub(soname="libvmaf.so.3", pie=False, runpath="$ORIGIN"), tmp_path / "libvmaf.so.3.0.0"
    )
    assert problem is None


def test_rejects_an_exec_type_binary(tmp_path: Path) -> None:
    problem = _check_with(_stub(elf_type="EXEC"), tmp_path / "vmaf")
    assert problem == "ELF type EXEC: not a position-independent executable"


def test_rejects_an_executable_without_the_pie_flag(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=False), tmp_path / "vmaf")
    assert problem == "no SONAME and no PIE flag: an executable without -fPIE/-pie"


def test_rejects_a_binary_without_relro(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, relro=False), tmp_path / "vmaf")
    assert problem == "no PT_GNU_RELRO segment (-Wl,-z,relro)"


def test_rejects_partial_relro_without_bind_now(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, bind_now=False), tmp_path / "vmaf")
    assert problem == "RELRO without BIND_NOW (-Wl,-z,now)"


def test_rejects_a_binary_without_the_stack_protector(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, stack_protector=False), tmp_path / "vmaf")
    assert problem == "no __stack_chk_fail import (-fstack-protector-strong)"


def test_rejects_a_binary_without_fortified_calls(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, fortify=False), tmp_path / "vmaf")
    assert problem == "no __*_chk import (_FORTIFY_SOURCE)"


def test_rejects_the_meson_build_tree_run_path(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, runpath="$ORIGIN/../src"), tmp_path / "vmaf")
    assert problem == "RUNPATH entry escapes its directory: [$ORIGIN/../src]"


def test_rejects_an_absolute_run_path_entry(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, runpath="/build/src"), tmp_path / "vmaf")
    assert problem == "RUNPATH entry is not $ORIGIN-relative: [/build/src]"


def test_rejects_a_run_path_with_one_bad_entry_among_good_ones(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, runpath="$ORIGIN:$ORIGIN/../lib"), tmp_path / "vmaf")
    assert problem is not None
    assert "$ORIGIN/../lib" in problem


def test_rejects_a_carried_dt_rpath(tmp_path: Path) -> None:
    problem = _check_with(_stub(pie=True, rpath="$ORIGIN"), tmp_path / "vmaf")
    assert problem == "carries DT_RPATH, which the loader keeps beside RUNPATH"


def test_rejects_an_unexpected_elf_type(tmp_path: Path) -> None:
    problem = _check_with(_stub(elf_type="CORE"), tmp_path / "vmaf")
    assert problem == "unexpected ELF type CORE"


def test_rejects_a_non_elf_file(tmp_path: Path) -> None:
    problem = _check_with(_fake_readelf("not an ELF file\n", "", "", ""), tmp_path / "vmaf")
    assert problem == "not an ELF file (no header type)"


def test_readelf_failure_is_fatal(tmp_path: Path) -> None:
    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad ELF")

    with patch.object(check_release_hardening.subprocess, "run", failing_run):
        problem = check_release_hardening.check_file(Path("readelf"), tmp_path / "vmaf")
    assert problem is not None and problem.startswith("readelf failed")


def test_main_fails_when_readelf_is_absent(tmp_path: Path) -> None:
    with patch.object(check_release_hardening.shutil, "which", return_value=None):
        assert check_release_hardening.main([str(tmp_path / "vmaf")]) == 1


def test_main_passes_a_fully_hardened_file(tmp_path: Path) -> None:
    binary = tmp_path / "vmaf"
    with patch.object(check_release_hardening.shutil, "which", return_value="/usr/bin/readelf"):
        with patch.object(
            check_release_hardening.subprocess,
            "run",
            _stub(elf_type="DYN", pie=True, runpath="$ORIGIN"),
        ):
            assert check_release_hardening.main([str(binary)]) == 0


def test_main_fails_a_file_lacking_the_properties(tmp_path: Path) -> None:
    binary = tmp_path / "vmaf"
    with patch.object(check_release_hardening.shutil, "which", return_value="/usr/bin/readelf"):
        with patch.object(
            check_release_hardening.subprocess,
            "run",
            _stub(
                elf_type="DYN",
                pie=False,
                bind_now=False,
                stack_protector=False,
                fortify=False,
                runpath="$ORIGIN/../src",
            ),
        ):
            assert check_release_hardening.main([str(binary)]) == 1


def _require_linux_tools() -> None:
    if shutil.which("cc") is None or shutil.which("readelf") is None:
        pytest.skip("cc or readelf not available")


def _compile(tmp_path: Path, name: str, extra: list[str]) -> Path:
    cc = shutil.which("cc")
    assert cc is not None, "guarded by _require_linux_tools"
    source = tmp_path / "fixture.c"
    source.write_text(FIXTURE_SOURCE, encoding="utf-8")
    binary = tmp_path / name
    subprocess.run(  # noqa: S603 -- resolved host compiler, argv list, fixture source
        [cc, *extra, str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return binary


def test_compiled_hardened_fixture_is_accepted(tmp_path: Path) -> None:
    _require_linux_tools()
    binary = _compile(tmp_path, "vmaf-hardened", [*HARDEN_FLAGS, "-DCLI_MAIN"])
    assert check_release_hardening.main([str(binary)]) == 0


def test_compiled_unhardened_executable_is_rejected(tmp_path: Path) -> None:
    _require_linux_tools()
    binary = _compile(tmp_path, "vmaf-plain", ["-O2", "-no-pie", "-DCLI_MAIN"])
    assert check_release_hardening.main([str(binary)]) == 1


def test_compiled_unhardened_library_is_rejected(tmp_path: Path) -> None:
    _require_linux_tools()
    binary = _compile(tmp_path, "libplain.so", ["-O2", "-fPIC", "-shared"])
    assert check_release_hardening.main([str(binary)]) == 1
