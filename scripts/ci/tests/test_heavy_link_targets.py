#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression tests for heavy CI link target selection."""

from __future__ import annotations

import importlib
import json
import os
import re
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.lib.safe_subprocess import TextCommandResult, run

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(
    os.environ.get("HEAVY_LINK_TARGETS_SCRIPT", ROOT / "scripts/ci/heavy_link_targets.py")
)
MODULE = runpy.run_path(str(SCRIPT))
JOB_BUILD_STEP_NAMES = MODULE["JOB_BUILD_STEP_NAMES"]
PROFILES = MODULE["PROFILES"]
select_targets = MODULE["select_targets"]
validate_profile_links = MODULE["validate_profile_links"]
mcp_shared_link_paths = MODULE["mcp_shared_link_paths"]
is_test_consumed = MODULE["test_is_consumed"]
equivalent_rows = MODULE["equivalent_rows"]
option_map_from_tokens = MODULE["option_map_from_tokens"]
options_equal = MODULE["options_equal"]
parse_meson_defaults = MODULE["parse_meson_defaults"]
parse_project_defaults = MODULE["parse_project_defaults"]
tokenize_setup = MODULE["tokenize_setup"]


class HeavyLinkTargetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.build = Path(tempfile.mkdtemp()).resolve()

    def sample_fixtures(self) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        targets = [
            {
                "id": "vmaf@exe",
                "name": "vmaf",
                "type": "executable",
                "filename": [str(self.build / "tools/vmaf")],
                "build_by_default": True,
            },
            {
                "id": "dnn_test@exe",
                "name": "dnn_test",
                "type": "executable",
                "filename": [str(self.build / "test/dnn_test")],
                "build_by_default": True,
            },
            {
                "id": "other@exe",
                "name": "other",
                "type": "executable",
                "filename": [str(self.build / "test/other")],
                "build_by_default": True,
            },
            {
                "id": "optout@exe",
                "name": "optout",
                "type": "executable",
                "filename": [str(self.build / "test/optout")],
                "build_by_default": False,
            },
            {
                "id": "libvmaf@sha",
                "name": "libvmaf",
                "type": "shared library",
                "filename": [str(self.build / "src/libvmaf.so.1")],
                "build_by_default": True,
            },
        ]
        tests: list[dict[str, object]] = [
            {
                "name": "dnn_case",
                "suite": ["libvmaf:dnn"],
                "cmd": [],
                "depends": ["dnn_test@exe"],
            },
            {
                "name": "other_case",
                "suite": ["libvmaf:fast"],
                "cmd": [],
                "depends": ["other@exe"],
            },
        ]
        return targets, tests

    def test_rejects_string_target_filename(self) -> None:
        targets, tests = self.sample_fixtures()
        targets[0]["filename"] = str(self.build / "tools/vmaf")
        with self.assertRaisesRegex(ValueError, "vmaf@exe"):
            select_targets("netflix-golden", targets, tests, ["obj/a.o"], True, True, self.build)

    def test_rejects_empty_target_filename_list(self) -> None:
        targets, tests = self.sample_fixtures()
        targets[0]["filename"] = []
        with self.assertRaisesRegex(ValueError, "vmaf@exe"):
            select_targets("netflix-golden", targets, tests, ["obj/a.o"], True, True, self.build)

    def test_rejects_missing_target_filename(self) -> None:
        targets, tests = self.sample_fixtures()
        del targets[0]["filename"]
        with self.assertRaisesRegex(ValueError, "vmaf@exe"):
            select_targets("netflix-golden", targets, tests, ["obj/a.o"], True, True, self.build)

    def test_rejects_non_string_target_filename_element(self) -> None:
        targets, tests = self.sample_fixtures()
        targets[0]["filename"] = [str(self.build / "tools/vmaf"), 17]
        with self.assertRaisesRegex(ValueError, "vmaf@exe"):
            select_targets("netflix-golden", targets, tests, ["obj/a.o"], True, True, self.build)

    def test_bare_selector_matches_suite(self) -> None:
        self.assertTrue(is_test_consumed({"suite": ["libvmaf:dnn"]}, ("dnn",), ()))

    def test_bare_selector_rejects_suite_prefix_match(self) -> None:
        self.assertFalse(is_test_consumed({"suite": ["libvmaf:dnnx"]}, ("dnn",), ()))

    def test_bare_selector_matches_project(self) -> None:
        self.assertTrue(is_test_consumed({"suite": ["dnn:fast"]}, ("dnn",), ()))

    def test_suite_only_and_project_qualified_selectors(self) -> None:
        dnn = {"suite": ["libvmaf:dnn"]}
        self.assertTrue(is_test_consumed(dnn, (":dnn",), ()))
        self.assertFalse(is_test_consumed({"suite": ["dnn:fast"]}, (":dnn",), ()))
        self.assertTrue(is_test_consumed(dnn, ("libvmaf:dnn",), ()))
        self.assertFalse(is_test_consumed({"suite": ["other:dnn"]}, ("libvmaf:dnn",), ()))

    def test_dnn_twin_active_skips_unconsumed_tests(self) -> None:
        targets, tests = self.sample_fixtures()
        paths, summary = select_targets(
            "dnn",
            targets,
            tests,
            ["obj/a.o", "obj/b.o"],
            tier_full=False,
            c_core=True,
            build_dir=self.build,
        )
        self.assertIn("tools/vmaf", paths)
        self.assertIn("test/dnn_test", paths)
        self.assertNotIn("test/other", paths)
        self.assertEqual(summary["twin"], "Ubuntu gcc+DNN,Ubuntu clang+DNN")
        self.assertEqual((summary["tests_linked"], summary["tests_left"]), (1, 1))

    def test_consumed_test_selects_both_dependency_outputs(self) -> None:
        targets, tests = self.sample_fixtures()
        targets.append(
            {
                "id": "dnn_data@cus",
                "name": "dnn_data",
                "type": "custom",
                "filename": [
                    str(self.build / "test/dnn_input.bin"),
                    str(self.build / "test/dnn_expected.bin"),
                ],
                "build_by_default": False,
            }
        )
        tests[0]["depends"] = ["dnn_test@exe", "dnn_data@cus"]
        paths, _ = select_targets("dnn", targets, tests, ["obj/a.o"], False, True, self.build)
        self.assertIn("test/dnn_input.bin", paths)
        self.assertIn("test/dnn_expected.bin", paths)

    def test_profile_link_accepts_second_output_and_rejects_unknown_path(self) -> None:
        targets, _ = self.sample_fixtures()
        targets[0]["filename"] = [
            str(self.build / "tools/vmaf.debug"),
            str(self.build / "tools/vmaf"),
        ]
        with self.assertRaisesRegex(
            ValueError, "link path missing from introspection: tools/missing"
        ):
            validate_profile_links(["tools/missing"], targets, self.build)
        validate_profile_links(["tools/vmaf"], targets, self.build)

    def test_netflix_twin_active_when_tier_full(self) -> None:
        targets, tests = self.sample_fixtures()
        paths, _ = select_targets(
            "netflix-golden",
            targets,
            tests,
            ["obj/a.o"],
            tier_full=True,
            c_core=True,
            build_dir=self.build,
        )
        self.assertNotIn("test/other", paths)
        self.assertNotIn("test/dnn_test", paths)

    def test_netflix_tier_full_false_keeps_tests(self) -> None:
        targets, tests = self.sample_fixtures()
        paths, _ = select_targets(
            "netflix-golden",
            targets,
            tests,
            ["obj/a.o"],
            tier_full=False,
            c_core=True,
            build_dir=self.build,
        )
        self.assertIn("test/other", paths)

    def test_dnn_c_core_false_keeps_all_tests(self) -> None:
        targets, tests = self.sample_fixtures()
        paths, summary = select_targets(
            "dnn",
            targets,
            tests,
            ["obj/a.o"],
            tier_full=False,
            c_core=False,
            build_dir=self.build,
        )
        self.assertIn("test/other", paths)
        self.assertIn("test/dnn_test", paths)
        self.assertEqual((summary["tests_linked"], summary["tests_left"]), (2, 0))

    def test_mcp_keeps_all_default_tests(self) -> None:
        targets, tests = self.sample_fixtures()
        # bound: four consumed MCP tests
        for name in (
            "test_mcp_smoke",
            "test_compat_conformance",
            "check_exported_symbols",
            "check_exported_symbols_libvmaf",
        ):
            ident = f"{name}@exe"
            targets.append(
                {
                    "id": ident,
                    "name": name,
                    "type": "executable",
                    "filename": [str(self.build / f"test/{name}")],
                    "build_by_default": True,
                }
            )
            tests.append({"name": name, "suite": ["libvmaf:mcp"], "cmd": [], "depends": [ident]})
        paths, _ = select_targets(
            "mcp-smoke",
            targets,
            tests,
            ["obj/a.o"],
            tier_full=False,
            c_core=True,
            build_dir=self.build,
        )
        self.assertIn("test/other", paths)
        self.assertIn("src/libvmaf.so.1", paths)

    def test_mcp_shared_link_paths_selects_matching_second_output(self) -> None:
        targets = [
            {
                "id": "libvmafx@sha",
                "name": "libvmafx",
                "type": "shared library",
                "filename": [
                    str(self.build / "src/debug-symbols"),
                    str(self.build / "src/libvmafx.so.1"),
                ],
                "build_by_default": True,
            }
        ]
        self.assertEqual(mcp_shared_link_paths(targets, self.build), ["src/libvmafx.so.1"])

    def test_negative_cases_raise(self) -> None:
        targets, tests = self.sample_fixtures()
        with self.assertRaises(ValueError):
            select_targets(
                "nope",
                targets,
                tests,
                ["obj/a.o"],
                False,
                False,
                self.build,
            )
        with self.assertRaises(ValueError):
            select_targets(
                "dnn",
                targets,
                tests,
                [],
                False,
                False,
                self.build,
            )
        # bound: target fixture count
        without_vmaf = [target for target in targets if target.get("id") != "vmaf@exe"]
        with self.assertRaises(ValueError):
            select_targets("dnn", without_vmaf, tests, ["obj/a.o"], False, True, self.build)
        tests_no_dnn = [tests[1]]
        with self.assertRaises(ValueError):
            select_targets("dnn", targets, tests_no_dnn, ["obj/a.o"], False, True, self.build)

    def test_boundary_cases(self) -> None:
        targets, tests = self.sample_fixtures()
        paths, _ = select_targets(
            "dnn",
            targets,
            tests,
            ["obj/a.o", "obj/a.o"],
            False,
            True,
            self.build,
        )
        self.assertEqual(paths, sorted(set(paths)))
        paths2, _ = select_targets(
            "netflix-golden", targets, [], ["obj/a.o"], True, True, self.build
        )
        self.assertIn("tools/vmaf", paths2)


