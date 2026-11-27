#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verify the workflow and apt-list clang literals name build-config.env's CLANG_MAJOR.

The Linux CI legs build with clang-22 in six workflows and install its runtime
packages from two .github/apt/*.txt lists, and all of them repeat the major as
literals because YAML cannot source build-config.env at parse time. A literal
that names another major is the drift that once split ROCm across build.yml and
libvmaf-build-matrix.yml; `check_clang_major()` refuses it, so bumping the
compiler moves every mirror -- comments included -- in the same pull request.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "workflow_versions", ROOT / "scripts/ci/check-workflow-versions.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClangMajorGate(Protocol):
    def check_clang_major(self, root: Path, want: str | None) -> list[str]: ...

    def load_config(self, root: Path) -> dict[str, str]: ...


GATE = cast(ClangMajorGate, MODULE)
WANT = "22"

WORKFLOW = (
    "jobs:\n"
    "  build:\n"
    "    steps:\n"
    "      - name: Install dependencies\n"
    "        run: |\n"
    "          sudo bash scripts/ci/install-llvm.sh {major}\n"
    "      - name: Build\n"
    "        env:\n"
    "          CC: ccache clang-{major}\n"
    "          CXX: ccache clang++-{major}\n"
    "          LDFLAGS: -fuse-ld=lld-{major}\n"
)

APT_LIST = (
    "# lld-{major} keeps the linker in the same LLVM major as the\n"
    "# clang-{major} installed above; clang-tools-{major} lints.\n"
    "clang-tools-{major}\n"
    "libclang-rt-{major}-dev\n"
    "libomp-{major}-dev\n"
    "lld-{major}\n"
)


class ClangMajorSingleSource(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.write(".github/workflows/build.yml", WORKFLOW.format(major=WANT))
        self.write(".github/apt/sanitizers.txt", APT_LIST.format(major=WANT))

    def write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_matching_literals_pass(self) -> None:
        self.assertEqual(GATE.check_clang_major(self.repo, WANT), [])

    def test_repository_literals_match_config(self) -> None:
        want = GATE.load_config(ROOT).get("CLANG_MAJOR")
        self.assertEqual(GATE.check_clang_major(ROOT, want), [])

    def test_missing_config_value_fails(self) -> None:
        self.assertEqual(
            GATE.check_clang_major(self.repo, None),
            ["build-config.env defines no CLANG_MAJOR"],
        )

    def test_differing_workflow_literal_fails(self) -> None:
        self.write(".github/workflows/build.yml", WORKFLOW.format(major=WANT))
        self.write(".github/workflows/other.yml", WORKFLOW.format(major="21"))
        problems = GATE.check_clang_major(self.repo, WANT)
        self.assertEqual(len(problems), 4, problems)
        self.assertTrue(all("pins LLVM major 21" in problem for problem in problems))

    def test_differing_apt_literal_fails(self) -> None:
        self.write(".github/apt/sanitizers.txt", APT_LIST.format(major=WANT))
        self.write(
            ".github/apt/sanitizers.txt",
            APT_LIST.format(major=WANT).replace(f"-{WANT}", "-20"),
        )
        problems = GATE.check_clang_major(self.repo, WANT)
        # Every occurrence, comments included: the apt list names the major in
        # three comment lines and four package lines.
        self.assertEqual(len(problems), 7, problems)
        self.assertTrue(all("pins LLVM major 20" in problem for problem in problems))

    def test_no_literal_at_all_fails(self) -> None:
        self.write(".github/workflows/build.yml", "jobs: {}\n")
        self.write(".github/apt/sanitizers.txt", "ninja-build\n")
        problems = GATE.check_clang_major(self.repo, WANT)
        self.assertEqual(len(problems), 1)
        self.assertIn("no versioned clang/LLVM literal", problems[0])


if __name__ == "__main__":
    unittest.main()
