#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""The lock writer keeps tooling-lock pins in Python hooks in sync (issue #3656).

Positive: the checked-in `.pre-commit-config.yaml` is a fixed point, and a
planted lock version moves every matching hook pin in flow and block lists.
Boundary: bare names, ranges and extras in a lock-tied dependency are refused
rather than guessed; a package present only in the lock is still tied.
Drift: the writer's package set, every lock-tied pin in the config, and the
Renovate skip rule name the same packages.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ci import check_python_dependency_locks as locks  # noqa: E402

CONFIG = ROOT / ".pre-commit-config.yaml"
LOCK = ROOT / locks.TOOLING_HOOK_LOCK
RENOVATE = ROOT / "renovate.json"


def package_key(name: str) -> str:
    """Normalise a distribution name to its PEP 503 key."""
    return re.sub(r"[-_.]+", "-", name).lower()


def config_pin_keys() -> set[str]:
    """Every package key with an exact pin in additional_dependencies."""
    text = CONFIG.read_text(encoding="utf-8")
    return {
        package_key(dependency.name)
        for dependency in locks.hook_dependency_entries(text)
        if dependency.operator == "=="
    }


def renovate_skip_names() -> set[str]:
    """The package names of the one disabled pre-commit-python rule."""
    doc = json.loads(RENOVATE.read_text(encoding="utf-8"))
    matches = [
        rule
        for rule in doc["packageRules"]
        if rule.get("matchManagers") == ["pre-commit"]
        and rule.get("matchDepTypes") == ["pre-commit-python"]
        and rule.get("enabled") is False
    ]
    assert len(matches) == 1
    return {package_key(name) for name in matches[0]["matchDepNames"]}


def hook_config(value: str) -> str:
    """A minimal Python hook whose additional_dependencies value is ``value``."""
    return (
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: probe\n"
        "        name: probe\n"
        "        entry: probe\n"
        "        language: python\n"
        f"        additional_dependencies: {value}\n"
        "        files: '^scripts/'\n"
    )