class EquivalentRowsTest(unittest.TestCase):
    def test_option_map_expands_matrix_arguments_in_order(self) -> None:
        computed = option_map_from_tokens(
            ["-Dfoo=before", "${{ matrix.meson_extra }}", "-Dfoo=after"],
            None,
            {"meson_extra": "--prefix /x --buildtype release -Dwerror=true -Dfoo=bar --prefix"},
        )
        self.assertEqual(computed, {"buildtype": "release", "werror": "true", "foo": "after"})

    def test_option_map_rejects_shell_expansion_in_matrix_argument(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported shell expansion"):
            option_map_from_tokens(
                ["${{ matrix.meson_extra }}"], None, {"meson_extra": "-Dfoo=${nested}"}
            )

    def test_drift_matches_profiles(self) -> None:
        tests = (ROOT / ".github/workflows/tests-and-quality-gates.yml").read_text(encoding="utf-8")
        matrix = (ROOT / ".github/workflows/libvmaf-build-matrix.yml").read_text(encoding="utf-8")
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        # bound: three heavy job profiles
        self.assertEqual(computed, {job: profile["twins"] for job, profile in PROFILES.items()})

    def test_os_drift_breaks_match(self) -> None:
        tests = (ROOT / ".github/workflows/tests-and-quality-gates.yml").read_text(encoding="utf-8")
        tests = re.sub(
            r"(  dnn:\n(?:.*\n)*?    runs-on: )ubuntu-26\.04",
            r"\1ubuntu-24.04",
            tests,
            count=1,
        )
        matrix = (ROOT / ".github/workflows/libvmaf-build-matrix.yml").read_text(encoding="utf-8")
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        self.assertEqual(computed["dnn"], ())

    def test_on_block_drift_breaks_match(self) -> None:
        tests = (
            (ROOT / ".github/workflows/tests-and-quality-gates.yml")
            .read_text(encoding="utf-8")
            .replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  workflow_call:\n", 1)
        )
        matrix = (ROOT / ".github/workflows/libvmaf-build-matrix.yml").read_text(encoding="utf-8")
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        self.assertEqual(computed, dict.fromkeys(PROFILES, ()))

    def test_option_drift_breaks_match(self) -> None:
        tests = (ROOT / ".github/workflows/tests-and-quality-gates.yml").read_text(encoding="utf-8")
        matrix = (
            (ROOT / ".github/workflows/libvmaf-build-matrix.yml")
            .read_text(encoding="utf-8")
            .replace(
                "-Denable_dnn=enabled -Denable_zimg=true",
                "-Denable_dnn=enabled -Denable_zimg=true -Denable_float=false",
                1,
            )
        )
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        self.assertEqual(computed["dnn"], (("Ubuntu clang+DNN", "light"),))

    def test_shell_token_drift_breaks_match(self) -> None:
        tests = (
            (ROOT / ".github/workflows/tests-and-quality-gates.yml")
            .read_text(encoding="utf-8")
            .replace(
                "$(../scripts/ci/werror-args.sh true)",
                "$(other)",
                1,
            )
        )
        matrix = (ROOT / ".github/workflows/libvmaf-build-matrix.yml").read_text(encoding="utf-8")
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        self.assertEqual(computed["netflix-golden"], ())

    def test_setup_flag_aliases_and_unknown_flag(self) -> None:
        base = option_map_from_tokens(["-Denable_float=true"], None, None)
        # bound: four differentiating Meson setup flag lists
        for extra in (
            ["--default-library=static"],
            ["--default-library", "static"],
            ["--werror"],
            ["--optimization=0"],
        ):
            other = option_map_from_tokens(["-Denable_float=true", *extra], None, None)
            self.assertFalse(options_equal(base, other, {}), extra)
        bt_space = option_map_from_tokens(["--buildtype", "release"], None, None)
        bt_eq = option_map_from_tokens(["--buildtype=release"], None, None)
        bt_d = option_map_from_tokens(["-Dbuildtype=release"], None, None)
        self.assertTrue(options_equal(bt_space, bt_eq, {}))
        self.assertTrue(options_equal(bt_eq, bt_d, {}))
        aliases = option_map_from_tokens(
            ["--warnlevel", "2", "--backend=ninja", "--strip", "--debug"], None, None
        )
        self.assertEqual(
            aliases,
            {"warning_level": "2", "backend": "ninja", "strip": "true", "debug": "true"},
        )
        self.assertEqual(
            option_map_from_tokens(["--prefix", "x", "--prefix=x", "-Dfoo=1"], None, None),
            {"foo": "1"},
        )
        with self.assertRaisesRegex(ValueError, r"unknown setup flag: --unity=on"):
            option_map_from_tokens(["--unity=on"], None, None)

    def test_project_defaults_parsed_and_merged(self) -> None:
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        meson_options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        project_defaults = parse_project_defaults(meson_build)
        self.assertEqual(project_defaults["buildtype"], "release")
        self.assertEqual(project_defaults["default_library"], "both")
        self.assertEqual(project_defaults["b_lto"], "true")
        self.assertEqual(project_defaults["c_std"], "c23,c2x,c17,none")
        defaults = {**project_defaults, **parse_meson_defaults(meson_options)}
        explicit_tokens, _ = tokenize_setup("meson setup build --buildtype release -Dwerror=true")
        implicit_tokens, _ = tokenize_setup("meson setup build -Dwerror=true")
        explicit = option_map_from_tokens(explicit_tokens, None, None)
        implicit = option_map_from_tokens(implicit_tokens, None, None)
        self.assertTrue(options_equal(explicit, implicit, defaults))
        tests = (
            (ROOT / ".github/workflows/tests-and-quality-gates.yml")
            .read_text(encoding="utf-8")
            .replace(" --buildtype=release", "", 1)
        )
        matrix = (ROOT / ".github/workflows/libvmaf-build-matrix.yml").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, meson_options, meson_build)
        self.assertEqual(computed["netflix-golden"], PROFILES["netflix-golden"]["twins"])

    def test_malformed_matrix_row_quote_is_skipped(self) -> None:
        tests = (ROOT / ".github/workflows/tests-and-quality-gates.yml").read_text(encoding="utf-8")
        matrix = (
            (ROOT / ".github/workflows/libvmaf-build-matrix.yml")
            .read_text(encoding="utf-8")
            .replace(
                "-Denable_dnn=enabled -Denable_zimg=true",
                '"-Denable_dnn=enabled \'unterminated"',
                1,
            )
        )
        options = (ROOT / "core/meson_options.txt").read_text(encoding="utf-8")
        meson_build = (ROOT / "core/meson.build").read_text(encoding="utf-8")
        computed = equivalent_rows(tests, matrix, options, meson_build)
        self.assertEqual(computed["dnn"], (("Ubuntu clang+DNN", "light"),))
        self.assertEqual(computed["netflix-golden"], PROFILES["netflix-golden"]["twins"])

    def test_workflow_steps_guard_empty_link_targets(self) -> None:
        yaml = importlib.import_module("yaml")
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/tests-and-quality-gates.yml").read_text(encoding="utf-8")
        )
        mapfile_line = 'mapfile -t targets < "${RUNNER_TEMP}/link-targets.txt"'
        guard_line = (
            'if [ "${#targets[@]}" -eq 0 ]; then '
            'echo "::error::heavy-link-targets printed no targets"; exit 2; fi'
        )
        # bound: three heavy job build steps
        for job_id, step_name in JOB_BUILD_STEP_NAMES.items():
            steps = workflow["jobs"][job_id]["steps"]
            # bound: steps in one heavy job
            matches = [step for step in steps if step.get("name") == step_name]
            self.assertEqual(len(matches), 1, job_id)
            lines = [line.strip() for line in matches[0]["run"].splitlines()]
            self.assertIn(mapfile_line, lines, job_id)
            self.assertIn(guard_line, lines, job_id)
            ninja_lines = [
                idx
                for idx, line in enumerate(lines)
                if line.startswith("ninja -C ") and line.endswith(' "${targets[@]}"')
            ]
            self.assertEqual(len(ninja_lines), 1, job_id)
            self.assertLess(lines.index(mapfile_line), lines.index(guard_line), job_id)
            self.assertLess(lines.index(guard_line), ninja_lines[0], job_id)


