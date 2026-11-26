#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Require positive file-selection fixtures for Renovate custom managers.

The repository uses anchored, flag-free regexes with the /regex/ delimiter
contract verified against Renovate 44.56.3's installed matcher. These patterns
use syntax shared by Python re and RE2; no Renovate installation is needed for
the local/CI guard. See docs/research/renovate-file-pattern-delimiters.md.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.lib.renovate_regex import to_python
from scripts.lib.safe_subprocess import run as run_command

ROOT = Path(__file__).resolve().parents[3]
GIT = shutil.which("git") or "/usr/bin/git"
CONFIG = "build-config.env"
CONFIG_LINE = re.compile(r'^([A-Z][A-Z0-9_]*)="([^"]*)"', re.MULTILINE)
DOCKERFILE = re.compile(r"^(?:Dockerfile[^/]*|docker/Dockerfile[^/]*|dev/Containerfile[^/]*)$")
CUDA_COMPAT = "docker/dev/ubuntu-26.04-cuda.Dockerfile"
# The controller store tests' PostgreSQL images (#2656): Go string constants
# of the form postgres:<tag>@sha256:<digest>, read by a custom manager.
STORETEST = "cmd/vmafx-controller/internal/storetest/storetest.go"
IMAGE_CONSTANT = re.compile(
    r'^const (\w+) = "postgres:([^"@]+)@(sha256:[a-f0-9]{64})"$', re.MULTILINE
)


def image_keys(config_text: str) -> set[str]:
    """The build-config.env keys whose value is an image reference, by the shape
    scripts/ci/check-base-image-single-source.sh uses: a registry path or a tag
    separator, and not a URL."""
    return {
        key
        for key, value in CONFIG_LINE.findall(config_text)
        if not value.startswith("http") and ("/" in value or ":" in value)
    }


def base_files(files: list[str], read: Callable[[str], str]) -> set[str]:
    """build-config.env and every Dockerfile in the single-source gate's scope that
    mirrors one of its image keys as an ARG default. Derived from the tree, so a
    new mirror the custom manager does not select fails the tests below instead
    of being left to the built-in manager (docker/Dockerfile.tester kept ROCm
    10.0.0 in the 10.1.0 bump, #2170)."""
    keys = image_keys(read(CONFIG))
    arg = re.compile(r"^\s*ARG\s+(" + "|".join(sorted(keys)) + r")=", re.MULTILINE)
    scope = [
        path
        for path in files
        if (DOCKERFILE.match(path) and not path.startswith("docker/dev/")) or path == CUDA_COMPAT
    ]
    return {CONFIG} | {path for path in scope if arg.search(read(path))}


class RenovateFilePatterns(unittest.TestCase):
    config: ClassVar[dict[str, Any]]
    files: ClassVar[list[str]]
    base: ClassVar[set[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "renovate.json").read_text(encoding="utf-8"))
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        result = run_command(
            [GIT, "-C", str(ROOT), "ls-files"],
            allowed_executables=(GIT,),
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert isinstance(result.stdout, str)
        cls.files = result.stdout.splitlines()
        cls.base = base_files(cls.files, lambda path: (ROOT / path).read_text(encoding="utf-8"))

    def patterns(self, manager: dict[str, Any]) -> list[re.Pattern[str]]:
        patterns = manager["managerFilePatterns"]
        self.assertTrue(patterns)
        compiled = []
        for pattern in patterns:
            self.assertTrue(pattern.startswith("/") and pattern.endswith("/"), pattern)
            compiled.append(re.compile(pattern[1:-1]))
        return compiled

    def test_every_custom_pattern_selects_a_tracked_input(self) -> None:
        for manager in self.config["customManagers"]:
            name = manager.get("depNameTemplate", manager.get("description", "custom"))
            for pattern in self.patterns(manager):
                with self.subTest(manager=name, pattern=pattern.pattern):
                    selected = [path for path in self.files if pattern.search(path)]
                    self.assertTrue(selected, "Pattern selects no tracked files")
                    for path in selected:
                        self.assertIsNone(pattern.search(f"archive/{path}"))
                        self.assertIsNone(pattern.search(f"{path}.bak"))

    def test_base_image_manager_selects_config_and_all_mirrors(self) -> None:
        managers = [
            manager
            for manager in self.config["customManagers"]
            if manager["datasourceTemplate"] == "docker" and "depNameTemplate" not in manager
        ]
        self.assertEqual(len(managers), 1)
        patterns = self.patterns(managers[0])
        selected = {
            path for path in self.files if any(pattern.search(path) for pattern in patterns)
        }
        self.assertEqual(selected, self.base)

    def test_builtin_docker_exclusions_are_covered_by_the_custom_manager(self) -> None:
        rules = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("matchManagers") == ["dockerfile"] and rule.get("enabled") is False
        ]
        self.assertEqual(len(rules), 1)
        self.assertEqual(set(rules[0]["matchFileNames"]), self.base - {CONFIG})

    def test_base_files_follow_the_config(self) -> None:
        # The derivation must see the known mirrors, and a Dockerfile that stops
        # mirroring a config key must drop out of it.
        self.assertIn("docker/Dockerfile.tester", self.base)
        self.assertIn("dev/Containerfile", self.base)
        fake = {
            CONFIG: 'ROCM_BUILDER="rocm/dev-ubuntu-26.04:1.0-full@sha256:00"\nROCM_VERSION="1.0"\n',
            "docker/Dockerfile.a": 'ARG ROCM_BUILDER="x"\n',
            "docker/Dockerfile.b": 'ARG ROCM_VERSION="1.0"\n',
            "docker/dev/alpine.Dockerfile": 'ARG ROCM_BUILDER="x"\n',
        }
        self.assertEqual(base_files(list(fake), fake.__getitem__), {CONFIG, "docker/Dockerfile.a"})

    def test_rocm_review_rule_matches_the_pinned_image(self) -> None:
        # The manual-review rule (ADR-1225) named rocm/dev-ubuntu-24.04 after the
        # pin moved to the 26.04 image, so the 10.1.0 bump (#2170) arrived without
        # its rocm / manual-review labels.
        config = (ROOT / CONFIG).read_text(encoding="utf-8")
        pinned = {
            value.split(":", 1)[0]
            for key, value in CONFIG_LINE.findall(config)
            if key in {"ROCM_BUILDER", "ROCM_RUNTIME"}
        }
        self.assertEqual(len(pinned), 1)
        rules = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("groupName") == "ROCm (hip runtime)"
        ]
        self.assertEqual(len(rules), 1)
        self.assertLessEqual(pinned, set(rules[0]["matchPackageNames"]))
        self.assertIn("manual-review", rules[0]["labels"])

    def test_glibc_floor_runner_pin_is_frozen(self) -> None:
        # ADR-1354: Renovate must not move the native-bundle verify job off the
        # oldest image that can load it, and the rule must still point at the
        # job it protects.
        rules = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("matchDatasources") == ["github-runners"] and rule.get("enabled") is False
        ]
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule["matchDepNames"], ["ubuntu"])
        self.assertEqual(rule["matchCurrentValue"], "24.04")
        self.assertEqual(rule["matchFileNames"], [".github/workflows/supply-chain.yml"])
        workflow = (ROOT / rule["matchFileNames"][0]).read_text(encoding="utf-8")
        match = re.search(r"(?ms)^  verify-native-artifacts:\n(.*?)(?=^  [\w-]+:\n|\Z)", workflow)
        self.assertIsNotNone(match, "supply-chain.yml has no verify-native-artifacts job")
        self.assertRegex(match.group(1) if match else "", r"(?m)^    runs-on: ubuntu-24\.04$")

    def storetest_manager(self) -> dict[str, Any]:
        managers = [
            manager
            for manager in self.config["customManagers"]
            if any(pattern.search(STORETEST) for pattern in self.patterns(manager))
        ]
        self.assertEqual(len(managers), 1)
        manager: dict[str, Any] = managers[0]
        return manager

    def test_storetest_postgres_images_are_tracked(self) -> None:
        # No built-in manager reads a Go string constant, so without the custom
        # manager neither image digest is ever updated. Every image constant of
        # the file must be a match, with its tag and digest.
        manager = self.storetest_manager()
        self.assertEqual(manager["datasourceTemplate"], "docker")
        self.assertEqual(manager["depNameTemplate"], "postgres")
        text = (ROOT / STORETEST).read_text(encoding="utf-8")
        constants = {name: (tag, digest) for name, tag, digest in IMAGE_CONSTANT.findall(text)}
        self.assertEqual(set(constants), {"Image", "OldestImage"})
        patterns = [re.compile(to_python(match)) for match in manager["matchStrings"]]
        found = {
            (match["currentValue"], match["currentDigest"])
            for pattern in patterns
            for match in pattern.finditer(text)
        }
        self.assertEqual(found, set(constants.values()))
        # An image without a digest is not a match (testutil/pg refuses it).
        undigested = 'const Image = "postgres:18.6-alpine"\n'
        self.assertFalse(any(pattern.search(undigested) for pattern in patterns))

    def test_oldest_postgres_image_stays_on_its_major(self) -> None:
        # OldestImage is the oldest release external servers may run: digest,
        # minor and patch updates only. Image keeps its major updates.
        rules = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("matchFileNames") == [STORETEST]
        ]
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule["matchDepNames"], ["postgres"])
        text = (ROOT / STORETEST).read_text(encoding="utf-8")
        tags = {name: tag for name, tag, _ in IMAGE_CONSTANT.findall(text)}
        current = re.compile(to_python(rule["matchCurrentValue"][1:-1]))
        allowed = re.compile(to_python(rule["allowedVersions"][1:-1]))
        self.assertTrue(current.search(tags["OldestImage"]))
        self.assertFalse(current.search(tags["Image"]))
        major = int(tags["OldestImage"].split(".", 1)[0])
        self.assertTrue(allowed.search(f"{major}.99-alpine"))
        self.assertFalse(allowed.search(f"{major + 1}.0-alpine"))

    def test_helm_subchart_archives_option_set_when_charts_tracked(self) -> None:
        # Renovate #3045 / #3047: git tracks deploy/helm/.../charts/*.tgz, so
        # renovate.json must include helmUpdateSubChartArchives in postUpdateOptions.
        tracked_archives = [
            path for path in self.files if re.search(r"(?:^|/)charts/[^/]+\.tgz$", path)
        ]
        self.assertTrue(tracked_archives, "Expected tracked Helm chart archives in repository")
        self.assertEqual(helm_subchart_findings(self.config, self.files), [])

    def test_helm_subchart_archives_option_missing_fails(self) -> None:
        fake_config = json.loads(json.dumps(self.config))
        if "postUpdateOptions" in fake_config:
            fake_config["postUpdateOptions"] = [
                opt
                for opt in fake_config["postUpdateOptions"]
                if opt != "helmUpdateSubChartArchives"
            ]
        errors = helm_subchart_findings(fake_config, self.files)
        self.assertTrue(any("missing from postUpdateOptions" in e for e in errors))


