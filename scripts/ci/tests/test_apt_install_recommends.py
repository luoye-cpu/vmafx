#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check bash scripts/ci/apt-install-cached.sh in step-level literal run blocks.
Allow configure, refresh, or install .github/apt/<list>.txt [--no-install-recommends].
Driver packages and dotted package names (possible apt regexes) require the flag.
Separate tokens with ASCII space or tab. Only column-zero # starts a list comment.
Tokens are [a-z0-9][a-z0-9+.-]* with optional :[a-z0-9]+, then optional
=[A-Za-z0-9.+~:-]+ or /[a-z0-9.-]+. Caps bound YAML files, nodes, bytes, .github
entries and install calls per file, and bytes per list. More-indented and
backslash-continued forms fail closed.
Invalid UTF-8 in a workflow or package list is reported at its byte offset.
A call that never writes apt-install-cached on one line goes unseen. A form inside a
Bash quoted string or heredoc is accepted with the arguments it shows.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from collections.abc import Iterator
from collections.abc import Set as AbstractSet
from io import StringIO
from itertools import chain
from pathlib import Path
from types import SimpleNamespace

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
# Measured 159 entries under .github; allow six times that total before stopping.
MAX_GITHUB_ENTRIES = 1_024
WORKFLOW_DIRECTORY_DEPTH = 2
ACTION_DIRECTORY_MIN_DEPTH = 3

STEP_PREFIX = "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - "
STEP_LINE_SHIFT = 4
SOURCE_NAME = "fixture.yml"


def _as_step_fixture(text: str) -> str | None:
    """Wrap a root run block in a job step, preserving its line numbering offset."""
    lines = text.split("\n")
    first = lines[0]
    indent = len(first) - len(first.lstrip(" "))
    command = first.lstrip(" ")
    if command.startswith("- run:"):
        command = command[2:]
    elif not command.startswith("run:"):
        return None
    rest = []
    for raw_line in lines[1:]:
        trimmed = raw_line[indent:] if raw_line.startswith(" " * indent) else raw_line
        rest.append("        " + trimmed if trimmed else trimmed)
    return STEP_PREFIX + command + "\n" + "\n".join(rest)


def _remap_finding(finding: str, wrapped: bool, source_name: str) -> str:
    if not wrapped:
        return finding
    match = re.match(rf"^{re.escape(source_name)}:(\d+):(.*)$", finding)
    if match is None:
        return finding
    return f"{source_name}:{max(1, int(match.group(1)) - STEP_LINE_SHIFT)}:{match.group(2)}"


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


def _direct_value(mapping: yaml.MappingNode, name: str) -> yaml.Node | None:
    """Return the value of a key written directly in this mapping, not through an alias."""
    for key, value in mapping.value:
        if (
            isinstance(key, yaml.ScalarNode)
            and key.value == name
            and key.start_mark.index >= mapping.start_mark.index
        ):
            return value
    return None


def _count_nodes(root: yaml.Node | None) -> int | None:
    """Count unique composed nodes, returning None when the cap is exceeded."""
    stack = [root] if root is not None else []
    seen: set[yaml.Node] = set()
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
    return len(seen)


def _literal_runs(root: yaml.Node | None) -> set[yaml.ScalarNode] | None:
    """Collect literal run values under jobs.<id>.steps[*].run or runs.steps[*].run."""
    count = _count_nodes(root)
    if count is None:
        return None
    if not isinstance(root, yaml.MappingNode):
        return set()
    step_lists: list[yaml.SequenceNode] = []
    jobs = _direct_value(root, "jobs")
    if isinstance(jobs, yaml.MappingNode):
        for _, job in jobs.value:
            if not isinstance(job, yaml.MappingNode):
                continue
            steps = _direct_value(job, "steps")
            if isinstance(steps, yaml.SequenceNode):
                step_lists.append(steps)
    runs_block = _direct_value(root, "runs")
    if isinstance(runs_block, yaml.MappingNode):
        steps = _direct_value(runs_block, "steps")
        if isinstance(steps, yaml.SequenceNode):
            step_lists.append(steps)
    runs: set[yaml.ScalarNode] = set()
    for steps in step_lists:
        for step in steps.value:
            if not isinstance(step, yaml.MappingNode):
                continue
            run = _direct_value(step, "run")
            if isinstance(run, yaml.ScalarNode) and run.style == "|":
                runs.add(run)
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


def _is_yaml_candidate(relative: Path) -> bool:
    parts = relative.parts
    name = relative.name
    if len(parts) == WORKFLOW_DIRECTORY_DEPTH and parts[0] == "workflows":
        return relative.suffix in {".yml", ".yaml"}
    return (
        len(parts) >= ACTION_DIRECTORY_MIN_DEPTH
        and parts[0] == "actions"
        and name in {"action.yml", "action.yaml"}
    )