@unittest.skipUnless(
    os.name == "posix",
    "HISS-21: the fixture executes a POSIX Ninja stub, covered by the Linux CI tests",
)
class CliHeavyLinkTargetsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        tmp = Path(temporary.name).resolve()
        self.build = tmp / "build"
        self.info = self.build / "meson-info"
        self.info.mkdir(parents=True)
        self.ninja = tmp / "ninja"
        self.write_ninja()

    def write_introspection(
        self,
        targets: list[dict[str, object]],
        tests: list[dict[str, object]],
    ) -> None:
        (self.info / "intro-targets.json").write_text(json.dumps(targets), encoding="utf-8")
        (self.info / "intro-tests.json").write_text(json.dumps(tests), encoding="utf-8")

    def write_ninja(self) -> None:
        # bound: two compile entries, in reverse target order
        entries = [
            {
                "directory": str(self.build),
                "command": f"cc -c {name}.c -o obj/{name}.o",
                "file": f"{name}.c",
                "output": f"obj/{name}.o",
            }
            for name in ("b", "a")
        ]
        source = (
            f"#!{sys.executable}\n"
            "import sys\n"
            f"if sys.argv[1:3] != ['-C', {str(self.build)!r}]:\n"
            "    raise SystemExit('unexpected build directory')\n"
            "args = sys.argv[3:]\n"
            "if args == ['-t', 'rules']:\n"
            "    print('c_COMPILER\\ncpp_COMPILER')\n"
            "elif args == ['-t', 'compdb', 'c_COMPILER', 'cpp_COMPILER']:\n"
            f"    print({json.dumps(entries)!r})\n"
            "else:\n"
            "    raise SystemExit(f'unexpected Ninja arguments: {args!r}')\n"
        )
        self.ninja.write_text(source, encoding="utf-8")
        self.ninja.chmod(0o755)

    def run_cli(self, job: str = "netflix-golden", *, c_core: bool = True) -> TextCommandResult:
        return run(
            [
                sys.executable,
                "-B",
                str(SCRIPT),
                "--job",
                job,
                "--build-dir",
                str(self.build),
                "--tier-full",
                "true",
                "--c-core",
                "true" if c_core else "false",
                "--ninja",
                str(self.ninja),
            ],
            allowed_executables=(sys.executable,),
            capture_output=True,
            text=True,
        )

    def write_netflix_introspection(self) -> None:
        self.write_introspection(
            [
                {"id": "vmaf", "type": "executable", "filename": [str(self.build / "tools/vmaf")]},
                {
                    "id": "one",
                    "type": "executable",
                    "filename": [str(self.build / "test/one")],
                    "build_by_default": True,
                },
            ],
            [{"name": "one", "suite": ["libvmaf:fast"], "depends": ["one"]}],
        )

    def assert_netflix_summary(self, result: TextCommandResult, twin: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertTrue(result.stderr.startswith("heavy-link-targets: job=netflix-golden "))
        self.assertIn(f" twin={twin} ", result.stderr)

    def test_cli_reports_twin_and_skips_its_test_executable(self) -> None:
        self.write_netflix_introspection()
        result = self.run_cli()
        self.assert_netflix_summary(result, "Ubuntu gcc,Ubuntu clang")
        self.assertEqual(result.stdout.splitlines(), ["obj/a.o", "obj/b.o", "tools/vmaf"])

    def test_cli_without_c_core_keeps_test_executable(self) -> None:
        self.write_netflix_introspection()
        result = self.run_cli(c_core=False)
        self.assert_netflix_summary(result, "none")
        self.assertEqual(
            result.stdout.splitlines(), ["obj/a.o", "obj/b.o", "test/one", "tools/vmaf"]
        )

    def test_cli_reports_missing_link_path(self) -> None:
        self.write_introspection([], [])
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("link path missing from introspection: tools/vmaf", result.stderr)

    def test_cli_reports_missing_dnn_suite(self) -> None:
        self.write_netflix_introspection()
        result = self.run_cli("dnn")
        self.assertEqual(result.returncode, 2)
        self.assertIn("consumed test suite absent from introspection: dnn", result.stderr)

    def test_cli_reports_missing_intro_file(self) -> None:
        result = self.run_cli("dnn")
        self.assertEqual(result.returncode, 2)
        self.assertIn("intro-targets.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