def _matches_regex_or_glob_list(value: str, patterns: list[str]) -> bool:
    """Mirror matchRegexOrGlobList (Renovate main 208cd854196d,
    lib/util/string-match.ts:13-21, 29-57, 72-77, 94-104): an empty list is
    false; positive/negative split requires at least one positive match when
    positives exist and every !-negated pattern to hold; /re/ and /re/i (plus
    !/re/ and !/re/i) are regexes, and other patterns (including !glob) are
    case-insensitive globs."""
    if not patterns:
        return False
    has_positive = False
    positive_matched = False
    for raw in patterns:
        negated = raw.startswith("!")
        body = raw[1:] if negated else raw
        regex = re.fullmatch(r"/(.+)/(i?)", body, re.DOTALL)
        if regex is not None:
            flags = re.IGNORECASE if regex[2] == "i" else 0
            hit = re.search(to_python(regex[1]), value, flags) is not None
        else:
            # fnmatch stands in for minimatch(..., {nocase: true, dot: true}):
            # its * and ? match /, and brace or extglob syntax is not supported.
            hit = fnmatch.fnmatchcase(value.lower(), body.lower())
        if negated:
            if hit:
                return False
        else:
            has_positive = True
            positive_matched = positive_matched or hit
    return not has_positive or positive_matched


# Renovate main 208cd854196d, lib/util/package-rules/matchers.ts:29-48 defines
# 19 packageRule matchers; only these 8 delegate to matchRegexOrGlobList.
_REGEX_OR_GLOB_LIST_MATCHERS: frozenset[str] = frozenset(
    {
        "matchManagers",
        "matchFileNames",
        "matchDatasources",
        "matchPackageNames",
        "matchDepNames",
        "matchDepTypes",
        "matchCurrentValue",
        "matchUpdateTypes",
    }
)
_RENOVATE_REGEX = re.compile(r"!?/(.+)/(i?)", re.DOTALL)
_PLAIN_RELEASE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
MAX_RELEASE_PARTS = 16


class UnsupportedPackageRuleError(ValueError):
    """Raised when a matching packageRule carries an unmodeled matcher or value
    (Renovate main 208cd854196d, lib/util/package-rules/matchers.ts:29-48,
    lib/util/package-rules/current-version.ts:24-79)."""

    def __init__(self, rule_index: int, key: str, value: Any) -> None:
        self.rule_index = rule_index
        self.key = key
        self.value = value
        super().__init__(f"packageRules[{rule_index}]: matcher {key}={value!r} is not modeled")


def _plain_release_numbers(text: str) -> tuple[int, ...] | None:
    """Parse an exact PEP 440 release (`^[0-9]+(\\.[0-9]+)*$`) and drop trailing
    zeros for exact-version equality in CurrentVersionMatcher (Renovate main
    208cd854196d, lib/util/package-rules/current-version.ts:24-79)."""
    if _PLAIN_RELEASE.fullmatch(text) is None:
        return None
    raw_parts = text.split(".")
    if len(raw_parts) > MAX_RELEASE_PARTS:
        return None
    parts = [int(part) for part in raw_parts[:MAX_RELEASE_PARTS]]
    end = 1
    for index, number in enumerate(parts[:MAX_RELEASE_PARTS]):
        if number != 0:
            end = index + 1
    return tuple(parts[:end])


def _matches_current_version(version: str | None, pattern: Any) -> bool | None:
    """Mirror Renovate CurrentVersionMatcher (Renovate main 208cd854196d,
    lib/util/package-rules/current-version.ts:24-79): /re/, /re/i, !/re/, and
    !/re/i reuse regex matching from lib/util/string-match.ts:29-57, 94-104;
    plain PEP 440 releases (`^[0-9]+(\\.[0-9]+)*$`) compare release numbers
    after dropping trailing zeros; ranges, globs, and non-strings return None."""
    if version is None or not isinstance(pattern, str):
        return None
    if _RENOVATE_REGEX.fullmatch(pattern) is not None:
        return _matches_regex_or_glob_list(version, [pattern])
    current = _plain_release_numbers(version)
    wanted = _plain_release_numbers(pattern)
    if current is not None and wanted is not None:
        return current == wanted
    return None


def _eval_modeled_matcher(key: str, raw: Any, dependency: dict[str, str]) -> bool | None:
    """Evaluate one match* key: dispatch the 8 allow-listed keys to
    matchRegexOrGlobList (Renovate main 208cd854196d,
    lib/util/package-rules/matchers.ts:29-48), matchCurrentVersion to
    CurrentVersionMatcher (current-version.ts:24-79), and return None for any
    other matcher or non-string pattern list."""
    if key == "matchCurrentVersion":
        return _matches_current_version(dependency.get(key), raw)
    if key not in _REGEX_OR_GLOB_LIST_MATCHERS:
        return None
    choices = raw if isinstance(raw, list) else [raw]
    if not all(isinstance(item, str) for item in choices):
        return None
    return _matches_regex_or_glob_list(dependency.get(key, ""), choices)


def _rule_matches(index: int, rule: dict[str, Any], dependency: dict[str, str]) -> bool:
    """Evaluate a packageRule's matchers (Renovate main 208cd854196d,
    lib/util/package-rules/index.ts:64-137, matchers.ts:29-48): check every
    modeled predicate first so a rule excluded by one is skipped, then raise
    UnsupportedPackageRuleError if an unmodeled key or value remains."""
    unmodeled: tuple[str, Any] | None = None
    for key, raw in rule.items():
        if not key.startswith("match"):
            continue
        matched = _eval_modeled_matcher(key, raw, dependency)
        if matched is False:
            return False
        if matched is None and unmodeled is None:
            unmodeled = (key, raw)
    if unmodeled is not None:
        raise UnsupportedPackageRuleError(index, unmodeled[0], unmodeled[1])
    return True


