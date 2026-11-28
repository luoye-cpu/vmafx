#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check bash scripts/ci/apt-install-cached.sh in YAML-composed literal run blocks.
Allow configure, refresh, or install .github/apt/<list>.txt [--no-install-recommends].
Driver packages and dotted package names (possible apt regexes) require the flag.
Separate tokens with ASCII space or tab. Only column-zero # starts a list comment.
Tokens are [a-z0-9][a-z0-9+.-]* with optional :[a-z0-9]+, then optional
=[A-Za-z0-9.+~:-]+ or /[a-z0-9.-]+. Caps bound YAML files, nodes, bytes and install
calls per file, and bytes per list. More-indented and backslash-continued forms fail closed.
Invalid UTF-8 in a workflow or package list is reported at its byte offset.
A call that never writes apt-install-cached on one line goes unseen. A form inside a
Bash quoted string or heredoc is accepted with the arguments it shows.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from collections.abc import Iterator
from collections.abc import Set as AbstractSet
from io import StringIO
from itertools import chain
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # The hook environment omits PyYAML stubs.

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests-and-quality-gates.yml"
DRIVER_RECOMMENDING: set[str] = {"libcudart12"}
# Largest tracked workflow .github/workflows/required-aggregator.yml is 65,234 bytes (wc -c); fourfold.
MAX_WORKFLOW_BYTES = 262_144
# Largest .github/apt/*.txt is tidy-ratchet.txt at 810 bytes (wc -c).
MAX_LIST_BYTES = 16_384
# git grep -c 'apt-install-cached.sh install' -- .github/workflows/ counts 8 calls.
MAX_INSTALL_CALLS = 64

# Measured 55 workflows + 7 composite actions, 0 .yaml files. Allow twice that total.
MAX_YAML_FILES = 124
# Largest tracked tree: docker-publish-operator-node.yml, 2,310 nodes; allow fourfold.
MAX_YAML_NODES = 9_240

APT_QUALIFIER_RE = re.compile(r"[:=/]")
PACKAGE_TOKEN = re.compile(
    r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?(?:=[A-Za-z0-9.+~:-]+|/[a-z0-9.-]+)?"
)
COMMAND_PREFIX = r"bash scripts/ci/apt-install-cached\.sh "
NO_INSTALL_FORM = re.compile(COMMAND_PREFIX + r"(?:configure|refresh)[ \t]*")
INSTALL_FORM = re.compile(
    COMMAND_PREFIX + r"install (?P<list_path>\.github/apt/[a-z0-9][a-z0-9-]*\.txt)"
    r"(?P<flag> --no-install-recommends)?[ \t]*"
)
UNSUPPORTED_FORM = (
    "unsupported apt-install-cached.sh form; write a plain command at literal run block "
    "indentation (see scripts/ci/tests/test_apt_install_recommends.py)"
)


def workflow_under_test() -> Path:
    """Return WORKFLOW_UNDER_TEST when set, otherwise the tracked workflow."""
    override = os.environ.get("WORKFLOW_UNDER_TEST")
    return Path(override) if override else DEFAULT_WORKFLOW


def read_bounded_utf8(path: Path, max_bytes: int) -> tuple[str | None, str | None]:
    """Read at most max_bytes + 1 bytes and report size or UTF-8 errors."""
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return None, f"{path}: exceeds {max_bytes} bytes"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as error:
        return None, f"{path}: invalid UTF-8 at byte {error.start}"


def _requires_flag(token: str, driver_recommending: AbstractSet[str]) -> bool:
    name = APT_QUALIFIER_RE.split(token, maxsplit=1)[0]
    return "." in name or (
        not token.endswith("-") and name.removesuffix("+") in driver_recommending
    )