def _github_yaml_files(
    github: Path,
    scandir: object = os.scandir,
) -> set[Path] | None:
    """Walk .github bounded by entry count, returning every workflow or action file."""
    files: set[Path] = set()
    directories = [github]
    entries = 0
    while directories:
        directory = directories.pop()
        try:
            scan = scandir(directory)  # type: ignore[operator]
        except (FileNotFoundError, NotADirectoryError):
            continue
        try:
            for entry in scan:
                entries += 1
                if entries > MAX_GITHUB_ENTRIES:
                    return None
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                elif entry.is_file(follow_symlinks=False) and _is_yaml_candidate(
                    path.relative_to(github)
                ):
                    files.add(path)
        finally:
            scan.close()
    return files


def check_yaml_files(repo_root: Path) -> list[str]:
    """Glob local files too: an untracked workflow or action must also fail closed."""
    github = repo_root / ".github"
    files = _github_yaml_files(github)
    if files is None:
        return [f"exceeds {MAX_GITHUB_ENTRIES} .github entries"]
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
        wrapped = _as_step_fixture(text)
        if wrapped is None:
            return check_workflow_text(text, self.root, SOURCE_NAME)
        findings = check_workflow_text(wrapped, self.root, SOURCE_NAME)
        return [_remap_finding(finding, True, SOURCE_NAME) for finding in findings]

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
            (2, f"run: |\n  \t{self.configure}"),
            (3, f"run: |\n  true\n    {self.configure}"),
            (1, f'"{self.configure}": true'),
            (1, f"# {self.refresh}"),
            (3, f"run: &script\n  |\n    {self.configure}"),
            (2, f"name: &script |\n  {self.configure}\nrun: *script"),
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
        self.assertEqual(
            check_workflow_text(f"- run: |\n  {self.configure}", self.root, SOURCE_NAME),
            [f"{SOURCE_NAME}: unparseable YAML: ScannerError"],
        )
        for text, error in ((f"run: |\n  {self.call}\vignored", "ReaderError"),):
            with self.subTest(error=error):
                self.assertEqual(self.check(text), [f"{SOURCE_NAME}: unparseable YAML: {error}"])

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
            self.check(
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                f"      - run: |\n          {self.refresh}\n"
                f"      - run: |-\n          {self.configure}"
            ),
            [],
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
            path.write_text(
                f"jobs:\n  test:\n    steps:\n      - run: |\n          {self.call} "
                "--no-install-recommends",
                encoding="utf-8",
            )
        self.assertEqual(check_yaml_files(self.root), [])
        for path in paths:
            path.write_text(
                f"jobs:\n  test:\n    steps:\n      - run: |\n          {self.call}",
                encoding="utf-8",
            )
        self.assertEqual(
            check_yaml_files(self.root),
            [
                f"{p}:5: {self.list_rel} names libcudart12 without --no-install-recommends"
                for p in paths
            ],
        )

    def test_only_step_level_run_keys_are_commands(self) -> None:
        cases = (
            (
                "run_key: &k run\n"
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - name: x\n"
                "        *k: |\n"
                f"          {self.refresh}",
                7,
            ),
            (
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - <<: &merged\n"
                "          run: |\n"
                f"            {self.refresh}",
                6,
            ),
            (
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - env:\n"
                "          run: |\n"
                f"            {self.refresh}",
                6,
            ),
        )
        for text, lineno in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    check_workflow_text(text, self.root, SOURCE_NAME),
                    [f"{SOURCE_NAME}:{lineno}: {UNSUPPORTED_FORM}"],
                )

    def test_composite_action_runs_steps_are_commands(self) -> None:
        text = (
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - shell: bash\n"
            "      run: |\n"
            f"        {self.refresh}\n"
        )
        self.assertEqual(check_workflow_text(text, self.root, SOURCE_NAME), [])

    def test_github_entry_cap_stops_walk(self) -> None:
        github = self.root / ".github"
        shutil.rmtree(github)
        github.mkdir()
        for index in range(MAX_GITHUB_ENTRIES):
            (github / f"{index}").mkdir()
        self.assertEqual(check_yaml_files(self.root), [])
        (github / "extra").mkdir()
        self.assertEqual(
            check_yaml_files(self.root), [f"exceeds {MAX_GITHUB_ENTRIES} .github entries"]
        )

    def test_github_entry_cap_limits_pulls(self) -> None:
        pulls = 0
        root = self.root

        class FakeScan:
            def __iter__(self) -> FakeScan:
                return self

            def __next__(self) -> object:
                nonlocal pulls
                if pulls >= MAX_GITHUB_ENTRIES + 100:
                    raise StopIteration
                pulls += 1
                return SimpleNamespace(
                    path=str(root / ".github" / f"{pulls}.tmp"),
                    is_dir=lambda **_: False,
                    is_file=lambda **_: False,
                )

            def close(self) -> None:
                pass

        self.assertIsNone(_github_yaml_files(root / ".github", scandir=lambda _: FakeScan()))
        self.assertEqual(pulls, MAX_GITHUB_ENTRIES + 1)

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
        prefix = f"jobs:\n  test:\n    steps:\n      - run: |\n          {self.call}\n#é".encode()
        exact = prefix.ljust(MAX_WORKFLOW_BYTES, b" ")
        path.write_bytes(exact)
        self.assertEqual(
            check_workflow(path, self.root),
            [f"{path}:5: {self.list_rel} names libcudart12 without --no-install-recommends"],
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