def matching_package_rules(config: dict[str, Any], dependency: dict[str, str]) -> list[int]:
    """Find matching rules for these concrete fixtures in their configured order
    (Renovate main 208cd854196d, lib/util/package-rules/index.ts:64-137)."""
    return [
        index
        for index, rule in enumerate(config["packageRules"])
        if _rule_matches(index, rule, dependency)
    ]


def matching_group_rules(config: dict[str, Any], dependency: dict[str, str]) -> list[int]:
    return [
        i
        for i in matching_package_rules(config, dependency)
        if "groupName" in config["packageRules"][i]
    ]


class CoupledPinGroups(unittest.TestCase):
    config: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "renovate.json").read_text(encoding="utf-8"))

    def group(self, dep: dict[str, str]) -> str:
        indices = matching_group_rules(self.config, dep)
        return str(self.config["packageRules"][indices[-1]]["groupName"]) if indices else ""

    def test_clang_group_and_order(self) -> None:
        for manager, package in (
            ("pre-commit", "pre-commit/mirrors-clang-format"),
            ("pip_requirements", "clang-format"),
        ):
            with self.subTest(manager=manager):
                dep = {
                    "matchManagers": manager,
                    "matchPackageNames": package,
                    "matchUpdateTypes": "patch",
                }
                indices = matching_group_rules(self.config, dep)
                self.assertGreaterEqual(len(indices), 2)
                self.assertEqual(self.group(dep), "clang-format (all managers)")
                self.assertGreater(indices[-1], max(indices[:-1]))

    def test_other_dependencies_keep_their_groups(self) -> None:
        for manager, package, group in (
            ("pip_requirements", "requests", "Python (patch)"),
            ("pre-commit", "pre-commit/pre-commit-hooks", "pre-commit hooks"),
        ):
            with self.subTest(manager=manager):
                self.assertEqual(
                    self.group(
                        {
                            "matchManagers": manager,
                            "matchPackageNames": package,
                            "matchUpdateTypes": "patch",
                        }
                    ),
                    group,
                )

    def test_rust_custom_group_and_actions_exclusion(self) -> None:
        dep = {
            "matchManagers": "custom.regex",
            "matchDepNames": "rust",
            "matchPackageNames": "rust",
            "matchUpdateTypes": "minor",
        }
        self.assertEqual(self.group(dep), "Rust toolchain")
        rules = self.config["packageRules"]
        self.assertTrue(
            any(
                r.get("matchManagers") == ["github-actions"]
                and r.get("matchDepNames") == ["rust"]
                and r.get("matchDepTypes") == ["uses-with"]
                and r.get("enabled") is False
                for r in rules
            )
        )

    def test_scorecard_action_travels_alone_and_reviewed(self) -> None:
        """Positive: minor, patch and digest updates of ossf/scorecard-action land in
        their own non-automerged group. Negative: other GitHub Actions such as
        actions/checkout stay in the minor + patch group. Boundary: major updates of
        ossf/scorecard-action also land in the scorecard group after the major rule."""
        scorecard_group = "OpenSSF Scorecard action (gate identity)"
        for update_type in ("minor", "patch", "digest", "major"):
            with self.subTest(update_type=update_type):
                dep = {
                    "matchManagers": "github-actions",
                    "matchPackageNames": "ossf/scorecard-action",
                    "matchDepNames": "ossf/scorecard-action",
                    "matchUpdateTypes": update_type,
                }
                indices = matching_group_rules(self.config, dep)
                self.assertGreaterEqual(len(indices), 2)
                self.assertEqual(self.group(dep), scorecard_group)
                winning_rule = self.config["packageRules"][indices[-1]]
                self.assertIs(winning_rule.get("automerge"), False)
        checkout = {
            "matchManagers": "github-actions",
            "matchPackageNames": "actions/checkout",
            "matchDepNames": "actions/checkout",
            "matchUpdateTypes": "patch",
        }
        self.assertEqual(self.group(checkout), "GitHub Actions (minor + patch)")


def helm_subchart_findings(config: dict[str, Any], files: list[str]) -> list[str]:
    """Require postUpdateOptions: ['helmUpdateSubChartArchives'] in renovate.json
    when packaged Helm subchart archives (*.tgz under charts/) are git-tracked.
    Without this option, Renovate updates Chart.lock but leaves the old archive
    in place, causing helm lint to fail on absent archives (#3045, #3047)."""
    tracked_archives = [path for path in files if re.search(r"(?:^|/)charts/[^/]+\.tgz$", path)]
    if not tracked_archives:
        return []
    options = config.get("postUpdateOptions", [])
    if "helmUpdateSubChartArchives" not in options:
        return [
            f"tracked Helm chart archives present ({tracked_archives}) but "
            "'helmUpdateSubChartArchives' missing from postUpdateOptions"
        ]
    return []


# The CI Python interpreter (ADR-3312): one custom manager owns
# build-config.env's PYTHON_CI_VERSION and every workflow `python-version:` pin,
# read with the pattern scripts/ci/check-workflow-versions.py uses.
WORKFLOW_PYTHON = re.compile(r'python-version:\s*[\'"]?([0-9]+\.[0-9]+\.[0-9]+)[\'"]?')
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")


def python_ci_manager(config: dict[str, Any]) -> dict[str, Any] | None:
    """The custom manager that owns the CI Python interpreter, or None."""
    managers = [
        manager
        for manager in config.get("customManagers", [])
        if manager.get("depNameTemplate") == "python"
        and manager.get("packageNameTemplate") == "actions/python-versions"
    ]
    return managers[0] if len(managers) == 1 else None


def python_ci_findings(
    config: dict[str, Any], files: list[str], read: Callable[[str], str]
) -> list[str]:
    """Every way the config fails to own the CI Python interpreter as one group."""
    manager = python_ci_manager(config)
    if manager is None:
        return ["no single custom manager for python / actions/python-versions"]
    selectors = [re.compile(p[1:-1]) for p in manager.get("managerFilePatterns", [])]
    patterns = [re.compile(to_python(m)) for m in manager.get("matchStrings", [])]
    errors: list[str] = []
    for path in [CONFIG, *(f for f in files if WORKFLOW.match(f))]:
        text = read(path)
        if path == CONFIG:
            want = re.findall(r'^PYTHON_CI_VERSION="([^"]*)"', text, re.MULTILINE)
        else:
            want = WORKFLOW_PYTHON.findall(text)
        if not want:
            continue
        if not any(s.search(path) for s in selectors):
            errors.append(f"{path} is not selected by the manager")
            continue
        got = [m["currentValue"] for p in patterns for m in p.finditer(text)]
        if sorted(got) != sorted(want):
            errors.append(f"{path}: manager reads {sorted(got)}, the check reads {sorted(want)}")
    rules = config.get("packageRules", [])
    if not any(
        r.get("matchManagers") == ["github-actions"]
        and r.get("matchDepNames") == ["python"]
        and r.get("enabled") is False
        for r in rules
    ):
        errors.append("the github-actions manager still updates python-version")
    if not any(
        r.get("matchManagers") == ["custom.regex"]
        and r.get("matchPackageNames") == ["actions/python-versions"]
        and r.get("groupName") == "CI Python interpreter"
        and r.get("allowedVersions") == "<3.15.0"
        for r in rules
    ):
        errors.append("no CI Python interpreter group rule below 3.15.0")
    return errors


