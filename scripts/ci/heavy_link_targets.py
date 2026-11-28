#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Select Ninja link targets for heavy CI jobs (ADR-3625)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

PROFILES: dict[str, dict[str, Any]] = {
    "netflix-golden": {
        "links": ("tools/vmaf",),
        "test_suites": (),
        "test_names": (),
        "twins": (("Ubuntu gcc", "full"), ("Ubuntu clang", "full")),
    },
    "dnn": {
        "links": ("tools/vmaf",),
        "test_suites": ("dnn",),
        "test_names": (),
        "twins": (("Ubuntu gcc+DNN", "light"), ("Ubuntu clang+DNN", "light")),
    },
    "mcp-smoke": {
        "links": ("tools/vmaf",),
        "test_suites": (),
        "test_names": (
            "test_mcp_smoke",
            "test_compat_conformance",
            "check_exported_symbols",
            "check_exported_symbols_libvmaf",
        ),
        "twins": (),
    },
}

JOB_BUILD_STEP_NAMES = {
    "netflix-golden": "Build (CPU, release)",
    "dnn": "Build libvmaf (dnn enabled)",
    "mcp-smoke": "Build libvmaf (full MCP transport matrix)",
}

MCP_SHARED_PREFIXES = ("libvmaf.so", "libvmafx.so")


def load_compile_helpers() -> Any:
    script = Path(__file__).resolve().parent / "write-compile-commands.py"
    spec = importlib.util.spec_from_file_location("write_compile_commands", script)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load write-compile-commands.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def object_outputs(ninja: str, build: Path, helpers: Any) -> list[str]:
    rules = helpers.compiler_rules(ninja, build)
    raw = helpers.run_ninja(ninja, build, "-t", "compdb", *rules)
    entries = helpers.parse_database(raw)
    # bound: one compdb entry per compile rule output
    outputs: list[str] = []
    for index, entry in enumerate(entries):
        output = entry.get("output")
        if not output:
            decoded = json.loads(raw)
            output = decoded[index].get("output") if index < len(decoded) else None
            if not output:
                raise ValueError(f"compile entry {index} has no output path")
        outputs.append(str(output))
    return outputs


