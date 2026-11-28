#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the rendered changelog, ADR index and rebase notes (ADR-3295, ADR-3627).

A pull request carries fragments, never the render (ADR-2197). Since ADR-3295
the render lands as its own render-only pull request after a batch, so master
may sit between a landing and its render for a while. This check keeps that
window bounded and the last render exact:

1. ``make docs-render-check`` passes at HEAD: green.
2. Otherwise find the last first-parent commit that touched a rendered file.
   Its own tree must pass ``make docs-render-check``: a render that is not the
   render of its fragments (a hand edit, a stale render) is red.
3. The first first-parent commit after it starts the owed render. Older than
   the bound (24 hours by default): red. Within it: green with a warning.

With ``--group-base SHA`` the script checks the last first-parent render
commit in ``SHA..HEAD`` on its own tree. ``SHA`` must be a full commit ID that
is an ancestor of ``HEAD``, otherwise the check fails. A group without a render
passes.

With ``--open-after-hours H`` the script decides whether the render workflow
(``.github/workflows/docs-render-pr.yml``, ADR-3373) opens or refreshes the
render pull request. It prints ``open=true`` when a render has been owed for
at least H hours or the last render is inexact, and ``open=false`` otherwise.
It also prints ``owed_since=<sha>`` (empty when current) and exits 0.

Usage: check_render_on_master.py [--root DIR] [--bound-hours H] [--now EPOCH]
       [--group-base SHA | --open-after-hours H]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.safe_subprocess import run as run_command  # noqa: E402

# The files `make docs-render` writes; deliverables-check.sh keeps the same list
# (`rendered_paths`), without `_order.txt`, which no render writes.
RENDERED_PATHS = (
    "CHANGELOG.md",
    "docs/adr/README.md",
    "docs/adr/titles.md",
    "docs/research/titles.md",
    "docs/adr/by-tag",
    "docs/rebase-notes.md",
)
GIT_TIMEOUT_S = 120
RENDER_TIMEOUT_S = 900
DEFAULT_BOUND_HOURS = 24.0
TITLE = "ADR-3295 rendered docs"
FULL_COMMIT_ID_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True)
class Verdict:
    """The outcome: exit status and the annotation lines to print."""

    status: int
    lines: tuple[str, ...]


