#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""The lock writer keeps tooling-lock pins in Python hooks in sync (issue #3656).

Positive: the checked-in `.pre-commit-config.yaml` is a fixed point, and a
planted lock version moves every matching hook pin.
Boundary: a non-exact pin of a lock-tied package is refused rather than guessed.
Drift: the writer's package list, every lock-tied pin in the config, and the
Renovate skip rule name the same packages.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ci import check_python_dependency_locks as locks  # noqa: E402

CONFIG = ROOT / ".pre-commit-config.yaml"
LOCK = ROOT / "requirements" / "locks" / "tooling-tests.txt"
RENOVATE = ROOT / "renovate.json"


def package_key(name: str) -> str:
    """Normalise a distribution name to its PEP 503 key."""
    return re.sub(r"[-_.]+", "-", name).lower()


def config_pin_keys() -> set[str]:
    """Every package key with an exact pin in additional_dependencies."""
    text = CONFIG.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    keys: set[str] = set()
    for index, start, end in locks._dependency_value_spans(lines):
        for match in locks.HOOK_PIN_RE.finditer(lines[index][start:end]):
            keys.add(package_key(match.group(1)))
    return keys


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


class ToolingHookPinTest(unittest.TestCase):
    """The writer, the checked-in config and the Renovate rule agree."""

    def setUp(self) -> None:
        """Read the checked-in inputs once."""
        self.config = CONFIG.read_text(encoding="utf-8")
        self.lock = LOCK.read_text(encoding="utf-8")

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

    def test_a_non_exact_pin_is_refused(self) -> None:
        """A range is not silently converted to the lock version."""
        planted = self.config.replace('"bandit==1.9.4"', '"bandit>=1.9.4"', 1)
        with self.assertRaisesRegex(locks.ContractError, "not an exact '==' lock pin"):
            locks.sync_tooling_hook_pins(planted, self.lock)

    def test_a_change_outside_a_pin_token_is_refused(self) -> None:
        """A writer bug that changes anything but a version is refused."""
        original = locks._sync_hook_segment

        def drifting_rewrite(
            segment: str, lock_pins: dict[str, str], where: str, changes: list[str]
        ) -> str:
            updated = original(segment, lock_pins, where, changes)
            return updated.replace("bandit==1.9.5", "bandit==1.9.5  # drift", 1)

        planted = self.lock.replace("bandit==1.9.4", "bandit==1.9.5")
        with mock.patch.object(locks, "_sync_hook_segment", side_effect=drifting_rewrite):
            with self.assertRaisesRegex(locks.ContractError, "more than a pin version"):
                locks.sync_tooling_hook_pins(self.config, planted)

    def test_shared_package_list_matches_the_renovate_skip_rule(self) -> None:
        """The writer and the Renovate rule read one package list."""
        self.assertEqual(locks.TOOLING_HOOK_PACKAGES, renovate_skip_names())

    def test_config_pins_cover_exactly_the_shared_packages(self) -> None:
        """A new lock-tied pin needs a writer-list update, not silent drift."""
        lock_pins = locks.read_lock_pins(self.lock)
        self.assertEqual(config_pin_keys() & set(lock_pins), set(locks.TOOLING_HOOK_PACKAGES))


if __name__ == "__main__":
    unittest.main()
