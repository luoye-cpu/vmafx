#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fixture coverage for the master-push and merge-group render checks.


Every case builds a disposable repository whose stub ``Makefile`` renders
``CHANGELOG.md`` from ``changelog.d/`` and checks it, commits with fixed
committer times and runs ``scripts/ci/check_render_on_master.py`` with a fixed
``--now``. A current render passes; an owed render passes inside the bound with
a warning and fails past it; a last render that is not the render of its
fragments fails at any age.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # hook environments omit PyYAML stubs
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.lib import git_env

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ci import ci_expressions as gx  # noqa: E402

CHECK = Path(
    os.environ.get("CHECK_RENDER_SCRIPT") or ROOT / "scripts" / "ci" / "check_render_on_master.py"
)
WORKFLOW = ROOT / ".github" / "workflows" / "lint-and-format.yml"
RENDER_PR_WORKFLOW = ROOT / ".github" / "workflows" / "docs-render-pr.yml"
RENDER_PR_SCRIPT = ROOT / "scripts" / "ci" / "open-render-pr.sh"
GIT = shutil.which("git") or "/usr/bin/git"
GIT_TIMEOUT_S = 60
SCRIPT_TIMEOUT_S = 60
NOW = 1_800_000_000
HOUR = 3600
IDENT = {
    "GIT_AUTHOR_NAME": "Ada",
    "GIT_AUTHOR_EMAIL": "ada@example.org",
    "GIT_COMMITTER_NAME": "Ada",
    "GIT_COMMITTER_EMAIL": "ada@example.org",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
MAKEFILE = (
    ".PHONY: docs-render docs-render-check\n"
    "docs-render:\n"
    "\t@cat changelog.d/*/*.md > CHANGELOG.md\n"
    "docs-render-check:\n"
    "\t@cat changelog.d/*/*.md | cmp -s - CHANGELOG.md"
    " || { echo 'CHANGELOG.md is stale'; exit 1; }\n"
)


class RenderOnMasterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "master")
        self.commit({"Makefile": MAKEFILE, "changelog.d/fixed/a.md": "- fixed a\n"}, age_h=100)
        self.commit({"CHANGELOG.md": "- fixed a\n"}, age_h=99)

    def git(self, *args: str, date: str | None = None) -> str:
        """Run fixture Git commands for scripts/ci/check_render_on_master.py.

        Constructs fixture commits with fixed commit timestamps.
        """
        env = git_env.drop()
        env.update(IDENT)
        if date is not None:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        done = subprocess.run(  # noqa: S603 -- fixed argv, no shell, fixture repo
            [GIT, *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_S,
        )
        return done.stdout

    def commit(self, files: dict[str, str], age_h: float) -> None:
        for rel, content in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change", date=f"@{int(NOW - age_h * HOUR)} +0000")

    def check(self, *extra: str) -> subprocess.CompletedProcess[str]:
        """Run scripts/ci/check_render_on_master.py at a simulated wall-clock time.

        ADR-3295: runs the master render check at a simulated wall-clock time.
        """
        env = git_env.drop()
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell, check under test
            [sys.executable, str(CHECK), "--root", str(self.repo), "--now", str(NOW), *extra],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=SCRIPT_TIMEOUT_S,
        )

    def assert_status(
        self, res: subprocess.CompletedProcess[str], status: int, needle: str
    ) -> None:
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, status, out)
        self.assertIn(needle, out)

    # --- positive -------------------------------------------------------

    def test_current_render_passes(self) -> None:
        self.assert_status(self.check(), 0, "the render at HEAD is current")

    def test_owed_render_inside_the_bound_warns(self) -> None:
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)
        self.commit({"changelog.d/fixed/c.md": "- fixed c\n"}, age_h=0.5)
        self.assert_status(self.check(), 0, "::warning title=ADR-3295 rendered docs::render owed")

    def test_render_after_the_window_is_current_again(self) -> None:
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=30)
        self.commit({"CHANGELOG.md": "- fixed a\n- fixed b\n"}, age_h=29)
        self.assert_status(self.check(), 0, "the render at HEAD is current")

    def test_no_worktree_is_left_behind(self) -> None:
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)
        self.check()
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)

    # --- planted negatives ----------------------------------------------

    def test_owed_render_past_the_bound_fails(self) -> None:
        # The owed time starts at the first landing after the render, not the last.
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=25)
        self.commit({"changelog.d/fixed/c.md": "- fixed c\n"}, age_h=1)
        self.assert_status(self.check(), 1, "::error title=ADR-3295 rendered docs::render owed")

    def test_hand_edited_last_render_fails_inside_the_bound(self) -> None:
        self.commit({"CHANGELOG.md": "- fixed a\n- an edit no fragment carries\n"}, age_h=2)
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)
        self.assert_status(self.check(), 1, "is not the render of its fragments")

    def test_hand_edit_at_head_fails(self) -> None:
        self.commit({"CHANGELOG.md": "- fixed a\n- an edit no fragment carries\n"}, age_h=1)
        self.assert_status(self.check(), 1, "is not the render of its fragments")

    def test_no_render_commit_fails(self) -> None:
        shutil.rmtree(self.repo / ".git")
        (self.repo / "CHANGELOG.md").unlink()
        self.git("init", "-q", "-b", "master")
        self.commit({"Makefile": MAKEFILE, "changelog.d/fixed/a.md": "- fixed a\n"}, age_h=1)
        self.assert_status(self.check(), 1, "no commit of HEAD carries a render")

    def test_stale_merge_group_render_fails(self) -> None:
        """Reject a render computed before an earlier group commit added a fragment."""
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=3)
        base = self.git("rev-parse", "HEAD").strip()
        stale_render = "- fixed a\n- fixed b\n"
        self.commit({"changelog.d/fixed/c.md": "- fixed c\n"}, age_h=2)
        self.commit({"CHANGELOG.md": stale_render}, age_h=1)
        render = self.git("rev-parse", "HEAD").strip()

        res = self.check("--group-base", base)
        self.assert_status(res, 1, f"merge-group render commit {render[:12]} is not the render")
        self.assertIn("Re-render after the group's preceding changes land", res.stdout)

    def test_exact_merge_group_render_passes_with_later_fragment(self) -> None:
        """Check only the latest render, even when a later fragment is long owed."""
        base = self.git("rev-parse", "HEAD").strip()
        self.commit({"CHANGELOG.md": "- fixed a\n- hand edit\n"}, age_h=40)
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=38)
        self.commit({"CHANGELOG.md": "- fixed a\n- fixed b\n"}, age_h=35)
        render = self.git("rev-parse", "HEAD").strip()
        self.commit({"changelog.d/fixed/c.md": "- fixed c\n"}, age_h=30)

        self.assert_status(self.check("--group-base", base), 0, f"render {render[:12]} is exact")

    def test_merge_group_without_a_render_passes(self) -> None:
        """Skip a fragment-only group even if its base already has an inexact render."""
        self.commit({"CHANGELOG.md": "- fixed a\n- hand edit\n"}, age_h=2)
        base = self.git("rev-parse", "HEAD").strip()
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)

        self.assert_status(
            self.check("--group-base", base), 0, "no render commit in the merge group"
        )

    def test_merge_group_render_at_head_passes(self) -> None:
        """Allow an exact render as the final commit of a group."""
        base = self.git("rev-parse", "HEAD").strip()
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=2)
        self.commit({"CHANGELOG.md": "- fixed a\n- fixed b\n"}, age_h=1)
        render = self.git("rev-parse", "HEAD").strip()

        self.assert_status(self.check("--group-base", base), 0, f"render {render[:12]} is exact")

    def test_merge_group_without_a_base_fails(self) -> None:
        """Reject an empty CI base, which Git would read as HEAD."""
        self.assert_status(self.check("--group-base", ""), 1, "missing merge-group base SHA")

    def test_merge_group_base_not_a_commit_id_fails(self) -> None:
        """Reject non-IDs, option-like values, uppercase hex and abbreviated SHAs cleanly."""
        head = self.git("rev-parse", "HEAD").strip()
        for invalid in ("not-a-sha", "--output=x", head[:12], head.upper()):
            with self.subTest(invalid=invalid):
                res = self.check("--group-base", invalid)
                self.assert_status(res, 1, f"merge-group base {invalid!r} is not a full commit ID")
                self.assertEqual(res.stderr, "")
                self.assertNotIn("Traceback", res.stdout + res.stderr)

    def test_merge_group_base_not_an_ancestor_fails(self) -> None:
        """Reject a base commit on a diverged branch."""
        self.git("checkout", "-q", "-b", "side")
        self.commit({"changelog.d/fixed/side.md": "- side\n"}, age_h=2)
        side = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "master")
        self.commit({"changelog.d/fixed/main.md": "- main\n"}, age_h=1)

        self.assert_status(
            self.check("--group-base", side),
            1,
            f"merge-group base {side[:12]} is not an ancestor of HEAD",
        )

    def test_merge_group_base_descendant_fails(self) -> None:
        """Reject a base commit that descends from HEAD instead of passing an empty range."""
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)
        descendant = self.git("rev-parse", "HEAD").strip()
        self.git("reset", "--hard", "-q", "HEAD~1")

        self.assert_status(
            self.check("--group-base", descendant),
            1,
            f"merge-group base {descendant[:12]} is not an ancestor of HEAD",
        )

    def test_merge_group_base_equal_to_head_passes(self) -> None:
        """Allow an empty merge group where the base commit equals HEAD."""
        head = self.git("rev-parse", "HEAD").strip()
        self.assert_status(
            self.check("--group-base", head), 0, "no render commit in the merge group"
        )

    def test_merge_group_unknown_base_commit_fails(self) -> None:
        """Report git's status and stderr line when a full commit ID is missing from the repo."""
        unknown = "0" * 40
        res = self.check("--group-base", unknown)
        self.assert_status(
            res, 1, "::error title=ADR-3295 rendered docs::git merge-base --is-ancestor failed"
        )
        self.assertIn("(rc 128)", res.stdout)
        self.assertIn(unknown[:12], res.stdout)
        self.assertEqual(res.stderr, "")

    # --- the scheduled render pull request (ADR-3373) --------------------

    def decide(self) -> dict[str, str]:
        res = self.check("--open-after-hours", "6")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return dict(line.split("=", 1) for line in res.stdout.splitlines())

    def test_current_render_opens_nothing(self) -> None:
        self.assertEqual(self.decide(), {"open": "false", "owed_since": ""})

    def test_render_owed_briefly_opens_nothing_yet(self) -> None:
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=2)
        verdict = self.decide()
        self.assertEqual(verdict["open"], "false")
        self.assertNotEqual(verdict["owed_since"], "")

    def test_inexact_last_render_opens_the_pull_request_immediately(self) -> None:
        """Open a repair even though the first commit after the render is only an hour old."""
        self.commit({"CHANGELOG.md": "- fixed a\n- hand edit\n"}, age_h=2)
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=1)
        first = self.git("rev-parse", "HEAD").strip()

        self.assertEqual(self.decide(), {"open": "true", "owed_since": first, "owed_hours": "1.0"})

    def test_render_owed_past_the_threshold_opens_the_pull_request(self) -> None:
        # Planted case: no render ran for 7 hours. The decision opens the render
        # pull request 18 hours before the master-push check turns red.
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=7)
        first = self.git("rev-parse", "HEAD").strip()
        self.commit({"changelog.d/fixed/c.md": "- fixed c\n"}, age_h=1)
        verdict = self.decide()
        self.assertEqual(verdict["open"], "true")
        self.assertEqual(verdict["owed_since"], first)
        self.assertEqual(verdict["owed_hours"], "7.0")

    def test_render_owed_past_the_bound_opens_the_pull_request(self) -> None:
        self.commit({"changelog.d/fixed/b.md": "- fixed b\n"}, age_h=30)
        self.assertEqual(self.decide()["open"], "true")