def target_map(targets_json: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    # bound: intro-targets.json entry count
    for row in targets_json:
        ident = row.get("id")
        if isinstance(ident, str):
            mapping[ident] = row
    return mapping


def rel_filename(build_dir: Path, filename: str) -> str:
    path = Path(filename)
    if path.is_absolute():
        try:
            return path.relative_to(build_dir.resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def target_outputs(row: dict[str, Any], build_dir: Path) -> list[str]:
    filenames = row.get("filename")
    # bound: filename list length per target
    if (
        not isinstance(filenames, list)
        or not filenames
        or any(not isinstance(filename, str) for filename in filenames)
    ):
        raise ValueError(f"target {row.get('id')!r} filename must be a nonempty list of strings")
    # bound: filename list length per target
    return [rel_filename(build_dir, filename) for filename in filenames]


def mcp_shared_link_paths(targets_json: list[dict[str, Any]], build_dir: Path) -> list[str]:
    paths: list[str] = []
    # bound: intro-targets.json entry count
    for row in targets_json:
        if row.get("type") != "shared library":
            continue
        # bound: filename list length per shared-library target
        for path in target_outputs(row, build_dir):
            if Path(path).name.startswith(MCP_SHARED_PREFIXES):
                paths.append(path)
    return paths


def test_in_suite(test: dict[str, Any], selector: str) -> bool:
    tags = test.get("suite")
    if not isinstance(tags, list):
        return False
    selected_project, _, selected_suite = selector.partition(":")
    # bound: Meson suite tags per test
    for tag in tags:
        if not isinstance(tag, str):
            continue
        project, _, suite = tag.partition(":")
        if not selected_suite:
            matches = selected_project in (project, suite)
        else:
            matches = suite == selected_suite and selected_project in ("", project)
        if matches:
            return True
    return False


def test_is_consumed(test: dict[str, Any], suites: tuple[str, ...], names: tuple[str, ...]) -> bool:
    name = test.get("name")
    if isinstance(name, str) and name in names:
        return True
    # bound: configured consumed suite selectors
    return any(test_in_suite(test, selector) for selector in suites)


def depends_paths(
    depends: list[str],
    by_id: dict[str, dict[str, Any]],
    build_dir: Path,
    *,
    default_executables_only: bool,
) -> list[str]:
    paths: list[str] = []
    # bound: depends list length per test
    for ident in depends:
        if not isinstance(ident, str):
            continue
        row = by_id.get(ident)
        if row is None:
            continue
        if default_executables_only and (
            row.get("type") != "executable" or not row.get("build_by_default")
        ):
            continue
        paths.extend(target_outputs(row, build_dir))
    return paths


def twin_state(profile: dict[str, Any], tier_full: bool, c_core: bool) -> tuple[bool, str]:
    if not c_core or not profile["twins"]:
        return False, "none"
    active: list[str] = []
    # bound: at most two twin rows per profile
    for name, leg_tier in profile["twins"]:
        if leg_tier == "light" or tier_full:
            active.append(name)
    if not active:
        return False, "none"
    return True, ",".join(active)


def validate_profile_links(
    links: list[str],
    targets_json: list[dict[str, Any]],
    build_dir: Path,
) -> None:
    # bound: intro-targets.json entry count
    known_outputs = {
        path
        for row in targets_json
        # bound: filename list length per target
        for path in target_outputs(row, build_dir)
    }
    # bound: configured link paths (small)
    for path in links:
        if path not in known_outputs:
            raise ValueError(f"link path missing from introspection: {path}")


def validate_profile_tests(profile: dict[str, Any], tests_json: list[dict[str, Any]]) -> None:
    # bound: intro-tests.json entry count
    test_names = {str(test.get("name", "")) for test in tests_json if isinstance(test, dict)}
    # bound: configured consumed test names (small)
    for name in profile["test_names"]:
        if name not in test_names:
            raise ValueError(f"consumed test name absent from introspection: {name}")
    # bound: configured consumed suites (small)
    for suite in profile["test_suites"]:
        # bound: intro-tests.json entry count
        found = any(test_in_suite(test, suite) for test in tests_json if isinstance(test, dict))
        if not found:
            raise ValueError(f"consumed test suite absent from introspection: {suite}")


def merge_test_links(
    tests_json: list[dict[str, Any]],
    profile: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    twin_active: bool,
    build_dir: Path,
) -> tuple[set[str], int, int]:
    link_targets: set[str] = set()
    linked = 0
    left = 0
    # bound: intro-tests.json entry count
    for test in tests_json:
        if not isinstance(test, dict):
            continue
        is_consumed = test_is_consumed(test, profile["test_suites"], profile["test_names"])
        if not is_consumed and twin_active:
            left += 1
            continue
        depends = test.get("depends")
        if not isinstance(depends, list):
            depends = []
        linked += 1
        link_targets.update(
            depends_paths(depends, by_id, build_dir, default_executables_only=not is_consumed)
        )
    return link_targets, linked, left


def select_targets(
    job: str,
    targets_json: list[dict[str, Any]],
    tests_json: list[dict[str, Any]],
    objects: list[str],
    tier_full: bool,
    c_core: bool,
    build_dir: Path,
) -> tuple[list[str], dict[str, Any]]:
    if job not in PROFILES:
        raise ValueError(f"unknown heavy job id: {job}")
    if not objects:
        raise ValueError("object list is empty")
    profile = PROFILES[job]
    by_id = target_map(targets_json)
    links = list(profile["links"])
    if job == "mcp-smoke":
        links.extend(mcp_shared_link_paths(targets_json, build_dir))
    validate_profile_links(links, targets_json, build_dir)
    validate_profile_tests(profile, tests_json)
    twin_active, twin_name = twin_state(profile, tier_full, c_core)
    test_links, tests_linked, tests_left = merge_test_links(
        tests_json, profile, by_id, twin_active, build_dir
    )
    result = sorted(set(objects) | set(links) | test_links)
    if not result:
        raise ValueError("link target list would be empty")
    reason = (
        "matrix twin covers test links"
        if twin_active
        else "no matching matrix twin or c_core false"
    )
    summary = {
        "job": job,
        "objects": len(objects),
        "links": len(links),
        "tests_linked": tests_linked,
        "tests_left": tests_left,
        "twin": twin_name,
        "reason": reason,
    }
    return result, summary


def parse_meson_defaults(text: str) -> dict[str, str]:
    defaults: dict[str, str] = {}
    pattern = re.compile(
        r"^option\('([^']+)',\s*\n\s*type:\s*'([^']+)'.*?\n\s*value:\s*([^,\n]+)",
        re.MULTILINE | re.DOTALL,
    )
    # bound: meson_options.txt option count
    for match in pattern.finditer(text):
        defaults[match.group(1)] = match.group(3).strip().strip("'\"")
    return defaults


def strip_meson_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    # bound: characters in one meson.build line
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            escaped = char == "\\"
            if char == quote:
                quote = None
        elif char == "#":
            return line[:index]
        elif char in {"'", '"'}:
            quote = char
    return line


def parse_project_defaults(meson_build_text: str) -> dict[str, str]:
    # bound: lines in meson.build
    uncommented = "\n".join(strip_meson_comment(line) for line in meson_build_text.splitlines())
    block = re.search(
        r"\bdefault_options\s*:\s*\[((?:'[^']*'|\"[^\"]*\"|[^\]])*)\]",
        uncommented,
        re.DOTALL,
    )
    if block is None:
        return {}
    defaults: dict[str, str] = {}
    # bound: quoted entries in project default_options
    for match in re.finditer(r"'([^']*)'|\"([^\"]*)\"", block.group(1)):
        entry = match.group(1) if match.group(1) is not None else match.group(2)
        if entry is not None and "=" in entry:
            key, value = entry.split("=", 1)
            defaults[key.strip()] = value.strip()
    return defaults


def join_continuations(block: str) -> str:
    lines: list[str] = []
    # bound: run block line count in workflow step
    for line in block.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            lines.append(stripped[:-1].rstrip())
        else:
            lines.append(stripped)
    return " ".join(lines)


WERROR_ARGS = re.compile(r"\$\([^)]*werror-args\.sh\s+([^)]+)\)")


def strip_setup_paths(parts: list[str]) -> list[str]:
    if "setup" not in parts:
        raise ValueError("meson setup command not found")
    index = parts.index("setup") + 1
    # bound: at most two setup path arguments
    while index < len(parts) and not parts[index].startswith("-"):
        if parts[index].startswith("$("):
            break
        index += 1
    return parts[index:]


def tokenize_setup(command: str) -> tuple[list[str], str | None]:
    werror_match = WERROR_ARGS.search(command)
    werror_arg = werror_match.group(1).strip().strip('"') if werror_match else None
    joined = join_continuations(WERROR_ARGS.sub("", command))
    parts = shlex.split(joined, posix=True)
    return strip_setup_paths(parts), werror_arg


def expand_matrix_token(token: str, row: dict[str, Any]) -> str | None:
    match = re.fullmatch(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", token)
    if match is None:
        return None
    value = row.get(match.group(1))
    if value is None:
        return ""
    return str(value)


def werror_options(werror_arg: str | None, row: dict[str, Any] | None) -> dict[str, str]:
    if werror_arg is None:
        return {}
    if row is not None and "${{" in werror_arg:
        expanded = expand_matrix_token(werror_arg, row)
        if expanded is None:
            raise ValueError(f"unsupported shell expansion in setup: {werror_arg}")
        werror_arg = expanded
    return {"werror-args": werror_arg}


SETUP_VALUE_ALIASES: dict[str, str | None] = {
    # Install location only; it does not change the build tree the tests link.
    "--prefix": None,
    "--buildtype": "buildtype",
    "--default-library": "default_library",
    "--optimization": "optimization",
    "--warnlevel": "warning_level",
    "--backend": "backend",
}

SETUP_BOOL_FLAGS: dict[str, str] = {
    "--werror": "werror",
    "--strip": "strip",
    "--debug": "debug",
}


def consume_value_alias(
    arg: str,
    key: str | None,
    has_eq: str,
    inline_value: str,
    next_arg: str | None,
    options: dict[str, str],
) -> int:
    value = inline_value if has_eq else next_arg
    if value is None:
        if key is not None:
            raise ValueError(f"unknown setup flag: {arg}")
        return 1
    if key is not None:
        if value.startswith("$(") or "${" in value:
            raise ValueError(f"unsupported shell expansion in setup: {value}")
        options[key] = value
    return 1 if has_eq else 2


def consume_setup_option(arg: str, next_arg: str | None, options: dict[str, str]) -> int:
    if arg.startswith("$(") or "${" in arg:
        raise ValueError(f"unsupported shell expansion in setup: {arg}")
    if arg.startswith("-D") and "=" in arg:
        key, value = arg[2:].split("=", 1)
        options[key] = value
        return 1
    if arg in SETUP_BOOL_FLAGS:
        options[SETUP_BOOL_FLAGS[arg]] = "true"
        return 1
    flag, has_eq, inline_value = arg.partition("=")
    if flag in SETUP_VALUE_ALIASES:
        return consume_value_alias(
            arg, SETUP_VALUE_ALIASES[flag], has_eq, inline_value, next_arg, options
        )
    if not arg.startswith("build"):
        raise ValueError(f"unknown setup flag: {arg}")
    return 1


def option_map_from_tokens(
    tokens: list[str], werror_arg: str | None, row: dict[str, Any] | None
) -> dict[str, str]:
    options = werror_options(werror_arg, row)
    tokens = list(tokens)
    i = 0
    expansion_end = 0
    # bound: original Meson argv plus one expansion per original argument
    while i < len(tokens):
        arg = tokens[i]
        in_expansion = i < expansion_end
        expanded = expand_matrix_token(arg, row) if row and not in_expansion else None
        if expanded is None:
            # A flag at the end of an expansion cannot consume an outer argument.
            end = expansion_end if in_expansion else len(tokens)
            next_arg = tokens[i + 1] if i + 1 < end else None
            i += consume_setup_option(arg, next_arg, options)
            continue
        i += 1
        if expanded:
            sub_args, _ = tokenize_setup(f"meson setup x {expanded}")
            tokens[i:i] = sub_args
            expansion_end = i + len(sub_args)
    return options


def effective_options(options: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    merged = dict(defaults)
    merged.update(options)
    return merged


def normalize_bool(value: str) -> str:
    return value.strip().lower()


def options_equal(left: dict[str, str], right: dict[str, str], defaults: dict[str, str]) -> bool:
    left_eff = effective_options(left, defaults)
    right_eff = effective_options(right, defaults)
    keys = set(left_eff) | set(right_eff)
    # bound: meson option count
    for key in keys:
        lv = left_eff.get(key, "")
        rv = right_eff.get(key, "")
        if lv == "" or rv == "":
            if lv != rv:
                return False
            continue
        if normalize_bool(lv) in {"true", "false"} or normalize_bool(rv) in {
            "true",
            "false",
        }:
            if normalize_bool(lv) != normalize_bool(rv):
                return False
            continue
        if lv != rv:
            return False
    return True


def workflow_on_block(text: str) -> str:
    match = re.search(r"^on:\n(.*?)(?:^[\w-]+:|^\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


MESON_SETUP = re.compile(r"(?m)^[ \t]*(meson setup\b(?:[^\n]*\\\n)*[^\n]*)")


def job_meson_setup(workflow: dict[str, Any], job_id: str) -> tuple[str, str]:
    job = workflow["jobs"][job_id]
    os_label = str(job.get("runs-on", ""))
    step_name = JOB_BUILD_STEP_NAMES[job_id]
    # bound: steps in one job
    for step in job.get("steps") or []:
        if step.get("name") != step_name:
            continue
        run = step.get("run")
        if not isinstance(run, str):
            break
        match = MESON_SETUP.search(run)
        if match:
            return match.group(1), os_label
    raise ValueError(f"build step not found for job {job_id}")


def matrix_configure_command(matrix_doc: dict[str, Any]) -> str:
    # bound: steps in libvmaf-build job
    for step in matrix_doc["jobs"]["libvmaf-build"].get("steps") or []:
        if step.get("name") != "Configure libvmaf":
            continue
        run = step.get("run")
        if not isinstance(run, str):
            break
        match = MESON_SETUP.search(run)
        if match:
            return match.group(1)
    raise ValueError("matrix Configure libvmaf step not found")


def substitute_matrix_line(text: str, row: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        value = row.get(match.group(1))
        return "" if value is None else str(value)

    return re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", repl, text)


def equivalent_rows(
    tests_workflow_text: str,
    matrix_workflow_text: str,
    meson_options_text: str,
    meson_build_text: str,
) -> dict[str, tuple[tuple[str, str], ...]]:
    # Only the workflow drift check needs PyYAML.
    yaml = importlib.import_module("yaml")

    tests_doc = yaml.safe_load(tests_workflow_text)
    matrix_doc = yaml.safe_load(matrix_workflow_text)
    if workflow_on_block(tests_workflow_text) != workflow_on_block(matrix_workflow_text):
        return dict.fromkeys(PROFILES, ())
    defaults = {
        **parse_project_defaults(meson_build_text),
        **parse_meson_defaults(meson_options_text),
    }
    matrix_template = matrix_configure_command(matrix_doc)
    rows = matrix_doc["jobs"]["libvmaf-build"]["strategy"]["matrix"]["include"]
    result: dict[str, tuple[tuple[str, str], ...]] = {}
    # bound: three heavy jobs
    for job_id in PROFILES:
        setup_text, job_os = job_meson_setup(tests_doc, job_id)
        try:
            job_tokens, job_werror = tokenize_setup(setup_text)
            job_opts = option_map_from_tokens(job_tokens, job_werror, None)
        except ValueError:
            result[job_id] = ()
            continue
        matched: list[tuple[str, str]] = []
        # bound: matrix include rows
        for row in rows:
            name = str(row.get("name", ""))
            row_os = str(row.get("os", ""))
            if row_os != job_os:
                continue
            row_setup = substitute_matrix_line(matrix_template, row)
            try:
                row_tokens, row_werror = tokenize_setup(row_setup)
                row_opts = option_map_from_tokens(row_tokens, row_werror, row)
            except ValueError:
                continue
            if options_equal(job_opts, row_opts, defaults):
                matched.append((name, str(row.get("tier", ""))))
        result[job_id] = tuple(matched)
    return result


def parse_bool_flag(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, choices=sorted(PROFILES))
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--tier-full", choices=("true", "false"), required=True)
    parser.add_argument("--c-core", choices=("true", "false"), required=True)
    parser.add_argument("--ninja", default="ninja")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        build = args.build_dir.resolve(strict=True)
        targets_path = build / "meson-info" / "intro-targets.json"
        tests_path = build / "meson-info" / "intro-tests.json"
        targets_json = json.loads(targets_path.read_text(encoding="utf-8"))
        tests_json = json.loads(tests_path.read_text(encoding="utf-8"))
        helpers = load_compile_helpers()
        objects = object_outputs(args.ninja, build, helpers)
        paths, summary = select_targets(
            args.job,
            targets_json,
            tests_json,
            objects,
            parse_bool_flag(args.tier_full),
            parse_bool_flag(args.c_core),
            build,
        )
        # bound: selected Ninja target count
        for path in paths:
            print(path)
        print(
            "heavy-link-targets: "
            f"job={summary['job']} objects={summary['objects']} "
            f"links={summary['links']} tests_linked={summary['tests_linked']} "
            f"tests_left={summary['tests_left']} twin={summary['twin']} "
            f"reason={summary['reason']}",
            file=sys.stderr,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"heavy-link-targets: error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
