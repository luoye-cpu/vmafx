#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""The `Verify All` job publishes `make verify-all` evidence at the PR head.

Issue #3645: the repository asks every turn to run `make verify-all`, but a
reviewer or bot cannot read a log written on the contributor's host. The job
runs the same command through the pinned engine launcher, classifies the diff
with `praetorctl ci filter`, and uploads the log and a sha256 receipt even when
the command fails.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "standards-gate.yml"
JOB_ID = "verify-all"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def job() -> dict[str, Any]:
    return load()["jobs"][JOB_ID]


def step(name: str) -> dict[str, Any]:
    return next(s for s in job()["steps"] if s.get("name") == name)


class VerifyAllPullRequestJob(unittest.TestCase):
    def test_it_runs_for_light_and_full_pull_requests(self) -> None:
        definition = job()
        self.assertEqual(definition["name"], "Verify All")
        self.assertEqual(definition["needs"], "tier")
        self.assertIn("needs.tier.outputs.light == 'true'", definition["if"])
        self.assertIn("needs.tier.outputs.full == 'true'", definition["if"])
        self.assertEqual(definition["timeout-minutes"], 30)

    def test_it_checks_out_history_for_the_merge_base_filter(self) -> None:
        checkout = job()["steps"][0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        self.assertFalse(checkout["with"]["persist-credentials"])

    def test_it_sets_up_go_and_node_before_verify_all(self) -> None:
        steps = job()["steps"]
        go = next(s for s in steps if s.get("name") == "Set up Go")
        node = next(s for s in steps if s.get("name") == "Set up Node")
        verify = next(
            s for s in steps if s.get("name") == "Run make verify-all and publish its log"
        )
        self.assertEqual(go["with"]["go-version-file"], "go.mod")
        self.assertEqual(node["with"]["node-version"], "24")
        self.assertEqual(
            node["with"]["cache-dependency-path"], "tools/markdownlint/package-lock.json"
        )
        self.assertLess(steps.index(go), steps.index(verify))
        self.assertLess(steps.index(node), steps.index(verify))

    def test_it_installs_the_engine_through_the_launcher(self) -> None:
        install = step("Install the pinned governance engine")
        self.assertEqual(install["run"], "sh .config/lefthook/engine.sh --print-path")
        rendered = yaml.safe_dump(job(), sort_keys=False)
        self.assertNotIn("go install", rendered)

    def test_it_classifies_the_diff_before_running_verify_all(self) -> None:
        filter_step = step("Classify the diff (HISS-18)")
        self.assertIn("sh .config/lefthook/engine.sh ci filter", filter_step["run"])
        self.assertIn("--env", filter_step["run"])
        self.assertEqual(filter_step["id"], "filter")
        self.assertIn("github.event.pull_request.base.sha", filter_step["env"]["BASE_SHA"])
        self.assertIn("github.event.merge_group.base_sha", filter_step["env"]["BASE_SHA"])
        self.assertIn("github.event.before", filter_step["env"]["BASE_SHA"])
        self.assertIn("github.event.pull_request.head.sha", filter_step["env"]["HEAD_SHA"])
        self.assertIn("github.event.merge_group.head_sha", filter_step["env"]["HEAD_SHA"])

        verify = step("Run make verify-all and publish its log")
        self.assertEqual(verify["if"], "steps.filter.outputs.run_audit == 'true'")
        skip = step("No governance gate owed — verify-all skipped")
        self.assertEqual(skip["if"], "steps.filter.outputs.run_audit != 'true'")
        self.assertIn("only private session state changed", skip["run"])

    def test_it_runs_the_local_gate_and_keeps_its_log(self) -> None:
        verify = step("Run make verify-all and publish its log")
        run = verify["run"]
        self.assertIn("set -o pipefail", run)
        self.assertIn('make verify-all 2>&1 | tee "$log"', run)
        self.assertIn("status=${PIPESTATUS[0]}", run)
        self.assertIn("printf 'log_sha256=%s", run)
        self.assertIn("printf 'exit_code=%s", run)
        self.assertIn('exit "$status"', run)
        self.assertEqual(verify["shell"], "bash")

    def test_it_uploads_the_log_and_receipt_even_on_failure(self) -> None:
        uploads = [s for s in job()["steps"] if "upload-artifact" in s.get("uses", "")]
        self.assertEqual(len(uploads), 2)
        for upload in uploads:
            self.assertIn("always()", upload["if"])
            self.assertEqual(upload["with"]["name"], "verify-all")
            self.assertEqual(upload["with"]["if-no-files-found"], "ignore")
            paths = upload["with"]["path"]
            self.assertIn("verify-all/verify-all.log", paths)
            self.assertIn("verify-all/verify-all-receipt.txt", paths)

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        for step_definition in job()["steps"]:
            uses = step_definition.get("uses")
            if uses:
                self.assertRegex(uses, SHA_PIN, uses)


if __name__ == "__main__":
    unittest.main()