class RenderOnMasterWiringTest(unittest.TestCase):
    def test_master_push_step_runs_the_check(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn("python3 scripts/ci/check_render_on_master.py", text)
        self.assertNotIn("run: make docs-render-check\n", text)

    def test_merge_group_step_runs_the_check_with_the_base_from_env(self) -> None:
        """Pass the event base through the environment in the merge-group Docs step."""
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        triggers = data.get("on", data.get(True))
        self.assertIn("checks_requested", triggers["merge_group"]["types"])
        steps = data["jobs"]["docs-lint"]["steps"]
        checks = [step for step in steps if "--group-base" in step.get("run", "")]
        self.assertEqual(len(checks), 1, "Docs must run the merge-group render check")
        step = checks[0]
        self.assertEqual(step["if"], "github.event_name == 'merge_group'")
        self.assertEqual(
            step["env"]["MERGE_GROUP_BASE_SHA"], "${{ github.event.merge_group.base_sha }}"
        )
        self.assertEqual(
            step["run"],
            'python3 scripts/ci/check_render_on_master.py --group-base "$MERGE_GROUP_BASE_SHA"',
        )

    def test_merge_group_checkout_fetches_full_history(self) -> None:
        """Keep the group base and fragment landing history available to the check."""
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = data["jobs"]["docs-lint"]["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        depth = checkout["with"]["fetch-depth"]
        self.assertEqual(gx.evaluate(depth, {"github": {"event_name": "merge_group"}}), 0)

    def test_render_pull_request_runs_on_a_schedule(self) -> None:
        # Every render before ADR-3373 was opened by hand; without a scheduled
        # opener, master goes red 24 hours after a landing nobody rendered.
        text = RENDER_PR_WORKFLOW.read_text()
        self.assertRegex(text, r"(?m)^  schedule:\n    - cron: ")
        self.assertIn("check_render_on_master.py --open-after-hours", text)
        hours = re.search(r'OPEN_AFTER_HOURS: "(\d+)"', text)
        if hours is None:
            self.fail("docs-render-pr.yml sets no OPEN_AFTER_HOURS")
        # Leave room inside the 24-hour bound for checks and auto-merge.
        self.assertLessEqual(int(hours.group(1)), 12)
        # A pull request opened with GITHUB_TOKEN gets no check runs (ADR-1151).
        self.assertIn("actions/create-github-app-token@", text)
        self.assertIn("run: bash scripts/ci/open-render-pr.sh", text)

    def test_render_pull_request_runs_on_master_push(self) -> None:
        # Issue 3462 (ADR-3552): scheduled runs are dropped, so a push to master
        # must start the workflow; schedule stays as the hourly fallback.
        data = yaml.safe_load(RENDER_PR_WORKFLOW.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        triggers = data.get("on", data.get(True))
        self.assertIsInstance(triggers, dict)
        self.assertIn("push", triggers)
        push = triggers["push"]
        self.assertIsInstance(push, dict)
        # Boundary: push runs on master and names no other branch (a render on
        # any other branch's push would render a non-master tip).
        self.assertEqual(push.get("branches"), ["master"])
        self.assertEqual(set(push.get("branches", [])), {"master"})
        for forbidden in ("paths", "paths-ignore", "tags", "branches-ignore"):
            self.assertNotIn(forbidden, push)
        self.assertEqual(set(push.keys()), {"branches"})
        self.assertIn("schedule", triggers)
        schedule = triggers["schedule"]
        self.assertIsInstance(schedule, list)
        self.assertEqual(len(schedule), 1)
        self.assertIsInstance(schedule[0], dict)
        self.assertIn("cron", schedule[0])
        self.assertIn("workflow_dispatch", triggers)

    def test_render_pull_request_carries_rendered_paths_only(self) -> None:
        script = RENDER_PR_SCRIPT.read_text()
        self.assertIn("make -s docs-render", script)
        self.assertIn("gh pr merge", script)
        self.assertIn("--auto", script)
        self.assertIn("Signed-off-by:", script)
        self.assertIn("wrote outside the rendered paths", script)


if __name__ == "__main__":
    unittest.main()