class ToolingHookPinTest(unittest.TestCase):
    """The writer, the checked-in config and the Renovate rule agree."""

    def setUp(self) -> None:
        """Read the checked-in inputs once."""
        self.config = CONFIG.read_text(encoding="utf-8")
        self.lock = LOCK.read_text(encoding="utf-8")

    def lock_with(self, package: str, old: str, new: str) -> str:
        """Move one tooling-lock version, adding a missing distribution."""
        if f"{package}=={old}" in self.lock:
            return self.lock.replace(f"{package}=={old}", f"{package}=={new}", 1)
        return self.lock + f"{package}=={new} \\\n    --hash=sha256:{'0' * 64}\n"

    def test_checked_in_config_is_a_fixed_point(self) -> None:
        """No hook pin in the tree is stale against the tooling lock."""
        updated, changes = locks.sync_tooling_hook_pins(self.config, self.lock)
        self.assertEqual(changes, [])
        self.assertEqual(updated, self.config)

    def test_a_planted_lock_version_moves_matching_hook_pins(self) -> None:
        """A lock move rewrites every occurrence, not just the sentinel hook."""
        planted = self.lock.replace("bandit==1.9.4", "bandit==1.9.5")
        updated, changes = locks.sync_tooling_hook_pins(self.config, planted)
        self.assertEqual(len(changes), 3)
        self.assertNotIn("bandit==1.9.4", updated)
        self.assertEqual(updated.count("bandit==1.9.5"), 3)

    def test_flow_multiline_and_comment_pins_move(self) -> None:
        """A flow list spread over lines is read as a list, not as one line."""
        text = hook_config(
            "[\n"
            "          pytest==9.2.0,  # suite runner\n"
            "          zopfli==0.4.3,\n"
            "        ]"
        )
        updated, changes = locks.sync_tooling_hook_pins(
            text, self.lock_with("pytest", "9.2.0", "9.2.1")
        )
        self.assertEqual(len(changes), 1)
        self.assertIn("pytest==9.2.1,  # suite runner", updated)
        self.assertIn("zopfli==0.4.3", updated)

    def test_block_list_pin_moves(self) -> None:
        """The block-list spelling is the same dependency, not a comment."""
        text = hook_config(
            "\n"
            "          - pytest==9.2.0\n"
            "          - zopfli==0.4.3"
        )
        updated, changes = locks.sync_tooling_hook_pins(
            text, self.lock_with("pytest", "9.2.0", "9.2.1")
        )
        self.assertEqual(len(changes), 1)
        self.assertIn("          - pytest==9.2.1\n", updated)

    def test_spaced_exact_pin_is_accepted(self) -> None:
        """Spaces around ``==`` do not turn an exact pin into a range."""
        text = hook_config("[pytest == 9.2.0]")
        updated, changes = locks.sync_tooling_hook_pins(
            text, self.lock_with("pytest", "9.2.0", "9.2.1")
        )
        self.assertEqual(len(changes), 1)
        self.assertIn("pytest == 9.2.1", updated)

    def test_non_exact_lock_tied_pins_are_refused(self) -> None:
        """Bare names, ranges and extras are refused, never guessed."""
        for value in ("[pytest]", "[pytest>=9.2.0]", "[pytest[psutil]~=9.2.0]"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    locks.ContractError, "not an exact '==' lock pin"
                ):
                    locks.sync_tooling_hook_pins(hook_config(value), self.lock)

    def test_dependency_only_in_the_lock_is_tied(self) -> None:
        """The set comes from the lock, so a new distribution cannot be missed."""
        text = hook_config("[pytest==9.2.0, attrs==24.1.0]")
        planted = self.lock_with("attrs", "24.1.0", "25.1.0")
        updated, changes = locks.sync_tooling_hook_pins(text, planted)
        self.assertEqual(len(changes), 1)
        self.assertIn("attrs==25.1.0", updated)

    def test_a_package_outside_the_lock_is_ignored(self) -> None:
        """A hook-only range is not a lock mirror and is left alone."""
        text = hook_config("['semgrep>=1.186.0,<2.0']")
        updated, changes = locks.sync_tooling_hook_pins(text, self.lock)
        self.assertEqual(changes, [])
        self.assertEqual(updated, text)

    def test_a_second_pass_is_clean(self) -> None:
        """The rewritten config is already a fixed point."""
        planted = self.lock.replace("bandit==1.9.4", "bandit==1.9.5")
        updated, changes = locks.sync_tooling_hook_pins(self.config, planted)
        self.assertTrue(changes)
        again, second = locks.sync_tooling_hook_pins(updated, planted)
        self.assertEqual(second, [])
        self.assertEqual(again, updated)

    def test_a_non_exact_pin_is_refused(self) -> None:
        """A range is not silently converted to the lock version."""
        planted = self.config.replace('"bandit==1.9.4"', '"bandit>=1.9.4"', 1)
        with self.assertRaisesRegex(locks.ContractError, "not an exact '==' lock pin"):
            locks.sync_tooling_hook_pins(planted, self.lock)

    def test_a_change_outside_a_pin_token_is_refused(self) -> None:
        """A writer bug that changes anything but a version is refused."""
        original = locks._rewrite_hook_pins

        def drifting_rewrite(
            config_text: str, lock_pins: dict[str, str], changes: list[str]
        ) -> str:
            updated = original(config_text, lock_pins, changes)
            return updated.replace("bandit==1.9.5", "bandit==1.9.5  # drift", 1)

        planted = self.lock.replace("bandit==1.9.4", "bandit==1.9.5")
        with mock.patch.object(locks, "_rewrite_hook_pins", side_effect=drifting_rewrite):
            with self.assertRaisesRegex(locks.ContractError, "more than a pin version"):
                locks.sync_tooling_hook_pins(self.config, planted)

    def test_missing_config_or_lock_fails_closed(self) -> None:
        """One half of the pair is a contract error, not a silent skip."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / locks.PRE_COMMIT_CONFIG).write_text(
                hook_config("[pytest==9.2.0]"), encoding="utf-8"
            )
            findings = locks.tooling_hook_pin_findings(root)
            self.assertEqual(len(findings), 1)
            self.assertIn("must both exist", findings[0])
            with self.assertRaisesRegex(locks.ContractError, "must both exist"):
                locks.sync_config_hook_pins(root)

    def test_shared_package_list_matches_the_renovate_skip_rule(self) -> None:
        """The lock reader and the Renovate rule name the same packages."""
        lock_pins = locks.read_lock_pins(self.lock)
        self.assertEqual(config_pin_keys() & set(lock_pins), renovate_skip_names())

    def test_config_pins_cover_exactly_the_shared_packages(self) -> None:
        """A new lock-tied pin requires a config entry, not silent drift."""
        lock_pins = locks.read_lock_pins(self.lock)
        self.assertEqual(config_pin_keys() & set(lock_pins), renovate_skip_names())


if __name__ == "__main__":
    unittest.main()
