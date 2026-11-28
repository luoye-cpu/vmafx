#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Tests for the scheduled render pull request opener (ADR-3373).

Verifies author and cross-repository filtering for open PRs, auto-merge arming
on parent==base early exit, queued render refresh and fail-closed queue reads,
detection of untracked files in rendered paths, and required environment
validation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "open-render-pr.sh"
GIT = shutil.which("git") or "/usr/bin/git"
BASH = shutil.which("bash") or "/bin/bash"
GIT_TIMEOUT_S = 60
SCRIPT_TIMEOUT_S = 60

MIN_CALL_LEN = 2
TEST_TOKEN = "test-token"  # noqa: S105 -- dummy token for fixture

GH_STUB = r"""#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

state = Path(os.environ["STUB_STATE_DIR"])
log = state / "gh.log"
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")

argv = sys.argv[1:]
if not argv:
    sys.exit(0)

def eval_jq(data, expr):
    res = subprocess.run(
        ["jq", "-r", expr],
        input=json.dumps(data),
        text=True,
        capture_output=True,
        check=True,
    )
    sys.stdout.write(res.stdout)

cmd = argv[0]
if cmd == "pr":
    sub = argv[1] if len(argv) > 1 else ""
    if sub == "list":
        prs_path = state / "prs.json"
        data = json.loads(prs_path.read_text(encoding="utf-8")) if prs_path.exists() else []
        if "--jq" in argv:
            eval_jq(data, argv[argv.index("--jq") + 1])
        else:
            print(json.dumps(data))
    elif sub == "create":
        after_path = state / "prs_after_create.json"
        if after_path.exists():
            (state / "prs.json").write_text(after_path.read_text(encoding="utf-8"), encoding="utf-8")
    elif sub == "merge":
        if "--body-file" in argv:
            bf = Path(argv[argv.index("--body-file") + 1])
            if bf.exists():
                (state / "merge_body.txt").write_text(
                    bf.read_text(encoding="utf-8"), encoding="utf-8"
                )
    sys.exit(0)

if cmd == "api":
    endpoint = argv[1] if len(argv) > 1 else ""
    if endpoint == "graphql":
        gql_path = state / "graphql.json"
        cfg = json.loads(gql_path.read_text(encoding="utf-8")) if gql_path.exists() else {}
        if any("dequeuePullRequest" in arg for arg in argv):
            sys.exit(int(cfg.get("dequeue_returncode", 0)))
        answers = cfg.get("answers", [cfg.get("isInMergeQueue", False)])
        answer = answers[0]
        if len(answers) > 1:
            cfg["answers"] = answers[1:]
            gql_path.write_text(json.dumps(cfg), encoding="utf-8")
        rc = int(cfg.get("returncode", 0))
        if rc != 0:
            sys.exit(rc)
        data = {
            "data": {
                "repository": {
                    "pullRequest": {"isInMergeQueue": answer, "id": cfg.get("id", "PR_node42")}
                }
            }
        }
        if "--jq" in argv:
            eval_jq(data, argv[argv.index("--jq") + 1])
        else:
            print(json.dumps(data))
        sys.exit(0)
    if "branches" in endpoint:
        branch_path = state / "branch.json"
        data = (
            json.loads(branch_path.read_text(encoding="utf-8"))
            if branch_path.exists()
            else {"commit": {"parents": [{"sha": "none"}]}}
        )
        if "--jq" in argv:
            eval_jq(data, argv[argv.index("--jq") + 1])
        else:
            print(json.dumps(data))
    elif "users" in endpoint:
        data = {"id": 12345678}
        if "--jq" in argv:
            eval_jq(data, argv[argv.index("--jq") + 1])
        else:
            print(json.dumps(data))
    sys.exit(0)

sys.exit(0)
"""

MAKE_STUB = r"""#!/usr/bin/env python3
import json
import os
import runpy
import sys
from pathlib import Path

state = Path(os.environ["STUB_STATE_DIR"])
log = state / "make.log"
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")

action = state / "make_action.py"
if action.exists() and "docs-render" in sys.argv:
    runpy.run_path(str(action))

sys.exit(0)
"""


RENDERED_FILES = (
    "CHANGELOG.md",
    "docs/adr/README.md",
    "docs/adr/titles.md",
    "docs/research/titles.md",
    "docs/rebase-notes.md",
)