class PythonCiVersionGroup(unittest.TestCase):
    """Positive: the checked-in config owns every pin. Negative: a manager that
    misses the workflows, a manager that misses build-config.env, a removed
    disable rule and a removed freeze bound each fail. Boundary: other Python
    keys of build-config.env are not read."""

    config: ClassVar[dict[str, Any]]
    files: ClassVar[list[str]]

    @classmethod
    def setUpClass(cls) -> None:
        RenovateFilePatterns.setUpClass()
        cls.config = RenovateFilePatterns.config
        cls.files = RenovateFilePatterns.files

    def findings(self, change: Callable[[dict[str, Any]], None] | None = None) -> list[str]:
        config = json.loads(json.dumps(self.config))
        if change is not None:
            change(config)
        return python_ci_findings(
            config, self.files, lambda path: (ROOT / path).read_text(encoding="utf-8")
        )

    def test_checked_in_config_owns_every_pin(self) -> None:
        self.assertEqual(self.findings(), [])
        pins = [f for f in self.files if WORKFLOW.match(f)]
        read = (ROOT / CONFIG).read_text(encoding="utf-8")
        self.assertIn("PYTHON_CI_VERSION=", read)
        self.assertTrue(
            any(WORKFLOW_PYTHON.search((ROOT / f).read_text(encoding="utf-8")) for f in pins)
        )

    def test_other_python_keys_are_not_read(self) -> None:
        manager = python_ci_manager(self.config)
        assert manager is not None
        patterns = [re.compile(to_python(m)) for m in manager["matchStrings"]]
        for line in ('PYTHON_VERSION="3.14"\n', 'RELEASE_PYTHON_BASE="python:3.14.8-slim"\n'):
            with self.subTest(line=line):
                self.assertFalse(any(p.search(line) for p in patterns))

    def test_manager_without_workflows_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = python_ci_manager(config)
            assert manager is not None
            manager["managerFilePatterns"] = ["/^build-config\\.env$/"]

        self.assertTrue(any("not selected" in e for e in self.findings(drop)))

    def test_manager_without_config_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = python_ci_manager(config)
            assert manager is not None
            manager["matchStrings"] = manager["matchStrings"][1:]

        self.assertTrue(any(e.startswith(CONFIG) for e in self.findings(drop)))

    def test_github_actions_copy_left_enabled_fails(self) -> None:
        def enable(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("matchManagers") == ["github-actions"] and rule.get(
                    "matchDepNames"
                ) == ["python"]:
                    rule["enabled"] = True

        self.assertIn(
            "the github-actions manager still updates python-version", self.findings(enable)
        )

    def test_group_without_freeze_bound_fails(self) -> None:
        def unbound(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("groupName") == "CI Python interpreter":
                    rule.pop("allowedVersions")

        self.assertIn("no CI Python interpreter group rule below 3.15.0", self.findings(unbound))

    def test_missing_manager_fails(self) -> None:
        def remove(config: dict[str, Any]) -> None:
            config["customManagers"] = [
                m for m in config["customManagers"] if m.get("depNameTemplate") != "python"
            ]

        self.assertEqual(
            self.findings(remove), ["no single custom manager for python / actions/python-versions"]
        )


TOOLING_INPUT = "requirements/locks/tooling-tests.in"
LOCK_MANIFEST = "requirements/locks/manifest.json"
UV_GROUP = "uv (lock generator, all managers)"
UV_SELECTOR_FINDING = "uv manager selector must be the exact anchored manifest path"
MAX_IGNORE_PATHS = 1024


def uv_lock_generator_manager(config: dict[str, Any]) -> dict[str, Any] | None:
    """The custom manager that owns manifest.json's uv_version, or None."""
    managers = [
        manager
        for manager in config.get("customManagers", [])
        if manager.get("depNameTemplate") == "uv"
    ]
    return managers[0] if len(managers) == 1 else None


def _uv_manifest_finding(manager: dict[str, Any], manifest_text: str, version: str) -> str | None:
    if manager.get("datasourceTemplate") != "pypi":
        return "uv manager datasourceTemplate must be pypi"
    selectors = manager.get("managerFilePatterns", [])
    if len(selectors) != 1:
        return "uv manager needs exactly one managerFilePatterns entry"
    if selectors[0] != r"/^requirements/locks/manifest\.json$/":
        return UV_SELECTOR_FINDING
    patterns = [re.compile(to_python(m)) for m in manager.get("matchStrings", [])]
    want = [version]
    got = [
        m.groupdict().get("currentValue", "") for p in patterns for m in p.finditer(manifest_text)
    ]
    if got != want:
        return f"{LOCK_MANIFEST}: manager reads {got}, manifest uv_version is {want}"
    return None


def _manager_enabled_by_list(config: dict[str, Any], manager: str) -> bool:
    """Mirror Renovate getEnabledManagersList (Renovate main 208cd854196d,
    lib/modules/manager/index.ts:146-156): an absent or empty enabledManagers
    enables all managers; a non-empty list enables only the listed ones."""
    enabled_managers = config.get("enabledManagers")
    return not enabled_managers or manager in enabled_managers


def _is_ignored_by_renovate(path: str, ignore_paths: list[str]) -> bool:
    """Mirror Renovate ignorePaths matching (Renovate main 208cd854196d,
    lib/workers/repository/extract/file-match.ts:32-35): substring or glob."""
    if len(ignore_paths) > MAX_IGNORE_PATHS:
        return True
    # fnmatchcase stands in for minimatch(..., {dot: true}); its * also matches /,
    # which errs toward reporting a file ignored (fail closed).
    return any(
        pattern in path or fnmatch.fnmatchcase(path, pattern)
        for pattern in ignore_paths[:MAX_IGNORE_PATHS]
    )


def _fold_uv_pin_rules(
    config: dict[str, Any],
    manager: str,
    path: str,
    current_value: str,
    version: str,
    update_type: str,
) -> tuple[list[int], bool, Any, Any]:
    """Fold matching packageRules in order for one uv pin (Renovate main
    208cd854196d, lib/util/package-rules/index.ts:64-137, mergeChildConfig)."""
    rules = config["packageRules"]
    indices = matching_package_rules(
        config,
        {
            "matchManagers": manager,
            "matchFileNames": path,
            "matchDatasources": "pypi",
            "matchPackageNames": "uv",
            "matchDepNames": "uv",
            "matchUpdateTypes": update_type,
            "matchCurrentValue": current_value,
            "matchCurrentVersion": version,
        },
    )
    enabled = bool(config.get(manager, {}).get("enabled", True))
    group_name = config.get("groupName")
    group_slug = config.get("groupSlug")
    for index in indices:
        rule = rules[index]
        enabled = bool(rule.get("enabled", enabled))
        group_name = rule.get("groupName", group_name)
        group_slug = rule.get("groupSlug", group_slug)
    return indices, enabled and _manager_enabled_by_list(config, manager), group_name, group_slug


def _uv_group_finding(
    config: dict[str, Any], update_type: str, manifest_version: str, pip_version: str
) -> list[str]:
    """Check manager enablement (Renovate main 208cd854196d,
    lib/modules/manager/index.ts:146-156), effective groupName folding
    (lib/util/package-rules/index.ts:64-137, mergeChildConfig), and effective
    groupSlug agreement (lib/workers/repository/updates/branch-name.ts:70-95).
    Stricter than Renovate, which compares slugify(groupSlug or groupName): this
    compares the configured groupSlug values, so it never misses a split branch
    but also reports a slug that only one pin sets, even when it equals the
    slug Renovate derives from groupName."""
    rules = config["packageRules"]
    shared = {index for index, rule in enumerate(rules) if rule.get("groupName") == UV_GROUP}
    groups: dict[str, Any] = {}
    slugs: dict[str, Any] = {}
    disabled: list[str] = []
    for manager, path, current_value, version in (
        ("pip_requirements", TOOLING_INPUT, f"=={pip_version}", pip_version),
        ("custom.regex", LOCK_MANIFEST, manifest_version, manifest_version),
    ):
        indices, is_enabled, group_name, group_slug = _fold_uv_pin_rules(
            config, manager, path, current_value, version, update_type
        )
        if not is_enabled:
            disabled.append(f"{path}: {manager} uv ({update_type}) disabled")
        shared.intersection_update(indices)
        groups[manager] = group_name
        slugs[manager] = group_slug
    if disabled:
        return disabled
    if not shared:
        return [f"uv ({update_type}): missing shared {UV_GROUP!r} rule"]
    for manager, group_name in groups.items():
        if group_name != UV_GROUP:
            return [
                f"uv ({update_type}): later {manager} rule overrides the uv group with {group_name!r}"
            ]
    if slugs["pip_requirements"] != slugs["custom.regex"]:
        return [
            f"uv ({update_type}): uv pins have mismatched groupSlug "
            f"({slugs['pip_requirements']!r} vs {slugs['custom.regex']!r})"
        ]
    return []


def _collect_uv_group_findings(
    config: dict[str, Any], manifest_version: str, pip_version: str
) -> list[str]:
    """Run _uv_group_finding across patch, minor, and major; turn an
    UnsupportedPackageRuleError (Renovate main 208cd854196d,
    lib/util/package-rules/matchers.ts:29-48, current-version.ts:24-79) into
    one fail-closed finding and stop the group check for that update type."""
    findings: list[str] = []
    for update_type in ("patch", "minor", "major"):
        try:
            group_findings = _uv_group_finding(config, update_type, manifest_version, pip_version)
        except UnsupportedPackageRuleError as exc:
            group_findings = [
                f"packageRules[{exc.rule_index}]: "
                f"the uv check does not model {exc.key}={exc.value!r}"
            ]
        for finding in group_findings:
            if finding not in findings:
                findings.append(finding)
    return findings


def uv_lock_generator_findings(config: dict[str, Any], read: Callable[[str], str]) -> list[str]:
    """Every way the config fails to move both uv pins in one pull request."""
    manager = uv_lock_generator_manager(config)
    if manager is None:
        return ["no single custom manager for uv"]
    errors: list[str] = []
    manifest_text = read(LOCK_MANIFEST)
    version = str(json.loads(manifest_text)["uv_version"])
    manifest_finding = _uv_manifest_finding(manager, manifest_text, version)
    if manifest_finding is not None:
        errors.append(manifest_finding)
    pins = re.findall(r"^uv==([^\s#]+)\s*$", read(TOOLING_INPUT), re.MULTILINE)
    if len(pins) != 1:
        return [f"{TOOLING_INPUT}: expected one exact uv pin"]
    patterns = config["pip_requirements"]["managerFilePatterns"]
    if not any(re.search(pattern[1:-1], TOOLING_INPUT) for pattern in patterns):
        errors.append(f"{TOOLING_INPUT}: outside pip_requirements file patterns")
    for path in (TOOLING_INPUT, LOCK_MANIFEST):
        if _is_ignored_by_renovate(path, config["ignorePaths"]):
            errors.append(f"{path}: ignored by Renovate")
    errors.extend(_collect_uv_group_findings(config, version, pins[0]))
    return errors


class UvLockGeneratorGroup(unittest.TestCase):
    """Positive: the checked-in config has no findings and manifest.json carries
    uv_version. Negative: a removed manager, a manager pattern that misses the
    manifest, matchStrings that read nothing, a removed uv group rule and a uv
    group rule placed before Python (patch) each fail. Boundary: a sibling
    uv_version_note key in manifest.json is ignored."""

    config: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        RenovateFilePatterns.setUpClass()
        cls.config = RenovateFilePatterns.config

    def findings(
        self,
        change: Callable[[dict[str, Any]], None] | None = None,
        files: dict[str, str] | None = None,
    ) -> list[str]:
        config = json.loads(json.dumps(self.config))
        if change is not None:
            change(config)

        def read(path: str) -> str:
            if files and path in files:
                return files[path]
            return (ROOT / path).read_text(encoding="utf-8")

        return uv_lock_generator_findings(config, read)

    def test_checked_in_config_owns_uv_pins(self) -> None:
        self.assertEqual(self.findings(), [])
        manifest = json.loads((ROOT / LOCK_MANIFEST).read_text(encoding="utf-8"))
        self.assertIn("uv_version", manifest)
        self.assertRegex(manifest["uv_version"], r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_uv_group_requires_both_updates(self) -> None:
        (rule,) = [r for r in self.config["packageRules"] if r.get("groupName") == UV_GROUP]
        self.assertEqual(rule.get("matchPackageNames"), ["uv"])
        self.assertEqual(rule.get("minimumGroupSize"), 2)
        self.assertEqual(rule.get("matchDatasources"), ["pypi"])
        self.assertCountEqual(
            rule.get("matchFileNames", []), ["requirements/locks/tooling-tests.in", LOCK_MANIFEST]
        )

    def test_disabled_pip_uv_without_group_name_fails(self) -> None:
        def disable(config: dict[str, Any]) -> None:
            config["packageRules"].append(
                {
                    "matchManagers": ["pip_requirements"],
                    "matchFileNames": [TOOLING_INPUT],
                    "matchPackageNames": ["uv"],
                    "enabled": False,
                }
            )

        self.assertIn(
            f"{TOOLING_INPUT}: pip_requirements uv (patch) disabled", self.findings(disable)
        )

    def test_negative_and_regex_package_patterns_match_like_renovate(self) -> None:
        """Negative and boundary: !glob, !/re/ and /re/i follow Renovate string-match.ts."""
        pip_disabled = f"{TOOLING_INPUT}: pip_requirements uv (patch) disabled"

        def append_pip_disable(patterns: list[str]) -> Callable[[dict[str, Any]], None]:
            def change(config: dict[str, Any]) -> None:
                config["packageRules"].append(
                    {
                        "matchManagers": ["pip_requirements"],
                        "matchFileNames": [TOOLING_INPUT],
                        "matchPackageNames": patterns,
                        "enabled": False,
                    }
                )

            return change

        self.assertIn(pip_disabled, self.findings(append_pip_disable(["!numpy"])))
        self.assertEqual(self.findings(append_pip_disable(["!uv"])), [])
        self.assertIn(pip_disabled, self.findings(append_pip_disable(["UV"])))
        self.assertIn(pip_disabled, self.findings(append_pip_disable(["/^UV$/i"])))
        self.assertIn(pip_disabled, self.findings(append_pip_disable(["!/^numpy$/i"])))
        self.assertEqual(self.findings(append_pip_disable(["!/^UV$/i"])), [])
        self.assertEqual(self.findings(append_pip_disable(["u*", "!uv"])), [])
        self.assertEqual(self.findings(append_pip_disable([])), [])

    def _append_pip_disable_rule(
        self, package: str = "uv", **extra: Any
    ) -> Callable[[dict[str, Any]], None]:
        def change(config: dict[str, Any]) -> None:
            config["packageRules"].append(
                {
                    "matchManagers": ["pip_requirements"],
                    "matchFileNames": [TOOLING_INPUT],
                    **extra,
                    "matchPackageNames": [package],
                    "enabled": False,
                }
            )

        return change

    def test_unmodeled_current_version_range_fails_closed(self) -> None:
        """Negative: a matchCurrentVersion range on uv is reported as not modeled."""
        rule_index = len(self.config["packageRules"])
        self.assertEqual(
            self.findings(self._append_pip_disable_rule(matchCurrentVersion=">=0")),
            [
                f"packageRules[{rule_index}]: "
                "the uv check does not model matchCurrentVersion='>=0'"
            ],
        )

    def test_current_version_regex_and_pep440_release_match_like_renovate(self) -> None:
        """Positive and negative: regexes and exact PEP 440 releases match CurrentVersionMatcher."""
        version = str(json.loads((ROOT / LOCK_MANIFEST).read_text(encoding="utf-8"))["uv_version"])
        pip_disabled = [
            f"{TOOLING_INPUT}: pip_requirements uv ({kind}) disabled"
            for kind in ("patch", "minor", "major")
        ]
        for matching in (r"/^0\./", version, f"{version}.0"):
            with self.subTest(matchCurrentVersion=matching):
                self.assertEqual(
                    self.findings(self._append_pip_disable_rule(matchCurrentVersion=matching)),
                    pip_disabled,
                )
        for non_matching in (r"/^1\./", r"!/^0\./", "0.13.5"):
            with self.subTest(matchCurrentVersion=non_matching):
                self.assertEqual(
                    self.findings(self._append_pip_disable_rule(matchCurrentVersion=non_matching)),
                    [],
                )

    def test_unmodeled_package_rule_matcher_fails_closed(self) -> None:
        """Negative: an unmodeled match* key on a rule that matches uv fails closed."""
        rule_index = len(self.config["packageRules"])
        self.assertEqual(
            self.findings(self._append_pip_disable_rule(matchConfidence=["high"])),
            [
                f"packageRules[{rule_index}]: "
                "the uv check does not model matchConfidence=['high']"
            ],
        )

    def test_unmodeled_matcher_on_non_uv_rule_is_ignored(self) -> None:
        """Boundary: a rule excluded by a modeled matcher is skipped despite unmodeled keys."""
        self.assertEqual(
            self.findings(
                self._append_pip_disable_rule(package="numpy", matchCurrentVersion=">=0")
            ),
            [],
        )
        self.assertEqual(
            self.findings(self._append_pip_disable_rule(package="numpy", matchConfidence=["high"])),
            [],
        )

    def test_uv_managers_must_be_enabled(self) -> None:
        pip_disabled = f"{TOOLING_INPUT}: pip_requirements uv (patch) disabled"
        manifest_disabled = f"{LOCK_MANIFEST}: custom.regex uv (patch) disabled"
        self.assertEqual(self.findings(lambda cfg: cfg.update(enabledManagers=[])), [])
        npm_findings = self.findings(lambda cfg: cfg.update(enabledManagers=["npm"]))
        self.assertIn(pip_disabled, npm_findings)
        self.assertIn(manifest_disabled, npm_findings)
        self.assertIn(
            pip_disabled, self.findings(lambda cfg: cfg.update(enabledManagers=["custom.regex"]))
        )
        self.assertIn(
            manifest_disabled,
            self.findings(lambda cfg: cfg.update(enabledManagers=["pip_requirements"])),
        )
        self.assertIn(
            manifest_disabled,
            self.findings(lambda cfg: cfg.update({"custom.regex": {"enabled": False}})),
        )

    def test_missing_tooling_input_pin_fails(self) -> None:
        self.assertIn(
            f"{TOOLING_INPUT}: expected one exact uv pin",
            self.findings(files={TOOLING_INPUT: "pytest-timeout==1.0"}),
        )

    def test_tooling_input_must_be_visible_to_pip_manager(self) -> None:
        def ignore(config: dict[str, Any]) -> None:
            config["ignorePaths"].append(TOOLING_INPUT)

        def exclude(config: dict[str, Any]) -> None:
            config["pip_requirements"]["managerFilePatterns"] = [r"/^requirements\.txt$/"]

        self.assertIn(f"{TOOLING_INPUT}: ignored by Renovate", self.findings(ignore))
        self.assertIn(
            f"{TOOLING_INPUT}: outside pip_requirements file patterns", self.findings(exclude)
        )

    def test_ignored_uv_manifest_fails(self) -> None:
        def ignore_exact(config: dict[str, Any]) -> None:
            config["ignorePaths"].append(LOCK_MANIFEST)

        def ignore_substring(config: dict[str, Any]) -> None:
            config["ignorePaths"] = ["requirements/locks"]

        self.assertIn(f"{LOCK_MANIFEST}: ignored by Renovate", self.findings(ignore_exact))
        substring_findings = self.findings(ignore_substring)
        self.assertIn(f"{LOCK_MANIFEST}: ignored by Renovate", substring_findings)
        self.assertIn(f"{TOOLING_INPUT}: ignored by Renovate", substring_findings)

    def test_manifest_allows_space_before_colon(self) -> None:
        self.assertEqual(self.findings(files={LOCK_MANIFEST: '{"uv_version" : "0.13.6"}'}), [])

    def test_missing_manager_fails(self) -> None:
        def remove(config: dict[str, Any]) -> None:
            config["customManagers"] = [
                m for m in config["customManagers"] if m.get("depNameTemplate") != "uv"
            ]

        self.assertEqual(self.findings(remove), ["no single custom manager for uv"])

    def test_manager_pattern_missing_manifest_fails(self) -> None:
        def mispoint(config: dict[str, Any]) -> None:
            manager = uv_lock_generator_manager(config)
            assert manager is not None
            manager["managerFilePatterns"] = ["/^requirements/locks/other\\.json$/"]

        self.assertIn(UV_SELECTOR_FINDING, self.findings(mispoint))

    def manager_findings(self, **changes: Any) -> list[str]:
        def change(config: dict[str, Any]) -> None:
            manager = uv_lock_generator_manager(config)
            assert manager is not None
            manager.update(changes)

        return self.findings(change)

    def test_manager_requires_one_selector(self) -> None:
        selectors = [r"/^requirements/locks/manifest\.json$/", r"/^another\.json$/"]
        for patterns in ([], selectors):
            with self.subTest(patterns=patterns):
                self.assertIn(
                    "uv manager needs exactly one managerFilePatterns entry",
                    self.manager_findings(managerFilePatterns=patterns),
                )

    def test_selector_cannot_match_sibling(self) -> None:
        self.assertIn(
            UV_SELECTOR_FINDING,
            self.manager_findings(managerFilePatterns=[r"/^requirements/locks/[^/]+\.json$/"]),
        )

    def test_selector_cannot_match_nested_manifest(self) -> None:
        self.assertIn(
            UV_SELECTOR_FINDING,
            self.manager_findings(
                managerFilePatterns=[r"/^requirements/locks/(?:.*/)?manifest\.json$/"]
            ),
        )

    def test_selector_cannot_match_archived_manifest(self) -> None:
        """Negative: even an optional literal archive directory widens the selector."""
        self.assertIn(
            UV_SELECTOR_FINDING,
            self.manager_findings(
                managerFilePatterns=[r"/^requirements/locks/(?:archive/)?manifest\.json$/"]
            ),
        )

    def test_manager_requires_pypi(self) -> None:
        self.assertIn(
            "uv manager datasourceTemplate must be pypi",
            self.manager_findings(datasourceTemplate="npm"),
        )

    def test_current_value_rule_cannot_split_uv_pins(self) -> None:
        """Negative: a later pip rule for this version overrides all three update types."""
        version = json.loads((ROOT / LOCK_MANIFEST).read_text(encoding="utf-8"))["uv_version"]

        def override(config: dict[str, Any]) -> None:
            config["packageRules"].append(
                {
                    "matchManagers": ["pip_requirements"],
                    "matchPackageNames": ["uv"],
                    "matchCurrentValue": f"=={version}",
                    "groupName": "split",
                }
            )

        self.assertEqual(
            self.findings(override),
            [
                f"uv ({kind}): later pip_requirements rule overrides the uv group with 'split'"
                for kind in ("patch", "minor", "major")
            ],
        )

    def test_later_pypi_group_cannot_override_uv(self) -> None:
        for manager, path in (
            ("custom.regex", LOCK_MANIFEST),
            ("pip_requirements", "requirements/locks/tooling-tests.in"),
        ):

            def override(config: dict[str, Any], manager: str = manager, path: str = path) -> None:
                config["packageRules"].append(
                    {
                        "matchManagers": [manager],
                        "matchDatasources": ["pypi"],
                        "matchPackageNames": ["uv"],
                        "matchFileNames": [path],
                        "matchUpdateTypes": ["major"],
                        "groupName": "another group",
                    }
                )

            with self.subTest(manager=manager):
                self.assertIn(
                    f"uv (major): later {manager} rule overrides the uv group with 'another group'",
                    self.findings(override),
                )

    def test_later_shared_rule_can_restore_uv_group_after_pip_override(self) -> None:
        """Positive and negative: folding matching rules lets a later shared rule restore UV_GROUP."""
        pip_split = {
            "matchManagers": ["pip_requirements"],
            "matchFileNames": [TOOLING_INPUT],
            "matchPackageNames": ["uv"],
            "groupName": "split",
        }
        shared_restore = {
            "matchDatasources": ["pypi"],
            "matchFileNames": [TOOLING_INPUT, LOCK_MANIFEST],
            "matchPackageNames": ["uv"],
            "groupName": UV_GROUP,
        }
        self.assertIn(
            "uv (patch): later pip_requirements rule overrides the uv group with 'split'",
            self.findings(lambda cfg: cfg["packageRules"].append(pip_split)),
        )
        self.assertEqual(
            self.findings(lambda cfg: cfg["packageRules"].extend([pip_split, shared_restore])),
            [],
        )

    def test_uv_group_slug_must_agree_across_both_pins(self) -> None:
        """Negative and positive: diverging groupSlug splits branches; a shared slug agrees."""
        pip_slug = {
            "matchManagers": ["pip_requirements"],
            "matchFileNames": [TOOLING_INPUT],
            "matchPackageNames": ["uv"],
            "groupSlug": "uv-pip-only",
        }
        shared_slug = {
            "matchDatasources": ["pypi"],
            "matchFileNames": [TOOLING_INPUT, LOCK_MANIFEST],
            "matchPackageNames": ["uv"],
            "groupSlug": "uv-lock-generator",
        }

        def top_and_pip_slug(cfg: dict[str, Any]) -> None:
            cfg["groupSlug"] = "top-slug"
            cfg["packageRules"].append(pip_slug)

        self.assertIn(
            "uv (patch): uv pins have mismatched groupSlug ('uv-pip-only' vs None)",
            self.findings(lambda cfg: cfg["packageRules"].append(pip_slug)),
        )
        self.assertEqual(
            self.findings(lambda cfg: cfg["packageRules"].append(shared_slug)),
            [],
        )
        self.assertEqual(
            self.findings(lambda cfg: cfg["packageRules"].extend([pip_slug, shared_slug])),
            [],
        )
        self.assertEqual(
            self.findings(lambda cfg: cfg.update(groupSlug="top-slug")),
            [],
        )
        self.assertIn(
            "uv (patch): uv pins have mismatched groupSlug ('uv-pip-only' vs 'top-slug')",
            self.findings(top_and_pip_slug),
        )

    def test_match_strings_reading_nothing_fails(self) -> None:
        def miss(config: dict[str, Any]) -> None:
            manager = uv_lock_generator_manager(config)
            assert manager is not None
            manager["matchStrings"] = [
                '"other_version":\\s*"(?<currentValue>[0-9]+\\.[0-9]+\\.[0-9]+)"'
            ]

        self.assertTrue(
            any(e.startswith(f"{LOCK_MANIFEST}: manager reads []") for e in self.findings(miss))
        )

    def test_removed_uv_group_rule_splits_copies(self) -> None:
        def ungroup(config: dict[str, Any]) -> None:
            config["packageRules"] = [
                r for r in config["packageRules"] if r.get("groupName") != UV_GROUP
            ]

        errors = self.findings(ungroup)
        self.assertTrue(any(e.startswith("uv (patch):") for e in errors))
        self.assertTrue(any(e.startswith("uv (minor):") for e in errors))

    def test_uv_rule_before_python_patch_splits_copies(self) -> None:
        def move_early(config: dict[str, Any]) -> None:
            uv_rules = [r for r in config["packageRules"] if r.get("groupName") == UV_GROUP]
            rest = [r for r in config["packageRules"] if r.get("groupName") != UV_GROUP]
            patch_idx = next(
                i for i, r in enumerate(rest) if r.get("groupName") == "Python (patch)"
            )
            config["packageRules"] = [*rest[:patch_idx], *uv_rules, *rest[patch_idx:]]

        errors = self.findings(move_early)
        self.assertTrue(any(e.startswith("uv (patch):") for e in errors))
        self.assertTrue(any(e.startswith("uv (minor):") for e in errors))

    def test_manifest_lookalike_key_is_ignored(self) -> None:
        real = json.loads((ROOT / LOCK_MANIFEST).read_text(encoding="utf-8"))
        planted = json.dumps(
            {
                "uv_version_note": "1.2.3",
                "uv_version": real["uv_version"],
                "locks": real["locks"],
            },
            indent=2,
        )
        self.assertIn('"uv_version_note": "1.2.3"', planted)
        self.assertEqual(self.findings(files={LOCK_MANIFEST: planted}), [])


RUST_PINS = {
    CONFIG: re.compile(r'^RUST_VERSION="([^"]*)"', re.MULTILINE),
    "rust-toolchain.toml": re.compile(r'^channel = "([^"]*)"', re.MULTILINE),
    "Cargo.toml": re.compile(r'^rust-version = "([^"]*)"', re.MULTILINE),
    "core/src/rust/Cargo.toml": re.compile(r'^rust-version = "([^"]*)"', re.MULTILINE),
    ".github/workflows/rust-ci.yml": re.compile(r"^[ \t]+toolchain:[ \t]*(\S+)", re.MULTILINE),
}


def rust_manager(config: dict[str, Any]) -> dict[str, Any] | None:
    managers = [
        m
        for m in config.get("customManagers", [])
        if m.get("depNameTemplate") == "rust" and m.get("datasourceTemplate") == "rust-version"
    ]
    return managers[0] if len(managers) == 1 else None


def rust_findings(config: dict[str, Any], read: Callable[[str], str]) -> list[str]:
    """Every way the config fails to own the Rust toolchain pins as one group (ADR-3318)."""
    manager = rust_manager(config)
    if manager is None:
        return ["no single custom manager for rust / rust-version"]
    selectors = [re.compile(p[1:-1]) for p in manager.get("managerFilePatterns", [])]
    patterns = [re.compile(to_python(m)) for m in manager.get("matchStrings", [])]
    errors: list[str] = []
    for path, pin in RUST_PINS.items():
        text = read(path)
        want = pin.findall(text)
        if not want:
            errors.append(f"{path}: no Rust pin")
            continue
        if not any(s.search(path) for s in selectors):
            errors.append(f"{path} is not selected by the manager")
            continue
        got = [m["currentValue"] for p in patterns for m in p.finditer(text)]
        if sorted(got) != sorted(want):
            errors.append(f"{path}: manager reads {sorted(got)}, the pin is {sorted(want)}")
    rules = config.get("packageRules", [])
    if not any(
        r.get("matchManagers") == ["rust-toolchain"] and r.get("enabled") is False for r in rules
    ):
        errors.append("the rust-toolchain manager still updates rust-toolchain.toml")
    if not any(
        r.get("matchManagers") == ["github-actions"]
        and r.get("matchDepNames") == ["rust"]
        and r.get("matchDepTypes") == ["uses-with"]
        and r.get("enabled") is False
        for r in rules
    ):
        errors.append("the github-actions manager still updates the Rust toolchain input")
    if not any(
        r.get("matchManagers") == ["custom.regex"]
        and r.get("matchDepNames") == ["rust"]
        and r.get("groupName") == "Rust toolchain"
        for r in rules
    ):
        errors.append("no Rust toolchain group rule")
    return errors


class RustToolchainGroup(unittest.TestCase):
    """Positive: the checked-in config owns every Rust pin. Negative: a manager that
    misses a file, a dropped match string, the built-in manager left on and a
    missing group each fail. Boundary: other keys and other workflows' inputs are
    not read."""

    config: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        RenovateFilePatterns.setUpClass()
        cls.config = RenovateFilePatterns.config

    def findings(self, change: Callable[[dict[str, Any]], None] | None = None) -> list[str]:
        config = json.loads(json.dumps(self.config))
        if change is not None:
            change(config)
        return rust_findings(config, lambda path: (ROOT / path).read_text(encoding="utf-8"))

    def manager(self, config: dict[str, Any]) -> dict[str, Any]:
        manager = rust_manager(config)
        assert manager is not None
        return manager

    def test_checked_in_config_owns_every_pin(self) -> None:
        self.assertEqual(self.findings(), [])

    def test_other_lines_are_not_read(self) -> None:
        patterns = [re.compile(to_python(m)) for m in self.manager(self.config)["matchStrings"]]
        for line in (
            'RUST_TOOLCHAIN="1.99.0"\n',
            'PYTHON_CI_VERSION="3.14.8"\n',
            "toolchain: 12.4\n",
            'version = "1.99.0"\n',
        ):
            with self.subTest(line=line):
                self.assertFalse(any(p.search(line) for p in patterns))

    def test_manager_without_the_workflow_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["managerFilePatterns"] = [
                p for p in manager["managerFilePatterns"] if "rust-ci" not in p
            ]

        self.assertIn(
            ".github/workflows/rust-ci.yml is not selected by the manager", self.findings(drop)
        )

    def test_manager_without_the_toolchain_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["matchStrings"] = [s for s in manager["matchStrings"] if "channel" not in s]

        self.assertTrue(any(e.startswith("rust-toolchain.toml:") for e in self.findings(drop)))

    def test_builtin_manager_left_enabled_fails(self) -> None:
        def enable(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("matchManagers") == ["rust-toolchain"]:
                    rule["enabled"] = True

        self.assertIn(
            "the rust-toolchain manager still updates rust-toolchain.toml", self.findings(enable)
        )

    def test_actions_manager_left_enabled_fails(self) -> None:
        def enable(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("matchManagers") == ["github-actions"] and rule.get(
                    "matchDepNames"
                ) == ["rust"]:
                    rule["enabled"] = True

        self.assertIn(
            "the github-actions manager still updates the Rust toolchain input",
            self.findings(enable),
        )

    def test_missing_group_fails(self) -> None:
        def ungroup(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("groupName") == "Rust toolchain":
                    rule.pop("groupName")

        self.assertIn("no Rust toolchain group rule", self.findings(ungroup))

    def test_missing_manager_fails(self) -> None:
        def remove(config: dict[str, Any]) -> None:
            config["customManagers"] = [
                m for m in config["customManagers"] if m.get("depNameTemplate") != "rust"
            ]

        self.assertEqual(
            self.findings(remove), ["no single custom manager for rust / rust-version"]
        )


# CI Node.js (ADR-3312 pattern): one custom manager owns build-config.env's
# NODE_VERSION, .pre-commit-config.yaml's default_language_version.node, .node-version
# and every workflow `node-version:` pin with an exact three-part version (x.y.z).
PRECOMMIT_CONFIG = ".pre-commit-config.yaml"
NODE_FILE = ".node-version"
WORKFLOW_NODE = re.compile(r"node-version:[ \t]*['\"]?([0-9]+\.[0-9]+\.[0-9]+)['\"]?")
PRECOMMIT_NODE = re.compile(
    r"^default_language_version:[ \t]*\n(?:[ \t]+.*\n)*?[ \t]+node:[ \t]*['\"]?([0-9]+\.[0-9]+\.[0-9]+)['\"]?",
    re.MULTILINE,
)


def node_ci_manager(config: dict[str, Any]) -> dict[str, Any] | None:
    """The custom manager that owns CI Node.js, or None."""
    managers = [
        m
        for m in config.get("customManagers", [])
        if m.get("depNameTemplate") == "node"
        and m.get("datasourceTemplate") == "github-releases"
        and m.get("packageNameTemplate") == "actions/node-versions"
        and m.get("versioningTemplate") == "node"
    ]
    return managers[0] if len(managers) == 1 else None


def node_ci_findings(
    config: dict[str, Any], files: list[str], read: Callable[[str], str]
) -> list[str]:
    """Every way the config fails to own the CI Node.js pins as one group."""
    manager = node_ci_manager(config)
    if manager is None:
        return ["no single custom manager for node / actions/node-versions"]
    selectors = [re.compile(p[1:-1]) for p in manager.get("managerFilePatterns", [])]
    patterns = [re.compile(to_python(m)) for m in manager.get("matchStrings", [])]
    errors: list[str] = []
    scope = [CONFIG, PRECOMMIT_CONFIG, NODE_FILE, *(f for f in files if WORKFLOW.match(f))]
    for path in scope:
        text = read(path)
        if path == CONFIG:
            want = re.findall(r'^NODE_VERSION="([^"]*)"', text, re.MULTILINE)
        elif path == PRECOMMIT_CONFIG:
            want = PRECOMMIT_NODE.findall(text)
        elif path == NODE_FILE:
            want = text.split()
        else:
            want = WORKFLOW_NODE.findall(text)
        if not want:
            continue
        if not any(s.search(path) for s in selectors):
            errors.append(f"{path} is not selected by the manager")
            continue
        got = [m["currentValue"] for p in patterns for m in p.finditer(text)]
        if sorted(got) != sorted(want):
            errors.append(f"{path}: manager reads {sorted(got)}, the check reads {sorted(want)}")
    rules = config.get("packageRules", [])
    if not any(
        r.get("matchManagers") == ["github-actions"]
        and r.get("matchDepNames") == ["node"]
        and r.get("matchDepTypes") == ["uses-with"]
        and r.get("enabled") is False
        for r in rules
    ):
        errors.append("the github-actions manager still updates node-version")
    if not any(r.get("matchManagers") == ["nodenv"] and r.get("enabled") is False for r in rules):
        errors.append("the nodenv manager still updates .node-version")
    if not any(
        r.get("matchManagers") == ["custom.regex"]
        and r.get("matchDepNames") == ["node"]
        and r.get("groupName") == "CI Node.js"
        and r.get("automerge") is False
        and r.get("draftPR") is False
        for r in rules
    ):
        errors.append("no CI Node.js group rule")
    return errors


class NodeCiVersionGroup(unittest.TestCase):
    """Positive: the checked-in config owns every Node.js pin. Negative: a manager that
    misses a file, a dropped match string, the github-actions manager left on and a
    missing group each fail. Boundary: major-only workflow pins and other keys are
    not read."""

    config: ClassVar[dict[str, Any]]
    files: ClassVar[list[str]]

    @classmethod
    def setUpClass(cls) -> None:
        RenovateFilePatterns.setUpClass()
        cls.config = RenovateFilePatterns.config
        cls.files = RenovateFilePatterns.files

    def findings(self, change: Callable[[dict[str, Any]], None] | None = None) -> list[str]:
        config = json.loads(json.dumps(self.config))
        if change is not None:
            change(config)
        return node_ci_findings(
            config, self.files, lambda path: (ROOT / path).read_text(encoding="utf-8")
        )

    def manager(self, config: dict[str, Any]) -> dict[str, Any]:
        manager = node_ci_manager(config)
        assert manager is not None
        return manager

    def test_checked_in_config_owns_every_pin(self) -> None:
        self.assertEqual(self.findings(), [])
        pins = [f for f in self.files if WORKFLOW.match(f)]
        read = (ROOT / CONFIG).read_text(encoding="utf-8")
        self.assertIn("NODE_VERSION=", read)
        self.assertTrue(
            any(WORKFLOW_NODE.search((ROOT / f).read_text(encoding="utf-8")) for f in pins)
        )

    def test_major_only_and_other_lines_are_not_read(self) -> None:
        patterns = [re.compile(to_python(m)) for m in self.manager(self.config)["matchStrings"]]
        for line in (
            'node-version: "24"\n',
            "node-version: '24'\n",
            "node-version: 24\n",
            'NODE_JS_VERSION="24.22.1"\n',
            'node: "24"\n',
        ):
            with self.subTest(line=line):
                self.assertFalse(any(p.search(line) for p in patterns))

    def test_manager_without_workflows_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["managerFilePatterns"] = [
                p for p in manager["managerFilePatterns"] if "workflows" not in p
            ]

        self.assertTrue(any("not selected" in e for e in self.findings(drop)))

    def test_manager_without_node_file_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["managerFilePatterns"] = [
                p for p in manager["managerFilePatterns"] if "node-version" not in p
            ]

        self.assertIn(f"{NODE_FILE} is not selected by the manager", self.findings(drop))

    def test_manager_without_node_file_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["matchStrings"] = manager["matchStrings"][1:]

        self.assertTrue(any(e.startswith(f"{NODE_FILE}: ") for e in self.findings(drop)))

    def test_nodenv_manager_left_on_fails(self) -> None:
        def enable(config: dict[str, Any]) -> None:
            config["packageRules"] = [
                r for r in config["packageRules"] if r.get("matchManagers") != ["nodenv"]
            ]

        self.assertIn("the nodenv manager still updates .node-version", self.findings(enable))

    def test_node_file_string_reads_nothing_else(self) -> None:
        pattern = re.compile(to_python(self.manager(self.config)["matchStrings"][0]))
        self.assertEqual(pattern.findall("24.22.1\n"), ["24.22.1"])
        for text in ("name: lint\n24.22.1\n", "24\n", "24.22.1\nmore\n"):
            with self.subTest(text=text):
                self.assertIsNone(pattern.search(text))

    def test_manager_without_precommit_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["managerFilePatterns"] = [
                p for p in manager["managerFilePatterns"] if "pre-commit" not in p
            ]

        self.assertIn(f"{PRECOMMIT_CONFIG} is not selected by the manager", self.findings(drop))

    def test_manager_without_config_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["managerFilePatterns"] = [
                p for p in manager["managerFilePatterns"] if "build-config" not in p
            ]

        self.assertIn(f"{CONFIG} is not selected by the manager", self.findings(drop))

    def test_manager_without_node_version_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["matchStrings"] = [
                s for s in manager["matchStrings"] if "NODE_VERSION" not in s
            ]

        self.assertTrue(any(e.startswith(f"{CONFIG}:") for e in self.findings(drop)))

    def test_manager_without_precommit_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["matchStrings"] = [
                s for s in manager["matchStrings"] if "default_language_version" not in s
            ]

        self.assertTrue(any(e.startswith(f"{PRECOMMIT_CONFIG}:") for e in self.findings(drop)))

    def test_manager_without_workflow_string_fails(self) -> None:
        def drop(config: dict[str, Any]) -> None:
            manager = self.manager(config)
            manager["matchStrings"] = [
                s for s in manager["matchStrings"] if "node-version" not in s
            ]

        self.assertTrue(any(".github/workflows/" in e for e in self.findings(drop)))

    def test_github_actions_copy_left_enabled_fails(self) -> None:
        def enable(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if (
                    rule.get("matchManagers") == ["github-actions"]
                    and rule.get("matchDepNames") == ["node"]
                    and rule.get("matchDepTypes") == ["uses-with"]
                ):
                    rule["enabled"] = True

        self.assertIn(
            "the github-actions manager still updates node-version", self.findings(enable)
        )

    def test_missing_group_fails(self) -> None:
        def ungroup(config: dict[str, Any]) -> None:
            for rule in config["packageRules"]:
                if rule.get("groupName") == "CI Node.js":
                    rule.pop("groupName")

        self.assertIn("no CI Node.js group rule", self.findings(ungroup))

    def test_missing_manager_fails(self) -> None:
        def remove(config: dict[str, Any]) -> None:
            config["customManagers"] = [
                m for m in config["customManagers"] if m.get("depNameTemplate") != "node"
            ]

        self.assertEqual(
            self.findings(remove), ["no single custom manager for node / actions/node-versions"]
        )


if __name__ == "__main__":
    unittest.main()