def read_package_list(path: Path) -> tuple[set[str], list[str]]:
    """Read a bounded apt list, reporting unsupported tokens at their source lines."""
    text, err = read_bounded_utf8(path, MAX_LIST_BYTES)
    if text is None:
        assert err is not None
        return set(), [err]
    packages: set[str] = set()
    problems: list[str] = []
    for lineno, raw in enumerate(StringIO(text), start=1):
        if raw.startswith("#"):
            continue
        for token in re.findall(r"[^ \t]+", raw.removesuffix("\r\n").removesuffix("\n")):
            if not PACKAGE_TOKEN.fullmatch(token):
                problems.append(f"{path}:{lineno}: invalid package token {token!r}")
            else:
                packages.add(token)
    return packages, problems


def _install_problems(
    install: re.Match[str], repo_root: Path, driver_recommending: AbstractSet[str]
) -> list[str]:
    list_path = install["list_path"]
    try:
        packages, problems = read_package_list(repo_root / list_path)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return [f"missing apt list {list_path}"]
    if install["flag"] is None:
        drivers = sorted(pkg for pkg in packages if _requires_flag(pkg, driver_recommending))
        if drivers:
            problems.append(
                f"{list_path} names {', '.join(drivers)} without --no-install-recommends"
            )
    return problems


def _literal_runs(root: yaml.Node | None) -> set[yaml.ScalarNode] | None:
    """Collect literal run values. Count each composed node once, including alias targets."""
    stack = [root] if root is not None else []
    seen: set[yaml.Node] = set()
    runs: set[yaml.ScalarNode] = set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if len(seen) > MAX_YAML_NODES:
            return None
        if isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            stack.extend(chain.from_iterable(node.value))
            runs.update(
                value
                for key, value in node.value
                if isinstance(key, yaml.ScalarNode)
                and key.value == "run"
                and isinstance(value, yaml.ScalarNode)
                and value.style == "|"
                # An alias points back to the scalar's definition, possibly under another key.
                and key.end_mark.index <= value.start_mark.index
            )
    return runs


def _eligible_lines(block: yaml.ScalarNode, source: list[str]) -> Iterator[int]:
    """Match source indentation and Bash line boundaries against the composed value."""
    continued = False
    for lineno, raw in enumerate(block.value.splitlines(keepends=True), block.start_mark.line + 2):
        line = raw.removesuffix("\n")
        # YAML preserves Unicode line/paragraph separators in the literal value.
        # Bash does not treat them as newlines.
        content = line.rstrip("\u2028\u2029")
        if lineno > len(source):
            break
        stripped = source[lineno - 1].lstrip(" ")
        if stripped != content.lstrip(" "):
            break
        if not continued and stripped == line:
            yield lineno
        backslashes = len(line) - len(line.rstrip("\\"))
        continued = line != content or backslashes % 2 == 1


def check_workflow_text(
    text: str,
    repo_root: Path = REPO_ROOT,
    source_name: str = ".github/workflows/tests-and-quality-gates.yml",
    driver_recommending: AbstractSet[str] = DRIVER_RECOMMENDING,
) -> list[str]:
    """Check apt installer mentions in one bounded workflow or composite action."""
    if len(text.encode("utf-8")) > MAX_WORKFLOW_BYTES:
        return [f"{source_name}: exceeds {MAX_WORKFLOW_BYTES} bytes"]
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError) as error:
        return [f"{source_name}: unparseable YAML: {type(error).__name__}"]
    runs = _literal_runs(root)
    if runs is None:
        return [f"{source_name}: exceeds {MAX_YAML_NODES} YAML nodes"]
    lines = text.splitlines()
    eligible = {lineno for run in runs for lineno in _eligible_lines(run, lines)}
    findings: list[str] = []
    install_count = 0
    for lineno, line in enumerate(lines, 1):
        if "apt-install-cached" not in line:
            continue
        command = line.lstrip(" ")
        if lineno in eligible and NO_INSTALL_FORM.fullmatch(command):
            continue
        location = f"{source_name}:{lineno}"
        install = INSTALL_FORM.fullmatch(command) if lineno in eligible else None
        if install is None:
            findings.append(f"{location}: {UNSUPPORTED_FORM}")
            continue
        install_count += 1
        if install_count > MAX_INSTALL_CALLS:
            findings.append(f"{location}: exceeds {MAX_INSTALL_CALLS} install calls")
            break
        problems = _install_problems(install, repo_root, driver_recommending)
        findings.extend(f"{location}: {problem}" for problem in problems)
    return findings