def fixture_env() -> dict[str, str]:
    """Environment for fixture git calls, without the caller's GIT_* variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    return env


def git(*args: str) -> str:
    """Execute a git command within the fixture environment and return standard output."""
    return subprocess.run(  # noqa: S603 -- fixed argv, fixture repo
        [GIT, *args],
        check=True,
        env=fixture_env(),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    ).stdout


class OpenRenderPrTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.runner_temp = self.root / "runner_temp"
        self.runner_temp.mkdir()
        self.app_slug = "vmafx-release-bot"
        self.token = TEST_TOKEN
        self.github_repository = "vidiko/vmafx"
        self.render_branch = "render-docs"
        self.push_url = (
            f"https://x-access-token:{self.token}@github.com/{self.github_repository}.git"
        )
        self._write_stubs()
        self._init_repos()

    def _write_stubs(self) -> None:
        for name, text in (("gh", GH_STUB), ("make", MAKE_STUB)):
            stub = self.bin_dir / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)

    def _init_repos(self) -> None:
        self.bare_repo = self.root / "bare.git"
        git("init", "--bare", "-q", str(self.bare_repo))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "master", str(self.repo))
        for rel in RENDERED_FILES:
            file_path = self.repo / rel
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("initial\n", encoding="utf-8")
        (self.repo / "docs/adr/by-tag").mkdir(parents=True, exist_ok=True)
        git("-C", str(self.repo), "add", "-A")
        git("-C", str(self.repo), "commit", "-q", "-m", "initial")
        git("-C", str(self.repo), "config", f"url.{self.bare_repo}.insteadOf", self.push_url)

    def gh_calls(self) -> list[list[str]]:
        log = self.state_dir / "gh.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def make_calls(self) -> list[list[str]]:
        log = self.state_dir / "make.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def graphql_calls(self) -> list[list[str]]:
        return [
            c
            for c in self.gh_calls()
            if len(c) >= MIN_CALL_LEN and c[0] == "api" and c[1] == "graphql"
        ]

    def own_pr_record(self, number: int = 42) -> dict[str, object]:
        return {
            "number": number,
            "author": {"login": f"{self.app_slug}[bot]"},
            "isCrossRepository": False,
        }

    def set_prs(self, prs: list[dict[str, object]]) -> None:
        (self.state_dir / "prs.json").write_text(json.dumps(prs), encoding="utf-8")

    def set_merge_queue(
        self, *, queued: bool = False, returncode: int = 0, **config: object
    ) -> None:
        (self.state_dir / "graphql.json").write_text(
            json.dumps({"isInMergeQueue": queued, "returncode": returncode, **config}),
            encoding="utf-8",
        )

    def stage_own_open_pr_with_render_diff(
        self, number: int = 42, parent_sha: str = "different-sha"
    ) -> None:
        self.set_prs([self.own_pr_record(number)])
        (self.state_dir / "branch.json").write_text(
            json.dumps({"commit": {"parents": [{"sha": parent_sha}]}}),
            encoding="utf-8",
        )
        (self.state_dir / "make_action.py").write_text(
            "from pathlib import Path\n"
            "Path('CHANGELOG.md').write_text('rendered\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

    def assert_existing_pr_rendered_and_pushed(
        self, res: subprocess.CompletedProcess[str], number: int = 42
    ) -> None:
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("auto-merge armed", res.stdout)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 2)
        self.assertIn("isInMergeQueue", " ".join(gql[0]))
        self.assertIn("isInMergeQueue", " ".join(gql[1]))
        self.assertTrue(any("docs-render" in c for c in self.make_calls()))
        bare_branches = git("-C", str(self.bare_repo), "branch")
        self.assertIn(self.render_branch, bare_branches)
        calls = self.gh_calls()
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [])
        merge_calls = [
            c for c in calls if c[:2] == ["pr", "merge"] and "--auto" in c and str(number) in c
        ]
        self.assertTrue(merge_calls, f"gh pr merge --auto was not called: {calls}")

    def run_script(
        self,
        render_due: bool = False,
        extra_env: dict[str, str] | None = None,
        remove_env: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Execute the open-render-pr script with test harness environment and stubs."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
            PATH=f"{self.bin_dir}:{env.get('PATH', '')}",
            STUB_STATE_DIR=str(self.state_dir),
            GH_TOKEN=self.token,
            APP_SLUG=self.app_slug,
            GITHUB_REPOSITORY=self.github_repository,
            RENDER_BRANCH=self.render_branch,
            RENDER_DUE="true" if render_due else "false",
            RUNNER_TEMP=str(self.runner_temp),
            OWED_SINCE="2026-10-25T00:00:00Z",
            RENDER_DEQUEUE_POLLS="12",
            RENDER_DEQUEUE_SLEEP_S="0",
            GIT_AUTHOR_NAME="Test User",
            GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test User",
            GIT_COMMITTER_EMAIL="test@example.com",
        )
        if extra_env:
            env.update(extra_env)
        for key in remove_env:
            env.pop(key, None)

        script_path = Path(os.environ.get("OPEN_RENDER_PR_SCRIPT", SCRIPT))
        return subprocess.run(  # noqa: S603 -- fixed argv, fixture repo
            [BASH, str(script_path)],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=SCRIPT_TIMEOUT_S,
        )

    def test_case_a_foreign_pr_on_render_branch_not_closed_when_render_not_due(self) -> None:
        # Case a: foreign PR (author other login) on RENDER_BRANCH with RENDER_DUE=false -> no gh pr close call.
        # Unfixed failure: gh pr list has no author filter, so open_pr takes the foreign PR number (99)
        # and executes 'gh pr close --delete-branch 99', closing an unrelated pull request.
        self.set_prs([{"number": 99, "author": {"login": "attacker"}, "isCrossRepository": False}])
        res = self.run_script(render_due=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        calls = self.gh_calls()
        close_calls = [
            c for c in calls if len(c) >= MIN_CALL_LEN and c[0] == "pr" and c[1] == "close"
        ]
        self.assertEqual(close_calls, [], f"foreign PR was closed: {close_calls}")

    def test_case_b_cross_repo_pr_by_bot_not_closed_nor_merged(self) -> None:
        # Case b: cross-repo PR by the bot -> not closed, not merged.
        # Unfixed failure: gh pr list does not filter by isCrossRepository == false.
        # With RENDER_DUE=false, the unfixed script calls 'gh pr close 99' on the fork PR.
        # With RENDER_DUE=true and parent != base, the unfixed script executes 'gh pr merge 99' on the fork PR.
        self.set_prs(
            [
                {
                    "number": 99,
                    "author": {"login": f"{self.app_slug}[bot]"},
                    "isCrossRepository": True,
                }
            ]
        )

        # Sub-check 1: RENDER_DUE=false -> not closed
        res_close = self.run_script(render_due=False)
        self.assertEqual(res_close.returncode, 0, res_close.stdout + res_close.stderr)
        close_calls = [
            c
            for c in self.gh_calls()
            if len(c) >= MIN_CALL_LEN and c[0] == "pr" and c[1] == "close"
        ]
        self.assertEqual(close_calls, [], f"cross-repo PR was closed: {close_calls}")

        # Sub-check 2: RENDER_DUE=true -> not merged
        (self.state_dir / "gh.log").unlink(missing_ok=True)
        # parent commit differs from base
        (self.state_dir / "branch.json").write_text(
            json.dumps({"commit": {"parents": [{"sha": "different-sha"}]}}),
            encoding="utf-8",
        )
        res_merge = self.run_script(render_due=True)
        self.assertEqual(res_merge.returncode, 0, res_merge.stdout + res_merge.stderr)
        merge_calls = [
            c
            for c in self.gh_calls()
            if len(c) >= MIN_CALL_LEN and c[0] == "pr" and c[1] == "merge" and "99" in c
        ]
        self.assertEqual(merge_calls, [], f"cross-repo PR was merged: {merge_calls}")

    def test_case_c_own_pr_parent_equals_base_arms_automerge_without_push(self) -> None:
        # Case c: own PR, parent==base -> gh pr merge --auto called, no push.
        # Unfixed failure: early exit when parent == base skips 'gh pr merge --auto', exiting 0
        # without arming auto-merge.
        head_sha = git("-C", str(self.repo), "rev-parse", "HEAD").strip()
        (self.state_dir / "branch.json").write_text(
            json.dumps({"commit": {"parents": [{"sha": head_sha}]}}),
            encoding="utf-8",
        )
        self.set_prs(
            [
                {
                    "number": 42,
                    "author": {"login": f"{self.app_slug}[bot]"},
                    "isCrossRepository": False,
                }
            ]
        )

        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("already rendered", res.stdout)

        calls = self.gh_calls()
        merge_auto_calls = [
            c
            for c in calls
            if len(c) >= MIN_CALL_LEN
            and c[0] == "pr"
            and c[1] == "merge"
            and "--auto" in c
            and "42" in c
        ]
        self.assertTrue(merge_auto_calls, f"gh pr merge --auto was not called: {calls}")
        self.assertIn("--body-file", merge_auto_calls[0])

        body_log = self.state_dir / "merge_body.txt"
        if body_log.exists():
            body_content = body_log.read_text(encoding="utf-8")
        else:
            bf_idx = merge_auto_calls[0].index("--body-file")
            body_content = Path(merge_auto_calls[0][bf_idx + 1]).read_text(encoding="utf-8")

        self.assertIn(
            f"Signed-off-by: {self.app_slug}[bot] <12345678+{self.app_slug}[bot]@users.noreply.github.com>",
            body_content,
        )
        self.assertIn(
            f"Render-only commit (ADR-3295, ADR-3373): make docs-render at {head_sha[:12]}.",
            body_content,
        )
        self.assertNotIn("Co-authored-by", body_content)

        bare_branches = git("-C", str(self.bare_repo), "branch")
        self.assertNotIn(self.render_branch, bare_branches)

    def test_case_f_own_pr_in_merge_queue_skips_render_and_push(self) -> None:
        # Case f: own open PR in merge queue -> exit 0 with skip line, no render, push or merge.
        base = git("-C", str(self.repo), "rev-parse", "HEAD").strip()
        self.stage_own_open_pr_with_render_diff(parent_sha=base)
        self.set_merge_queue(queued=True)

        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(
            f"leaving render pull request #42 in the merge queue at {base[:12]}",
            res.stdout,
        )

        gql = self.graphql_calls()
        self.assertEqual(len(gql), 1, gql)
        self.assertIn("o=vidiko", gql[0])
        self.assertIn("n=vmafx", gql[0])
        self.assertIn("p=42", gql[0])

        calls = self.gh_calls()
        self.assertEqual([c for c in calls if c[:2] == ["pr", "merge"]], [])
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [])
        self.assertEqual(self.make_calls(), [])
        self.assertEqual((self.repo / "CHANGELOG.md").read_text(encoding="utf-8"), "initial\n")
        bare_branches = git("-C", str(self.bare_repo), "branch")
        self.assertNotIn(self.render_branch, bare_branches)

    def test_case_g_own_pr_not_in_merge_queue_renders_and_pushes(self) -> None:
        # Case g: own open PR, not in merge queue, parent != base -> renders, pushes, arms merge.
        self.stage_own_open_pr_with_render_diff(number=42)
        self.set_merge_queue(queued=False)

        res = self.run_script(render_due=True)
        self.assert_existing_pr_rendered_and_pushed(res, number=42)

    def assert_queue_read_stopped_render(self) -> None:
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("merge-queue GraphQL query failed for pull request #42", res.stderr)
        self.assertEqual(self.make_calls(), [])
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.gh_calls()))
        self.assertEqual(len(self.graphql_calls()), 1)

    def test_case_h_merge_queue_query_error_stops_render(self) -> None:
        self.stage_own_open_pr_with_render_diff()
        self.set_merge_queue(returncode=7)
        self.assert_queue_read_stopped_render()

    def test_case_h2_invalid_queue_answer_stops_render(self) -> None:
        self.stage_own_open_pr_with_render_diff()
        self.set_merge_queue(answers=[None])
        self.assert_queue_read_stopped_render()

    def test_case_h3_non_string_or_empty_queue_id_stops_render(self) -> None:
        bad_ids: tuple[object, ...] = (12345, True, {"x": 1}, "")
        for idx, bad_id in enumerate(bad_ids):
            with self.subTest(id=bad_id):
                self._fresh_case(f"bad-id-{idx}")
                self.stage_own_open_pr_with_render_diff()
                self.set_merge_queue(queued=False, id=bad_id)
                self.assert_queue_read_stopped_render()

    def test_case_h4_single_char_queue_id_renders_and_pushes(self) -> None:
        self.stage_own_open_pr_with_render_diff(number=42)
        self.set_merge_queue(queued=False, id="P")

        res = self.run_script(render_due=True)
        self.assert_existing_pr_rendered_and_pushed(res, number=42)

    def bare_head(self) -> str:
        return git("-C", str(self.bare_repo), "rev-parse", self.render_branch).strip()

    def stage_queued_render(self, *, with_tag: bool = False) -> str:
        repo = str(self.repo)
        parent = git("-C", repo, "rev-parse", "HEAD").strip()
        self.stage_own_open_pr_with_render_diff(parent_sha=parent)
        git("-C", repo, "switch", "-qc", self.render_branch)
        (self.repo / "CHANGELOG.md").write_text("rendered\n", encoding="utf-8")
        tag = "docs/adr/by-tag/new.md"
        if with_tag:
            (self.repo / tag).write_text("# New\n", encoding="utf-8")
        git("-C", repo, "add", ".")
        git("-C", repo, "commit", "-qm", "queued render")
        git("-C", repo, "push", "-q", str(self.bare_repo), self.render_branch)
        git("-C", repo, "switch", "-q", "master")
        (self.repo / "source.md").write_text("new master content\n", encoding="utf-8")
        git("-C", repo, "add", "source.md")
        git("-C", repo, "commit", "-qm", "advance master")
        with (self.state_dir / "make_action.py").open("a", encoding="utf-8") as action:
            action.write(f"p = Path({tag!r})\np.parent.mkdir(exist_ok=True)\n")
            action.write("p.write_text('# New\\n', encoding='utf-8')\n")
        return self.bare_head()

    def wrap_git_command(self, command: str, action: str) -> None:
        wrapper = self.bin_dir / "git"
        wrapper.write_text(
            f'#!/bin/sh\nset -eu\nif [ "$1" = {command} ]; then\n  {action}\nfi\n'
            f'exec "{GIT}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    def test_case_j_dequeues_before_pushing_new_tag(self) -> None:
        old_head = self.stage_queued_render()
        self.set_merge_queue(answers=[True, False, False], id="PR_stale42")
        self.wrap_git_command(
            "push", 'cp "$STUB_STATE_DIR/gh.log" "$STUB_STATE_DIR/before-push.log"'
        )
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotEqual(self.bare_head(), old_head)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 4)
        self.assertIn("isInMergeQueue", " ".join(gql[0]))
        self.assertIn("dequeuePullRequest(input:{id:$id})", " ".join(gql[1]))
        self.assertIn("id=PR_stale42", gql[1])
        self.assertIn("isInMergeQueue", " ".join(gql[2]))
        self.assertIn("isInMergeQueue", " ".join(gql[3]))
        before_push = (self.state_dir / "before-push.log").read_text(encoding="utf-8")
        self.assertEqual([c for c in map(json.loads, before_push.splitlines()) if c in gql], gql)
        self.assertEqual(
            git("-C", str(self.bare_repo), "show", f"{self.render_branch}:docs/adr/by-tag/new.md"),
            "# New\n",
        )
        self.assertTrue(
            any(c[:2] == ["pr", "merge"] and "--auto" in c and "42" in c for c in self.gh_calls())
        )

    def stage_stale_pr_with_current_master(self) -> str:
        old_head = self.stage_queued_render()
        (self.state_dir / "make_action.py").unlink()
        return old_head

    def assert_stale_pr_closed(self, old_head: str) -> None:
        calls = self.gh_calls()
        self.assertEqual([c[:2] for c in calls if c[0] == "pr"], [["pr", "list"], ["pr", "close"]])
        self.assertEqual(
            calls[-1][:6],
            ["pr", "close", "--repo", self.github_repository, "--delete-branch", "42"],
        )
        self.assertEqual(self.bare_head(), old_head)

    def test_case_j2_fresh_render_already_in_master(self) -> None:
        old_head = self.stage_stale_pr_with_current_master()
        self.set_merge_queue(answers=[True, False])
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 3)
        self.assertIn("dequeuePullRequest", " ".join(gql[1]))
        self.assertIn("isInMergeQueue", " ".join(gql[2]))
        self.assert_stale_pr_closed(old_head)

    def test_case_j3_unqueued_stale_render_closes(self) -> None:
        old_head = self.stage_stale_pr_with_current_master()
        self.set_merge_queue(queued=False)
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(self.graphql_calls()), 1)
        self.assert_stale_pr_closed(old_head)

    def test_case_j4_dequeue_failure_stops_close(self) -> None:
        old_head = self.stage_stale_pr_with_current_master()
        self.set_merge_queue(queued=True, dequeue_returncode=7)
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("dequeue failed for pull request #42", res.stderr)
        self.assertFalse(any(c[:2] == ["pr", "close"] for c in self.gh_calls()))
        self.assertEqual(self.bare_head(), old_head)

    def test_case_k_matching_render_stays_queued(self) -> None:
        old_head = self.stage_queued_render(with_tag=True)
        self.set_merge_queue(queued=True)
        base = git("-C", str(self.repo), "rev-parse", "HEAD").strip()
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(f"queued render pull request #42 matches master at {base[:12]}", res.stdout)
        self.assertEqual(self.make_calls(), [["-s", "docs-render"]])
        self.assertEqual(len(self.graphql_calls()), 1)
        self.assertEqual(self.bare_head(), old_head)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.gh_calls()))

    def test_case_k2_comparison_error_stops_dequeue(self) -> None:
        old_head = self.stage_queued_render()
        self.set_merge_queue(queued=True)
        self.wrap_git_command("diff", "exit 128")
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("could not compare queued render for pull request #42", res.stderr)
        self.assertEqual(len(self.graphql_calls()), 1)
        self.assertEqual(self.bare_head(), old_head)

    def assert_dequeue_stopped_push(self, message: str, polls: int) -> None:
        old_head = self.stage_queued_render()
        res = self.run_script(render_due=True, extra_env={"RENDER_DEQUEUE_POLLS": "2"})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(message, res.stderr)
        calls = [" ".join(c) for c in self.graphql_calls()]
        self.assertEqual(sum("dequeuePullRequest" in c for c in calls), 1)
        self.assertEqual(sum("isInMergeQueue" in c for c in calls), 1 + polls)
        self.assertEqual(self.bare_head(), old_head)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.gh_calls()))

    def test_case_l_dequeue_failure_stops_push(self) -> None:
        self.set_merge_queue(queued=True, dequeue_returncode=7)
        self.assert_dequeue_stopped_push("dequeue failed for pull request #42", polls=0)

    def test_case_m_poll_bound_stops_push(self) -> None:
        self.set_merge_queue(queued=True)
        self.assert_dequeue_stopped_push("pull request #42 still queued after 2 polls", polls=2)

    def test_case_m2_invalid_poll_stops_push(self) -> None:
        self.set_merge_queue(answers=[True, None])
        self.assert_dequeue_stopped_push(
            "merge-queue GraphQL query failed for pull request #42", polls=1
        )

    def test_case_m3_invalid_poll_bound_stops_dequeue(self) -> None:
        old_head = self.stage_queued_render()
        self.set_merge_queue(queued=True)
        res = self.run_script(render_due=True, extra_env={"RENDER_DEQUEUE_POLLS": "invalid"})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("invalid RENDER_DEQUEUE_POLLS for pull request #42", res.stderr)
        self.assertEqual(len(self.graphql_calls()), 1)
        self.assertEqual(self.bare_head(), old_head)

    def test_case_i_no_open_pr_skips_merge_queue_graphql_query(self) -> None:
        # Case i: no open PR -> GraphQL merge-queue query is never called.
        self.set_prs([])
        (self.state_dir / "prs_after_create.json").write_text(
            json.dumps([self.own_pr_record(42)]),
            encoding="utf-8",
        )
        (self.state_dir / "make_action.py").write_text(
            "from pathlib import Path\n"
            "Path('CHANGELOG.md').write_text('rendered\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.set_merge_queue(queued=True)

        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.graphql_calls(), [], f"unexpected graphql call: {self.gh_calls()}")
        bare_branches = git("-C", str(self.bare_repo), "branch")
        self.assertIn(self.render_branch, bare_branches)

    def test_case_d_untracked_adr_tag_file_triggers_commit_and_push(self) -> None:
        # Case d: make docs-render creates only an untracked docs/adr/by-tag/x.md -> commit + push path taken.
        # Unfixed failure: 'git diff --quiet -- ...' ignores untracked files, so the unfixed script
        # prints 'make docs-render changed nothing' and exits 0 without committing or pushing.
        self.set_prs([])
        (self.state_dir / "prs_after_create.json").write_text(
            json.dumps(
                [
                    {
                        "number": 42,
                        "author": {"login": f"{self.app_slug}[bot]"},
                        "isCrossRepository": False,
                    }
                ]
            ),
            encoding="utf-8",
        )
        (self.state_dir / "make_action.py").write_text(
            "from pathlib import Path\n"
            "p = Path('docs/adr/by-tag/x.md')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('# Tag x\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("auto-merge armed", res.stdout)

        bare_branches = git("-C", str(self.bare_repo), "branch")
        self.assertIn(self.render_branch, bare_branches)

    def test_case_e_runner_temp_unset_exits_nonzero_before_gh_calls(self) -> None:
        # Case e: RUNNER_TEMP unset -> script exits non-zero before any gh call.
        # Unfixed failure: RUNNER_TEMP is omitted from the initial ': "${...:?}"' check,
        # so the unfixed script executes 'gh pr list' before failing later.
        self.set_prs([])
        res = self.run_script(render_due=False, remove_env=("RUNNER_TEMP",))
        self.assertNotEqual(res.returncode, 0, "script should fail when RUNNER_TEMP is unset")
        calls = self.gh_calls()
        self.assertEqual(calls, [], f"gh was called despite missing RUNNER_TEMP: {calls}")

    def _fresh_case(self, label: str) -> None:
        self.root = Path(self._tmp.name) / label
        self.root.mkdir()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.runner_temp = self.root / "runner_temp"
        self.runner_temp.mkdir()
        self._init_repos()

    def _assert_bad_dequeue_env(self, var: str, value: str, label: str) -> None:
        self._fresh_case(label)
        old_head = self.stage_queued_render()
        self.set_merge_queue(answers=[True, False, False], id="PR_stale42")
        res = self.run_script(render_due=True, extra_env={var: value})
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(var, res.stderr)
        self.assertIn("#42", res.stderr)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 1)
        self.assertFalse(any("dequeuePullRequest" in " ".join(c) for c in gql))
        self.assertEqual(self.bare_head(), old_head)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.gh_calls()))

    def test_case_n_no_open_pr_and_no_render_diff_exits_without_push(self) -> None:
        base = git("-C", str(self.repo), "rev-parse", "HEAD").strip()
        self.set_prs([])
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(f"make docs-render changed nothing at {base[:12]}", res.stdout)
        self.assertEqual(self.make_calls(), [["-s", "docs-render"]])
        self.assertEqual(self.graphql_calls(), [])
        calls = self.gh_calls()
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [])
        self.assertEqual([c for c in calls if c[:2] == ["pr", "merge"]], [])
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))

    def test_case_o_own_pr_not_due_unqueued_closes_without_dequeue(self) -> None:
        self.set_prs([self.own_pr_record(42)])
        self.set_merge_queue(queued=False)
        res = self.run_script(render_due=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("closed render pull request #42", res.stdout)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 1)
        self.assertIn("isInMergeQueue", " ".join(gql[0]))
        self.assertNotIn("dequeuePullRequest", " ".join(gql[0]))
        calls = self.gh_calls()
        self.assertEqual([c[:2] for c in calls if c[0] == "pr"], [["pr", "list"], ["pr", "close"]])
        self.assertEqual(
            calls[-1][:6],
            ["pr", "close", "--repo", self.github_repository, "--delete-branch", "42"],
        )
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))

    def test_case_o2_own_pr_not_due_queued_dequeues_before_close(self) -> None:
        self.set_prs([self.own_pr_record(42)])
        self.set_merge_queue(answers=[True, False], id="PR_notdue42")
        res = self.run_script(render_due=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        calls = self.gh_calls()
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 3)
        self.assertIn("isInMergeQueue", " ".join(gql[0]))
        self.assertIn("dequeuePullRequest(input:{id:$id})", " ".join(gql[1]))
        self.assertIn("id=PR_notdue42", gql[1])
        self.assertIn("isInMergeQueue", " ".join(gql[2]))
        dequeue_idx = next(i for i, c in enumerate(calls) if "dequeuePullRequest" in " ".join(c))
        close_idx = next(i for i, c in enumerate(calls) if c[:2] == ["pr", "close"])
        self.assertLess(dequeue_idx, close_idx)
        self.assertEqual(
            calls[close_idx][:6],
            ["pr", "close", "--repo", self.github_repository, "--delete-branch", "42"],
        )
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))

    def test_case_o3_own_pr_not_due_queue_read_failure_stops_close(self) -> None:
        self.set_prs([self.own_pr_record(42)])
        self.set_merge_queue(returncode=1)
        res = self.run_script(render_due=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("merge-queue GraphQL query failed for pull request #42", res.stderr)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 1)
        self.assertFalse(any("dequeuePullRequest" in " ".join(c) for c in gql))
        self.assertFalse(any(c[:2] == ["pr", "close"] for c in self.gh_calls()))
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))

    def test_case_o4_own_pr_not_due_dequeue_failure_stops_close(self) -> None:
        self.set_prs([self.own_pr_record(42)])
        self.set_merge_queue(queued=True, dequeue_returncode=1)
        res = self.run_script(render_due=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("dequeue failed for pull request #42", res.stderr)
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 2)
        self.assertIn("dequeuePullRequest", " ".join(gql[1]))
        self.assertFalse(any(c[:2] == ["pr", "close"] for c in self.gh_calls()))
        self.assertNotIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))

    def test_case_p_render_dequeue_polls_validation(self) -> None:
        for idx, bad in enumerate(("0", "00", "012", "", "1000", "121")):
            with self.subTest(polls=bad):
                self._assert_bad_dequeue_env("RENDER_DEQUEUE_POLLS", bad, f"polls-bad-{idx}")
        for idx, ok in enumerate(("1", "120")):
            with self.subTest(polls=ok):
                self._fresh_case(f"polls-ok-{idx}")
                old_head = self.stage_queued_render()
                self.set_merge_queue(answers=[True, False, False], id="PR_stale42")
                res = self.run_script(render_due=True, extra_env={"RENDER_DEQUEUE_POLLS": ok})
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertNotEqual(self.bare_head(), old_head)
                self.assertEqual(len(self.graphql_calls()), 4)
                self.assertTrue(
                    any(
                        c[:2] == ["pr", "merge"] and "--auto" in c and "42" in c
                        for c in self.gh_calls()
                    )
                )

    def test_case_q_render_dequeue_sleep_validation(self) -> None:
        for idx, bad in enumerate(("abc", "", "1.5", "-1")):
            with self.subTest(sleep_s=bad):
                self._assert_bad_dequeue_env("RENDER_DEQUEUE_SLEEP_S", bad, f"sleep-bad-{idx}")

    def test_case_r_requeued_before_push_stops_push(self) -> None:
        old_head = self.stage_queued_render()
        self.set_merge_queue(answers=[True, False, True], id="PR_stale42")
        res = self.run_script(render_due=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(
            "open-render-pr.sh: pull request #42 was queued again before the push; not pushing",
            res.stderr,
        )
        gql = self.graphql_calls()
        self.assertEqual(len(gql), 4)
        self.assertIn("isInMergeQueue", " ".join(gql[0]))
        self.assertIn("dequeuePullRequest(input:{id:$id})", " ".join(gql[1]))
        self.assertIn("isInMergeQueue", " ".join(gql[2]))
        self.assertIn("isInMergeQueue", " ".join(gql[3]))
        self.assertEqual(self.bare_head(), old_head)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.gh_calls()))

    def test_case_s_run_script_isolates_caller_render_dequeue_polls(self) -> None:
        with patch.dict(os.environ, {"RENDER_DEQUEUE_POLLS": "0"}):
            self.test_case_j_dequeues_before_pushing_new_tag()
        self.assertEqual(len(self.graphql_calls()), 4)
        self.assertIn(self.render_branch, git("-C", str(self.bare_repo), "branch"))


if __name__ == "__main__":
    unittest.main()