def git_environment() -> dict[str, str]:
    """The caller's environment without GIT_* variables, which would point git
    at another repository (a hook's, a test's)."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(root: Path, *args: str) -> str:
    """Run git in `root` and return stdout; a failure raises."""
    result = run_command(
        ["git", "-C", str(root), *args],
        allowed_executables=("git",),
        env=git_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout_seconds=GIT_TIMEOUT_S,
    )
    return str(result.stdout)


def render_check(tree: Path) -> tuple[bool, str]:
    """Run `make docs-render-check` in `tree`; return (passed, output)."""
    result = run_command(
        ["make", "-s", "-C", str(tree), "docs-render-check"],
        allowed_executables=("make",),
        env=git_environment(),
        capture_output=True,
        stderr_to_stdout=True,
        text=True,
        check=False,
        timeout_seconds=RENDER_TIMEOUT_S,
    )
    return result.returncode == 0, str(result.stdout).strip()


def render_check_at(root: Path, commit: str) -> tuple[bool, str]:
    """Run the render check on the tree of `commit` in a disposable worktree."""
    scratch = Path(tempfile.mkdtemp(prefix="render-check-"))
    tree = scratch / "tree"
    try:
        git(root, "worktree", "add", "--detach", "--quiet", str(tree), commit)
        return render_check(tree)
    finally:
        run_command(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
            allowed_executables=("git",),
            env=git_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout_seconds=GIT_TIMEOUT_S,
        )
        shutil.rmtree(scratch, ignore_errors=True)
        git(root, "worktree", "prune")


def last_render_commit(root: Path, revision: str = "HEAD") -> str:
    """The last first-parent commit in a revision or range that touched a rendered file."""
    out = git(
        root,
        "log",
        "--first-parent",
        "-n",
        "1",
        "--format=%H",
        "--end-of-options",
        revision,
        "--",
        *RENDERED_PATHS,
    )
    return out.strip()


def first_commit_after(root: Path, commit: str) -> tuple[str, int] | None:
    """The first first-parent commit after `commit` and its committer time."""
    out = git(root, "log", "--first-parent", "--reverse", "--format=%H %ct", f"{commit}..HEAD")
    lines = out.splitlines()
    if not lines:
        return None
    sha, stamp = lines[0].split()
    return sha, int(stamp)


def judge_owed(root: Path, render: str, now: float, bound_hours: float) -> Verdict:
    """Steps 2 and 3: the last render is exact and the owed one is young enough."""
    exact, output = render_check_at(root, render)
    if not exact:
        return Verdict(
            1,
            (
                output,
                f"::error title={TITLE}::the last render commit {render[:12]} is not the "
                "render of its fragments (make docs-render-check fails on its tree)",
            ),
        )
    first = first_commit_after(root, render)
    if first is None:
        return Verdict(
            1, (f"::error title={TITLE}::HEAD is the last render commit and still fails",)
        )
    sha, stamp = first
    age_hours = (now - stamp) / 3600.0
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(stamp))
    owed = f"render owed since {sha[:12]} ({when}, {age_hours:.1f} h ago)"
    if age_hours > bound_hours:
        return Verdict(1, (f"::error title={TITLE}::{owed}; the bound is {bound_hours:g} h",))
    return Verdict(0, (f"::warning title={TITLE}::{owed}; within the {bound_hours:g} h bound",))


def judge(root: Path, now: float, bound_hours: float) -> Verdict:
    """The whole check for the HEAD of `root`."""
    passed, output = render_check(root)
    if passed:
        return Verdict(0, ("check_render_on_master: the render at HEAD is current",))
    render = last_render_commit(root)
    if not render:
        return Verdict(1, (output, f"::error title={TITLE}::no commit of HEAD carries a render"))
    verdict = judge_owed(root, render, now, bound_hours)
    return Verdict(verdict.status, (output, *verdict.lines))


def merge_group_base_error(root: Path, base: str) -> str | None:
    """Return an `::error` line when `base` is not a full ancestor commit ID of `HEAD`."""
    if not base:
        return f"::error title={TITLE}::missing merge-group base SHA"
    if FULL_COMMIT_ID_RE.fullmatch(base) is None:
        return f"::error title={TITLE}::merge-group base {base!r} is not a full commit ID"
    result = run_command(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", "--end-of-options", base, "HEAD"],
        allowed_executables=("git",),
        env=git_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout_seconds=GIT_TIMEOUT_S,
    )
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        return f"::error title={TITLE}::merge-group base {base[:12]} is not an ancestor of HEAD"
    stderr_lines = result.stderr.strip().splitlines()
    first_line = stderr_lines[0] if stderr_lines else "no stderr output"
    return (
        f"::error title={TITLE}::git merge-base --is-ancestor failed for "
        f"{base[:12]} (rc {result.returncode}): {first_line}"
    )


def judge_merge_group(root: Path, base: str) -> Verdict:
    """Check the last render in the group against the fragments in its own tree.

    `base` must be a full commit ID that is an ancestor of `HEAD`.
    """
    error = merge_group_base_error(root, base)
    if error is not None:
        return Verdict(1, (error,))
    render = last_render_commit(root, f"{base}..HEAD")
    if not render:
        return Verdict(0, ("check_render_on_master: no render commit in the merge group",))
    exact, output = render_check_at(root, render)
    if exact:
        return Verdict(0, (f"check_render_on_master: merge-group render {render[:12]} is exact",))
    return Verdict(
        1,
        (
            output,
            f"::error title={TITLE}::merge-group render commit {render[:12]} is not the render "
            "of its fragments. Re-render after the group's preceding changes land.",
        ),
    )


def owed_age_hours(root: Path, now: float, render: str) -> tuple[str, float]:
    """The first commit of the owed render and its age, starting at the root without a render."""
    if render:
        first = first_commit_after(root, render)
    else:
        out = git(root, "log", "--first-parent", "--reverse", "--format=%H %ct", "HEAD")
        root_sha, root_stamp = out.splitlines()[0].split()
        first = (root_sha, int(root_stamp))
    if first is None:
        return (render, float("inf"))
    sha, stamp = first
    return sha, (now - stamp) / 3600.0


def decide_pull_request(root: Path, now: float, open_after_hours: float) -> Verdict:
    """Open once a render is overdue or the last render was inexact on its own tree."""
    passed, _ = render_check(root)
    if passed:
        return Verdict(0, ("open=false", "owed_since="))
    render = last_render_commit(root)
    sha, age = owed_age_hours(root, now, render)
    due = age >= open_after_hours
    if render and not due:
        exact, _ = render_check_at(root, render)
        due = not exact
    decision = "true" if due else "false"
    return Verdict(0, (f"open={decision}", f"owed_since={sha}", f"owed_hours={age:.1f}"))


def _bind_group_base_arg(argv: list[str]) -> list[str]:
    """Bind `--group-base <value>` as `--group-base=<value>` so `-` values reach validation."""
    bound: list[str] = []
    skip_next = False
    end_of_opts = False
    for index, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            end_of_opts = True
        if not end_of_opts and arg == "--group-base" and index + 1 < len(argv):
            bound.append(f"--group-base={argv[index + 1]}")
            skip_next = True
            continue
        bound.append(arg)
    return bound


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the master check, merge-group check or render workflow decision mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--bound-hours", type=float, default=DEFAULT_BOUND_HOURS)
    parser.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--group-base", metavar="SHA", help="check renders in SHA..HEAD")
    mode.add_argument(
        "--open-after-hours",
        type=float,
        default=None,
        help="decide whether to open the render pull request instead of judging (ADR-3373)",
    )
    return parser.parse_args(_bind_group_base_arg(argv))


def main(argv: list[str]) -> int:
    """Print the selected mode's verdict and return its exit status."""
    args = parse_args(argv)
    root = args.root.resolve()
    now = time.time() if args.now is None else args.now
    if args.group_base is not None:
        verdict = judge_merge_group(root, args.group_base)
    elif args.open_after_hours is not None:
        verdict = decide_pull_request(root, now, args.open_after_hours)
    else:
        verdict = judge(root, now, args.bound_hours)
    for line in verdict.lines:
        if line:
            print(line)
    return verdict.status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