def check_workflow(workflow_path: Path | None = None, repo_root: Path = REPO_ROOT) -> list[str]:
    """Check a workflow file on disk against driver-recommending apt package lists."""
    target = workflow_path if workflow_path is not None else workflow_under_test()
    text, err = read_bounded_utf8(target, MAX_WORKFLOW_BYTES)
    if text is None:
        assert err is not None
        return [err]
    return check_workflow_text(text, repo_root=repo_root, source_name=str(target))


def check_yaml_files(repo_root: Path) -> list[str]:
    """Glob local files too: an untracked workflow or action must also fail closed."""
    github = repo_root / ".github"
    patterns = (
        "workflows/*.yml",
        "workflows/*.yaml",
        "actions/**/action.yml",
        "actions/**/action.yaml",
    )
    paths = chain.from_iterable(github.glob(pattern) for pattern in patterns)
    files: set[Path] = set()
    for path in paths:
        files.add(path)
        if len(files) > MAX_YAML_FILES:
            return [f"exceeds {MAX_YAML_FILES} YAML files"]
    return [finding for path in sorted(files) for finding in check_workflow(path, repo_root)]


class AptInstallRecommendsTest(unittest.TestCase):
    install = "bash scripts/ci/apt-install-cached.sh install"
    list_rel = ".github/apt/cov-12.txt"
    call = f"{install} {list_rel}"
    configure = "bash scripts/ci/apt-install-cached.sh configure"
    refresh = "bash scripts/ci/apt-install-cached.sh refresh"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.list_path = self.root / self.list_rel
        self.list_path.parent.mkdir(parents=True)
        self.list_path.write_text("libcudart12\n", encoding="utf-8")

    def check(self, text: str) -> list[str]:
        return check_workflow_text(text, self.root, "fixture.yml")

    def test_checked_in_workflow_holds(self) -> None:
        target = workflow_under_test()
        self.assertEqual(check_workflow(target), [])
        text, _ = read_bounded_utf8(target, MAX_WORKFLOW_BYTES)
        self.assertIn(self.install, text or "")

    def test_driver_package_requires_flag_at_install_line(self) -> None:
        for token in ("libcudart12", "libcudart12:amd64", "libcudart12=12", "libcudart12/stable"):
            with self.subTest(token=token):
                self.list_path.write_text(token, encoding="utf-8")
                self.assertEqual(
                    self.check(f"- run: |\n    {self.call}\n"),
                    [
                        f"fixture.yml:2: {self.list_rel} names {token} without --no-install-recommends"
                    ],
                )

    def test_accepted_literal_forms(self) -> None:
        self.list_path.write_text("pkg:arm64 pkg=1:2.3~rc1-4+A pkg/stable-1.0", encoding="utf-8")
        for header in ("run: |", "run: |-", "run: |+", "- run: |"):
            for call in (
                self.call,
                self.call + " --no-install-recommends",
                self.configure,
                self.refresh,
            ):
                with self.subTest(header=header, call=call):
                    self.assertEqual(self.check(f"  {header}\r\n\n      {call} \t\r\n"), [])

    def test_arch_with_version_or_release(self) -> None:
        self.list_path.write_text("pkg:amd64=1.2 pkg:amd64/stable", encoding="utf-8")
        self.assertEqual(self.check(f"run: |\n  {self.call}"), [])

    def test_dotted_name_requires_flag(self) -> None:
        self.list_path.write_text("libcudart.2", encoding="utf-8")
        self.assertEqual(
            self.check(f"run: |\n  {self.call}"),
            [f"fixture.yml:2: {self.list_rel} names libcudart.2 without --no-install-recommends"],
        )
        self.assertEqual(self.check(f"run: |\n  {self.call} --no-install-recommends"), [])

    def test_previous_line_backslash_parity(self) -> None:
        for count in (0, 1, 2, 3):
            with self.subTest(count=count):
                previous = "printf -- --install-recommends | xargs " + "\\" * count
                self.assertEqual(
                    self.check(f"run: |\n  {previous}\n  {self.call} --no-install-recommends"),
                    [f"fixture.yml:3: {UNSUPPORTED_FORM}"] if count % 2 else [],
                )

    def test_backslash_with_intervening_line(self) -> None:
        self.assertEqual(self.check(f"run: |\n  printf x | xargs \\\n\n  {self.refresh}\n"), [])
        self.assertEqual(
            self.check(f"run: |\n  printf x | xargs \\\n  # note\n  {self.refresh}\n"), []
        )
        # Bash ends the comment at the newline. This finding is a fail-closed false positive.
        self.assertEqual(
            self.check(f"run: |\n  printf x | xargs\n  # note \\\n  {self.refresh}\n"),
            [f"fixture.yml:4: {UNSUPPORTED_FORM}"],
        )

    def test_list_without_driver_passes_without_flag(self) -> None:
        self.list_path.write_text("# libcudart12\n\nninja-build libcudart12-\n", encoding="utf-8")
        self.assertEqual(self.check(f"run: |\n  {self.call}"), [])

    def test_invalid_package_token_reports_both_lines(self) -> None:
        for entry, token in (
            ('"libcudart12"', '"libcudart12"'),
            ("libcudart12\\", "libcudart12\\"),
            ("$PKG", "$PKG"),
            ("Libcudart12", "Libcudart12"),
            ("  # indented comment", "#"),
            ("pkg\u00a0other", "pkg\u00a0other"),
        ):
            with self.subTest(entry=entry):
                self.list_path.write_text(f"# comment\nninja-build\n{entry}\n", encoding="utf-8")
                self.assertEqual(
                    self.check(f"run: |\n  {self.call} --no-install-recommends"),
                    [f"fixture.yml:2: {self.list_path}:3: invalid package token {token!r}"],
                )

    def test_all_invalid_tokens_reported(self) -> None:
        self.list_path.write_text("ok BAD\n# ignored\npkg:s390x $PKG", encoding="utf-8")
        self.assertEqual(
            self.check(f"run: |\n  {self.call}"),
            [
                f"fixture.yml:2: {self.list_path}:1: invalid package token 'BAD'",
                f"fixture.yml:2: {self.list_path}:3: invalid package token '$PKG'",
            ],
        )

    def test_unsupported_context_reports_source_line(self) -> None:
        for lineno, text in (
            (1, f"- run: {self.call} --no-install-recommends"),
            (2, f"steps:\n  run: {self.call} --no-install-recommends\n    --install-recommends"),
            (2, f"run: >\n  {self.call} --no-install-recommends\n  --install-recommends"),
            (4, f"- run: |\n    {self.configure}\n  name: next\n    {self.refresh}"),
            (2, f"run: |\n  \t{self.configure}"),
            (3, f"run: |\n  true\n    {self.configure}"),
            (1, f'"{self.configure}": true'),
            (1, f"# {self.refresh}"),
            (3, f"run: &script\n  |\n    {self.configure}"),
            (2, f"name: &script |\n  {self.configure}\nrun: *script"),
            (3, f"run: |\n  echo\u2028  {self.configure}"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [f"fixture.yml:{lineno}: {UNSUPPORTED_FORM}"])

    def test_source_mismatch_rejects_rest_of_literal(self) -> None:
        text = f"run: &script\n  |\n    true\n    {self.configure}\n    {self.configure}\n"
        self.assertEqual(
            self.check(text),
            [f"fixture.yml:4: {UNSUPPORTED_FORM}", f"fixture.yml:5: {UNSUPPORTED_FORM}"],
        )

    def test_literal_header_inside_another_scalar(self) -> None:
        flagged = self.call + " --no-install-recommends"
        for text in (
            f'run: "echo\n          run: |\n            {flagged}\n          --install-recommends"',
            f"run: >\n          run: |\n            {flagged}\n          --install-recommends",
            f"name: |\n  run: |\n    {flagged}",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [f"fixture.yml:3: {UNSUPPORTED_FORM}"])

    def test_only_literal_style_accepted(self) -> None:
        self.assertEqual(
            self.check(f"run: >-\n  {self.configure}"), [f"fixture.yml:2: {UNSUPPORTED_FORM}"]
        )

    def test_unparseable_yaml(self) -> None:
        for text, error in (
            (f"- run: |\n  {self.configure}", "ScannerError"),
            (f"run: |\n  {self.call}\vignored", "ReaderError"),
        ):
            with self.subTest(error=error):
                self.assertEqual(self.check(text), [f"fixture.yml: unparseable YAML: {error}"])

    def test_deeply_nested_yaml_reports_recursion_error(self) -> None:
        self.assertEqual(
            self.check("[" * 3000 + "]" * 3000), ["fixture.yml: unparseable YAML: RecursionError"]
        )

    def test_yaml_node_cap(self) -> None:
        # The sequence root is one node in addition to its scalar children.
        exact = "- a\n" * (MAX_YAML_NODES - 1)
        self.assertEqual(self.check(exact), [])
        self.assertEqual(
            self.check(exact + "- a\n"), [f"fixture.yml: exceeds {MAX_YAML_NODES} YAML nodes"]
        )

    def test_adjacent_literal_headers(self) -> None:
        self.assertEqual(
            self.check(f"- run: |\n    {self.refresh}\n- run: |-\n    {self.configure}"), []
        )

    def test_tracked_workflows_and_actions(self) -> None:
        self.assertEqual(check_yaml_files(REPO_ROOT), [])

    def test_yaml_discovery_checks_untracked_files(self) -> None:
        paths = [
            self.root / ".github" / rel
            for rel in (
                "actions/a/action.yaml",
                "actions/a/nested/action.yml",
                "workflows/x.yaml",
            )
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"run: |\n  {self.call} --no-install-recommends", encoding="utf-8")
        self.assertEqual(check_yaml_files(self.root), [])
        for path in paths:
            path.write_text(f"run: |\n  {self.call}", encoding="utf-8")
        self.assertEqual(
            check_yaml_files(self.root),
            [
                f"{p}:2: {self.list_rel} names libcudart12 without --no-install-recommends"
                for p in paths
            ],
        )

    def test_yaml_file_cap(self) -> None:
        workflows = self.root / ".github/workflows"
        workflows.mkdir()
        for index in range(MAX_YAML_FILES):
            (workflows / f"{index}.yml").touch()
        self.assertEqual(check_yaml_files(self.root), [])
        action = self.root / ".github/actions/extra/action.yaml"
        action.parent.mkdir(parents=True)
        action.touch()
        self.assertEqual(check_yaml_files(self.root), [f"exceeds {MAX_YAML_FILES} YAML files"])

    def test_unsupported_forms_report_source_line(self) -> None:
        for line in (
            f"{self.call} # comment",
            f'{self.install} "{self.list_rel}"',
            f'run: "{self.call}"',
            self.call + " \\" + "\n  --no-install-recommends",
            f"bash -c '{self.call}'",
            f"{self.call} && true",
            f"{self.call}; true",
            f"{self.call} | cat",
            f"{self.call} &",
            f"{self.call} `true`",
            f"{self.call} --no-install-recommends --no-install-recommends",
            f"{self.install} $VAR",
            f"{self.install} .github/apt/Cov.txt",
            f"{self.install} .github/apt/../cov.txt",
            f"# {self.call}",
            "# apt-install-cached",
            f"{self.configure} # comment",
        ):
            with self.subTest(line=line):
                self.assertEqual(
                    self.check(f"run: |\n  {line}\n"), [f"fixture.yml:2: {UNSUPPORTED_FORM}"]
                )

    def test_invalid_utf8_in_workflow(self) -> None:
        path = self.root / ".github/workflows/bad.yml"
        path.parent.mkdir()
        path.write_bytes(b"steps:\n  - run: echo \xff\n")
        expected = [f"{path}: invalid UTF-8 at byte 21"]
        self.assertEqual(check_workflow(path, self.root), expected)
        self.assertEqual(check_yaml_files(self.root), expected)

    def test_invalid_utf8_in_list_reports_install_line(self) -> None:
        self.list_path.write_bytes(b"ninja-build\n\xff\n")
        self.assertEqual(
            self.check(f"run: |\n  {self.call} --no-install-recommends\n"),
            [f"fixture.yml:2: {self.list_path}: invalid UTF-8 at byte 12"],
        )

    def test_workflow_byte_cap(self) -> None:
        path = self.root / "workflow.yml"
        prefix = f"run: |\n  {self.call}\n#é".encode()
        exact = prefix.ljust(MAX_WORKFLOW_BYTES, b" ")
        path.write_bytes(exact)
        self.assertEqual(
            check_workflow(path, self.root),
            [f"{path}:2: {self.list_rel} names libcudart12 without --no-install-recommends"],
        )
        path.write_bytes(exact + b" ")
        self.assertEqual(
            check_workflow(path, self.root), [f"{path}: exceeds {MAX_WORKFLOW_BYTES} bytes"]
        )
        self.assertEqual(
            self.check(exact.decode() + " "), [f"fixture.yml: exceeds {MAX_WORKFLOW_BYTES} bytes"]
        )

    def test_list_byte_cap(self) -> None:
        exact = b"libcudart12\n#".ljust(MAX_LIST_BYTES, b" ")
        self.list_path.write_bytes(exact)
        self.assertEqual(
            self.check(f"run: |\n  {self.call}"),
            [f"fixture.yml:2: {self.list_rel} names libcudart12 without --no-install-recommends"],
        )
        self.list_path.write_bytes(exact + b" ")
        self.assertEqual(
            self.check(f"run: |\n  {self.call} --no-install-recommends"),
            [f"fixture.yml:2: {self.list_path}: exceeds {MAX_LIST_BYTES} bytes"],
        )

    def test_install_cap_stops_at_first_excess_line(self) -> None:
        exact = ("  " + self.call + " --no-install-recommends\n") * MAX_INSTALL_CALLS
        self.assertEqual(self.check("run: |\n" + exact), [])
        prefix = f"run: |\n  {self.configure}\n  # apt-install-cached\n  {self.call}\n"
        self.assertEqual(
            self.check(prefix + exact + "# apt-install-cached"),
            [
                f"fixture.yml:3: {UNSUPPORTED_FORM}",
                f"fixture.yml:4: {self.list_rel} names libcudart12 without --no-install-recommends",
                f"fixture.yml:{MAX_INSTALL_CALLS + 4}: exceeds {MAX_INSTALL_CALLS} install calls",
            ],
        )

    def test_missing_list(self) -> None:
        missing = ".github/apt/missing.txt"
        self.assertEqual(
            self.check(f"run: |\n  {self.install} {missing} --no-install-recommends"),
            [f"fixture.yml:2: missing apt list {missing}"],
        )

    def test_script_name_built_from_variable_goes_unseen(self) -> None:
        # Documented gap: no physical line spells the script name.
        self.assertEqual(
            self.check(
                'run: |\n  name=apt-install\n  bash scripts/ci/"${name}-cached.sh" install '
                + self.list_rel
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
