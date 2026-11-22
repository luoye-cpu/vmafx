#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Inspect and guard local VMAFx merge-train and fork-queue actions (ADR-1244, ADR-3610).

Every remote mutation requires --apply; inspect, admit-fork and queue-audit are
read-only. Local gate receipts are produced only by executing make lint and make
test, never by accepting a pass flag.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TypeGuard

# Installed releases carry their own scripts/lib; checkouts retain the repository root.
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent
        if (Path(__file__).resolve().parent / "scripts/lib").is_dir()
        else Path(__file__).resolve().parents[2]
    ),
)

from scripts.lib.safe_subprocess import run as run_command

REPO = "vidiko/vmafx"
RELEASE_PR = 1213
LANDING_IDENTITY = "lusoris <lusoris@proton.me>"
KEY_BYTES = 32
INVENTORY_LIMIT = 100
PR_FIELDS = (
    "number,title,body,state,isDraft,baseRefName,headRefName,headRefOid,"
    "isCrossRepository,autoMergeRequest,mergeStateStatus"
)
GATES = (("make", "lint"), ("make", "test"))
HARDWARE_SWITCHES = ("GPU_COVERAGE_ENABLED", "SYCL_ARC_RUNNER_ENABLED")
HEX_SHA_RE = re.compile(r"[0-9a-f]{40}")
DECISIVE_REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
IGNORED_REVIEW_STATES = frozenset({"COMMENTED", "PENDING"})
QUEUE_QUERY = (
    'query { repository(owner: "vidiko", name: "vmafx") { '
    'mergeQueue(branch: "master") { entries(first: 100) { nodes { '
    "state pullRequest { number isCrossRepository headRefOid } "
    "} } } } }"
)


class Refused(RuntimeError):
    """The action has no safe, current authorization."""


def _is_sha(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and HEX_SHA_RE.fullmatch(value) is not None


def _is_positive_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_str_list(value: Any, *, non_empty_items: bool = False) -> bool:
    if not isinstance(value, list):
        return False
    if non_empty_items:
        return all(isinstance(item, str) and bool(item.strip()) for item in value)
    return all(isinstance(item, str) for item in value)


def _verified_head(pr: dict[str, Any], expected: str | None = None) -> str:
    if not isinstance(pr.get("isDraft"), bool):
        raise Refused("PR draft state is unavailable")
    head = pr.get("headRefOid", "")
    if not _is_sha(head):
        raise Refused("PR head is unavailable")
    if expected is not None and head != expected:
        raise Refused("PR head changed; repeat inspection and validation")
    return head


def _require_unchanged_text(pr: dict[str, Any], title: str, body_text: str) -> None:
    fresh_body = pr.get("body") or ""
    if pr.get("title", "") != title or fresh_body != body_text:
        raise Refused("PR title or body changed during squash message check")


def _review_login(review: dict[str, Any]) -> str:
    author = review.get("author")
    login = author.get("login") if isinstance(author, dict) else ""
    return login if isinstance(login, str) else ""


def _review_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise Refused("PR review timestamp is unavailable")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise Refused(f"invalid PR review timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise Refused(f"PR review timestamp lacks timezone: {value}")
    return parsed


def _latest_decisive_reviews(
    reviews: list[dict[str, Any]], allowed: set[str]
) -> dict[str, tuple[tuple[datetime, int], dict[str, Any]]]:
    latest: dict[str, tuple[tuple[datetime, int], dict[str, Any]]] = {}
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise Refused("GitHub returned incomplete or invalid PR reviews")
        login = _review_login(review)
        if login not in allowed:
            continue
        state = review.get("state")
        if state in IGNORED_REVIEW_STATES:
            continue
        if state not in DECISIVE_REVIEW_STATES:
            raise Refused(f"invalid PR review state: {state!r}")
        rank = (_review_time(review.get("submittedAt")), index)
        previous = latest.get(login)
        if previous is None or rank > previous[0]:
            latest[login] = (rank, review)
    return latest


def _select_fork_approval(
    reviews: list[dict[str, Any]], reviewed_head: str, fork_reviewers: list[str]
) -> tuple[tuple[str, str] | None, str]:
    if not fork_reviewers:
        return None, "train policy fork_reviewers must be non-empty"
    latest = _latest_decisive_reviews(reviews, set(fork_reviewers))
    matching: list[tuple[tuple[datetime, int], str, str]] = []
    for login, (rank, review) in latest.items():
        state = review.get("state")
        if state == "CHANGES_REQUESTED":
            return None, f"fork reviewer {login} has CHANGES_REQUESTED; approval refused"
        if state == "APPROVED":
            commit = review.get("commit")
            oid = commit.get("oid") if isinstance(commit, dict) else None
            if oid == reviewed_head:
                matching.append((rank, login, str(review["submittedAt"])))
    if not matching:
        return None, f"no maintainer approval from fork_reviewers on head {reviewed_head}"
    _, reviewer, submitted_at = max(matching, key=lambda item: item[0])
    return (reviewer, submitted_at), ""


def _queue_nodes(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("errors"):
        raise Refused("GitHub returned malformed or failed mergeQueue response")
    cursor: Any = payload
    for key in ("data", "repository", "mergeQueue", "entries"):
        cursor = cursor.get(key) if isinstance(cursor, dict) else None
    nodes = cursor.get("nodes") if isinstance(cursor, dict) else None
    if not isinstance(nodes, list) or len(nodes) >= INVENTORY_LIMIT:
        raise Refused("GitHub returned incomplete or malformed mergeQueue entries")
    return nodes


def _parse_queue_entry(entry: Any) -> tuple[int, bool, str]:
    if not isinstance(entry, dict):
        raise Refused("malformed merge queue entry")
    pr = entry.get("pullRequest")
    if not isinstance(pr, dict):
        raise Refused("malformed merge queue entry")
    pr_head = pr.get("headRefOid")
    number, cross = pr.get("number"), pr.get("isCrossRepository")
    if not (_is_positive_int(number) and isinstance(cross, bool)):
        raise Refused("malformed merge queue entry")
    if not _is_sha(pr_head):
        raise Refused("malformed merge queue entry")
    return number, cross, pr_head


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_squash_composer() -> Any:
    """Load the squash_message module that ships with this gateway.

    An installed gateway (``releases/<sha>/merge_train_guard.py``) loads the
    composer installed beside it, whose hash the launcher checked; the
    repository copy (``scripts/dev/``) loads ``scripts/ci/squash_message.py``.
    A missing or failing module raises ``Refused``.
    """
    here = Path(__file__).resolve().parent
    candidates = (here / "squash_message.py", here.parent / "ci" / "squash_message.py")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise Refused(f"cannot load squash_message module: {candidates[0]} or {candidates[1]}")
    spec = importlib.util.spec_from_file_location("squash_message", str(path))
    if spec is None or spec.loader is None:
        raise Refused(f"cannot load squash_message module: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # any import-time failure is a refusal, never a crash
        raise Refused(
            f"cannot load squash_message module: {path}: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def environment() -> dict[str, str]:
    """Do not inherit Git redirection or make dry-run/alternate-file flags."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
        and key
        not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS", "MAKEFILES", "MAKEOVERRIDES"}
    }


def execute(argv: list[str], cwd: Path) -> str:
    """Run fixed argument vectors; failures never become success-shaped output."""
    if not argv:
        raise Refused("execution boundary requires an executable")
    requested = Path(argv[0]).expanduser()
    binary: str | None
    if requested.is_absolute():
        binary = str(requested.resolve(strict=False))
    elif len(requested.parts) == 1:
        binary = shutil.which(argv[0], path=environment().get("PATH"))
    else:
        binary = str((cwd / requested).resolve(strict=False))
    if binary is None or not Path(binary).is_file():
        raise Refused(f"required executable is unavailable: {argv[0]}")
    result = run_command(
        [binary, *argv[1:]],
        allowed_executables=(binary,),
        cwd=cwd,
        env=environment(),
        capture_output=True,
        text=True,
        timeout_seconds=120,
    )
    if result.returncode:
        raise Refused(f"{' '.join(argv[:2])} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


class Train:
    def __init__(self, root: Path, state: Path) -> None:
        self.root = root.resolve()
        self.state = state.resolve()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return execute(["git", "-C", str(cwd or self.root), *args], self.root).strip()

    def gh(self, *args: str) -> str:
        return execute(["gh", *args, "--repo", f"github.com/{REPO}"], self.root)

    def pr(self, number: int) -> dict[str, Any]:
        value = json.loads(self.gh("pr", "view", str(number), "--json", PR_FIELDS))
        if not isinstance(value, dict) or value.get("number") != number:
            raise Refused("GitHub returned incomplete or mismatched PR metadata")
        return value

    def reviews(self, number: int) -> list[dict[str, Any]]:
        value = json.loads(self.gh("pr", "view", str(number), "--json", "reviews"))
        if not isinstance(value, dict):
            raise Refused("GitHub returned incomplete or invalid PR reviews")
        reviews = value.get("reviews")
        if not isinstance(reviews, list) or not all(isinstance(item, dict) for item in reviews):
            raise Refused("GitHub returned incomplete or invalid PR reviews")
        return reviews

    def policy(self) -> dict[str, Any]:
        value = json.loads((self.state / "policy.json").read_text())
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise Refused("missing supported train policy")
        for key in ("protected_branches", "protected_worktrees"):
            if not _is_str_list(value.get(key)):
                raise Refused(f"invalid train policy: {key}")
        if "fork_reviewers" in value and not _is_str_list(
            value["fork_reviewers"], non_empty_items=True
        ):
            raise Refused("invalid train policy: fork_reviewers")
        return value

    def held(self) -> set[int]:
        result = {RELEASE_PR}
        for raw in (self.state / "hold.txt").read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            token = line.split()[0]
            if not token.isdecimal() or int(token) <= 0:
                raise Refused("invalid hold entry; refusing mutations")
            result.add(int(token))
        return result

    def worktrees(self) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        record: dict[str, str] = {}
        for field in self.git("worktree", "list", "--porcelain", "-z").split("\0"):
            if not field:
                if record:
                    records.append(record)
                    record = {}
                continue
            key, _, value = field.partition(" ")
            record[key] = value
        if record:
            records.append(record)
        return records

    def _open_master_pr(self, number: int) -> tuple[dict[str, Any], dict[str, Any]]:
        policy = self.policy()
        if (self.state / "PAUSED").exists():
            raise Refused("train is paused")
        if number in self.held():
            raise Refused(f"PR #{number} is held")
        pr = self.pr(number)
        if pr.get("state") != "OPEN" or pr.get("baseRefName") != "master":
            raise Refused("only open PRs targeting master are eligible")
        return policy, pr

    def guard(self, number: int, expected: str | None = None) -> dict[str, Any]:
        policy, pr = self._open_master_pr(number)
        if pr.get("isCrossRepository") is not False:
            raise Refused("cross-repository PRs require an explicit owner")
        head = _verified_head(pr, expected)
        branch = pr.get("headRefName", "")
        if not isinstance(branch, str) or not branch or branch == "master":
            raise Refused("invalid PR branch")
        self.git("check-ref-format", "--branch", branch)
        if branch in policy["protected_branches"]:
            raise Refused("PR branch belongs to a protected owner")
        protected = {Path(path).resolve() for path in policy["protected_worktrees"]}
        for worktree in self.worktrees():
            path = Path(worktree["worktree"]).resolve()
            owns_branch = worktree.get("branch") == f"refs/heads/{branch}"
            owns_head = worktree.get("HEAD") == head and path.name.startswith("agent-")
            if owns_branch or owns_head:
                raise Refused(f"PR source is checked out by another owner: {path}")
            if path in protected and worktree.get("HEAD") == head:
                raise Refused(f"PR head belongs to protected worktree: {path}")
        return pr

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Serialize cooperating actors; root must retire old unrestricted actors."""
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "mutation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Refused("another train action owns the mutation lock") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def signing_key(self, create: bool = False) -> bytes:
        path = self.state / "validation-key"
        if create and not path.exists():
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(KEY_BYTES))
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise Refused("validation key must be a private regular file")
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise Refused("invalid validation key")
        return key

    def clean_head(self, checkout: Path, head: str) -> None:
        if self.git("rev-parse", "HEAD", cwd=checkout) != head:
            raise Refused("validation checkout does not match the PR head")
        if self.git("status", "--porcelain=v1", "--untracked-files=all", cwd=checkout):
            raise Refused("validation source has tracked or untracked changes")
        for name in ("GNUmakefile", "makefile", "Makefile"):
            if (checkout / name).exists():
                # Git status excludes ignored files; an ignored GNUmakefile can
                # shadow a tracked Makefile and turn both gates into no-ops.
                self.git("ls-files", "--error-unmatch", name, cwd=checkout)

    def validate(self, number: int, checkout: Path) -> None:
        """Explicit local validation may proceed while the PR is held/paused."""
        pr = self.pr(number)
        if number == RELEASE_PR or pr.get("state") != "OPEN" or pr.get("baseRefName") != "master":
            raise Refused("validation requires an open master-target PR other than #1213")
        checkout = checkout.resolve(strict=True)
        head = pr["headRefOid"]
        self.clean_head(checkout, head)
        self.state.mkdir(parents=True, exist_ok=True)
        logs = Path(tempfile.mkdtemp(prefix="validation-", dir=self.state))
        target = self.state / f"validated-{head}.json"
        if target.exists():
            # A new failed run must not leave an earlier passing receipt usable.
            target.rename(logs / "previous-receipt.json")
        results = []
        make = shutil.which("make")
        if make is None:
            raise Refused("make is unavailable")
        for command in GATES:
            self.clean_head(checkout, head)
            log = logs / f"{command[1]}.log"
            with log.open("wb") as output:
                result = run_command(
                    [make, command[1]],
                    allowed_executables=(make,),
                    cwd=checkout,
                    env=environment(),
                    stdout=output,
                    stderr_to_stdout=True,
                    timeout_seconds=7200,
                )
            if result.returncode:
                raise Refused(f"{' '.join(command)} failed ({result.returncode}); evidence: {log}")
            results.append({"command": list(command), "log": str(log), "sha256": digest(log)})
        self.clean_head(checkout, head)
        if self.pr(number)["headRefOid"] != head:
            raise Refused("PR head changed during validation")
        payload = {
            "schema": 1,
            "repository": REPO,
            "head": head,
            "tree": self.git("rev-parse", "HEAD^{tree}", cwd=checkout),
            "generator_sha256": digest(Path(__file__)),
            "results": results,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        signature = hmac.new(
            self.signing_key(create=True), encoded(payload), hashlib.sha256
        ).hexdigest()
        target.write_text(json.dumps({"payload": payload, "signature": signature}, indent=2) + "\n")
        print(f"Full make lint/test validation recorded for {head}: {target}")

    def require_validation(self, head: str) -> None:
        receipt = json.loads((self.state / f"validated-{head}.json").read_text())
        payload = receipt["payload"]
        signature = hmac.new(self.signing_key(), encoded(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, receipt.get("signature", "")):
            raise Refused("validation receipt was not issued by the local gate runner")
        if (
            payload.get("schema") != 1
            or payload.get("repository") != REPO
            or payload.get("head") != head
            or payload.get("generator_sha256") != digest(Path(__file__))
        ):
            raise Refused("validation receipt does not match this head and gate runner")
        results = payload.get("results", [])
        if [item.get("command") for item in results] != [list(command) for command in GATES]:
            raise Refused("full make lint and make test evidence is missing")
        for item in results:
            log = Path(item["log"]).resolve(strict=True)
            if not log.is_relative_to(self.state) or digest(log) != item["sha256"]:
                raise Refused("validation log is missing, outside state, or changed")

    def require_checks(self, number: int) -> None:
        checks = json.loads(
            self.gh("pr", "checks", str(number), "--required", "--json", "name,bucket,state")
        )
        if not checks or not any(
            item.get("name") == "Required Checks Aggregator" for item in checks
        ):
            raise Refused("required checks are absent; zero checks is not green")
        if any(item.get("bucket") != "pass" for item in checks):
            raise Refused("required checks are not eligible for this action")

    def enabled_hardware_switches(self) -> list[str]:
        """Return enabled hardware lanes; a fork merge group cannot run them."""
        variables = json.loads(self.gh("variable", "list", "--json", "name,value"))
        if not isinstance(variables, list):
            raise Refused("GitHub returned invalid repository variables")
        enabled = []
        for item in variables:
            if not isinstance(item, dict):
                raise Refused("GitHub returned invalid repository variables")
            name = item.get("name")
            value = item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                raise Refused("GitHub returned invalid repository variables")
            if name in HARDWARE_SWITCHES and value.strip().lower() == "true":
                enabled.append(name)
        return sorted(enabled)

    def _check_squash_message(self, number: int, title: str, body_text: str) -> Any:
        composer = load_squash_composer()
        code, output = composer.check_message(
            title=title,
            number=number,
            body_text=body_text,
            identity=LANDING_IDENTITY,
            repo_root=self.root,
        )
        if code != 0:
            raise Refused(f"squash message check failed: {output.strip()}")
        return composer

    def _require_fork_approval(
        self, number: int, reviewed_head: str, reviewers: list[str]
    ) -> tuple[str, str]:
        if not reviewers:
            raise Refused("train policy fork_reviewers must be non-empty")
        approved, reason = _select_fork_approval(self.reviews(number), reviewed_head, reviewers)
        if approved is None:
            raise Refused(reason)
        return approved

    def admit_fork(self, number: int, reviewed_head: str) -> dict[str, Any]:
        """Verify read-only fork PR queue-admission gates at the exact reviewed head."""
        if not _is_sha(reviewed_head):
            raise Refused("reviewed head must be a 40-character lowercase hex SHA")
        policy, pr = self._open_master_pr(number)
        if pr.get("isDraft") is not False:
            raise Refused("draft PRs cannot be admitted to the merge queue")
        if pr.get("isCrossRepository") is not True:
            raise Refused(
                "in-repository PRs must use merge-train actions "
                "(inspect, merge, arm, rebase, promote)"
            )
        head = _verified_head(pr)
        if head != reviewed_head:
            raise Refused("live PR head does not match reviewed head")
        merge_state = pr.get("mergeStateStatus")
        if merge_state != "CLEAN":
            raise Refused(f"PR merge state is {merge_state!r}, expected CLEAN")
        reviewer_login, submitted_at = self._require_fork_approval(
            number, reviewed_head, policy.get("fork_reviewers", [])
        )
        self.require_checks(number)
        enabled_hardware = self.enabled_hardware_switches()
        if enabled_hardware:
            raise Refused(
                "fork merge groups cannot satisfy enabled hardware lanes: "
                + ", ".join(enabled_hardware)
            )
        title = pr.get("title", "")
        body_text = pr.get("body", "") or ""
        self._check_squash_message(number, title, body_text)
        _, fresh = self._open_master_pr(number)
        if fresh.get("headRefOid") != reviewed_head:
            raise Refused("PR head changed during squash message check")
        _require_unchanged_text(fresh, title, body_text)
        if fresh.get("autoMergeRequest") is not None:
            raise Refused("PR has a pending auto-merge request; disarm it before retrying")
        return {
            "number": number,
            "head": reviewed_head,
            "reviewer": reviewer_login,
            "submitted_at": submitted_at,
            "merge_state": merge_state,
            "checks": "pass",
        }

    def queue_entries(self) -> list[dict[str, Any]]:
        raw = execute(["gh", "api", "graphql", "-f", f"query={QUEUE_QUERY}"], self.root)
        nodes = _queue_nodes(json.loads(raw))
        for node in nodes:
            _parse_queue_entry(node)
            if not isinstance(node.get("state"), str) or not node["state"]:
                raise Refused("GitHub returned mergeQueue entry without state")
        return nodes

    def audit_queue(self) -> list[str]:
        policy = self.policy()
        reviewers: list[str] = policy.get("fork_reviewers", [])
        problems: list[str] = []
        for entry in self.queue_entries():
            # headCommit is the squash commit GitHub builds for the entry, never the pull
            # request head, so the live head and its approval decide alone.
            number, cross, pr_head = _parse_queue_entry(entry)
            if not cross:
                continue
            approved, reason = _select_fork_approval(self.reviews(number), pr_head, reviewers)
            if approved is None:
                problems.append(f"PR #{number}: {reason}")
        return problems

    def merge(self, number: int, auto: bool) -> None:
        """Execute validated merge after verifying checks and squash commit message."""
        pr = self.guard(number)
        if pr["isDraft"]:
            raise Refused("draft PRs cannot be armed or merged")
        self.require_validation(pr["headRefOid"])
        self.require_checks(number)
        pr = self.guard(number, pr["headRefOid"])
        title = pr.get("title", "")
        body_text = pr.get("body", "") or ""
        composer = self._check_squash_message(number, title, body_text)
        fresh = self.guard(number, pr["headRefOid"])
        _require_unchanged_text(fresh, title, body_text)
        subject_line = composer.subject(title, number)
        body_content = composer.body(body_text, LANDING_IDENTITY)
        args = [
            "pr",
            "merge",
            str(number),
            "--squash",
            "--match-head-commit",
            pr["headRefOid"],
            "--subject",
            subject_line,
            "--body",
            body_content,
        ]
        if auto:
            args.append("--auto")
        self.gh(*args)

    def rebase(self, number: int) -> str:
        """Rebase an eligible pull request onto master in an isolated worktree."""
        pr = self.guard(number)
        head, branch = pr["headRefOid"], pr["headRefName"]
        runs = self.state / "worktrees"
        if any(runs.glob(f"pr-{number}-*/checkout")):
            raise Refused("a retained train checkout needs an explicit owner handoff before retry")
        canonical = {"https://github.com/vidiko/vmafx.git", "git@github.com:vidiko/vmafx.git"}
        for direction in ((), ("--push",)):
            urls = self.git("remote", "get-url", "--all", *direction, "origin").splitlines()
            if len(urls) != 1 or urls[0] not in canonical:
                raise Refused("origin URLs must point only to the canonical VMAFx repository")
        remote_head = self.git("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
        if remote_head != f"{head}\trefs/heads/{branch}":
            raise Refused("remote branch changed before rebase")
        base, _, ref = self.git(
            "ls-remote", "--exit-code", "origin", "refs/heads/master"
        ).partition("\t")
        if ref != "refs/heads/master" or re.fullmatch(r"[0-9a-f]{40}", base) is None:
            raise Refused("remote master head is unavailable")
        # FETCH_HEAD is shared by worktrees and can be replaced by an unrelated
        # fetch. Fetch the observed objects without reading or changing it.
        self.git("fetch", "--no-tags", "--no-write-fetch-head", "origin", head, base)
        runs.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix=f"pr-{number}-", dir=runs))
        checkout = run / "checkout"
        self.git("worktree", "add", "--detach", str(checkout), head)
        (run / "owner.json").write_text(
            json.dumps({"pr": number, "head": head, "checkout": str(checkout)}) + "\n"
        )
        try:
            self.git("rebase", base, cwd=checkout)
            self.guard(number, head)
            updated = self.git("rev-parse", "HEAD", cwd=checkout)
            self.clean_head(checkout, updated)
            if updated != head:
                self.git(
                    "push",
                    f"--force-with-lease=refs/heads/{branch}:{head}",
                    "origin",
                    f"HEAD:refs/heads/{branch}",
                    cwd=checkout,
                )
            self.guard(number, updated)
        except (Refused, OSError):
            print(f"Retained failed rebase checkout and ownership receipt: {run}", file=sys.stderr)
            raise
        if self.git(
            "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", cwd=checkout
        ):
            print(f"Retained rebase evidence/artifacts: {run}")
        else:
            self.git("worktree", "remove", str(checkout))
        return updated

    def promote(self, number: int) -> None:
        pr = self.guard(number)
        if not pr["isDraft"]:
            raise Refused("promotion requires a draft PR")
        head = self.rebase(number)
        self.guard(number, head)
        self.gh("pr", "ready", str(number))
        # Arming is deliberately separate: the new head requires full local gates.

    def cycle(self, apply: bool, window: int) -> None:
        """Bound one train pass; every action repeats the shared fresh guard."""
        prs = json.loads(
            self.gh(
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                str(INVENTORY_LIMIT),
                "--json",
                PR_FIELDS,
            )
        )
        if not isinstance(prs, list) or len(prs) >= INVENTORY_LIMIT:
            raise Refused("PR inventory is incomplete; inspect the queue manually")
        eligible = []
        for pr in prs:
            number = pr["number"]
            try:
                current = self.guard(number)
            except Refused as exc:
                print(f"PR #{number}: refused: {exc}")
                if pr.get("autoMergeRequest") is not None:
                    raise Refused(
                        f"ineligible PR #{number} already has server auto-merge; owner must disarm it"
                    ) from exc
                continue
            eligible.append(current)
            print(f"PR #{number}: eligible {'draft' if current['isDraft'] else 'ready'}")
        if not apply:
            return
        ready = [pr for pr in eligible if not pr["isDraft"]]
        for pr in ready:
            try:
                if pr.get("mergeStateStatus") in {"BEHIND", "DIRTY"}:
                    self.rebase(pr["number"])
                else:
                    self.merge(pr["number"], auto=False)
            except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
                print(f"PR #{pr['number']}: waiting for owner/validation: {exc}")
        if len(ready) < window:
            drafts = sorted((pr for pr in eligible if pr["isDraft"]), key=lambda pr: pr["number"])
            if drafts:
                self.promote(drafts[0]["number"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "inspect",
            "cycle",
            "promote",
            "rebase",
            "arm",
            "merge",
            "validate",
            "admit-fork",
            "queue-audit",
        ),
    )
    parser.add_argument("number", type=int, nargs="?")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--checkout", type=Path, help="explicit checkout for executing full local gates"
    )
    parser.add_argument(
        "--reviewed-head", type=str, help="exact 40-char SHA approved for admit-fork"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--window", type=int, default=3, help="maximum ready queue size for cycle")
    return parser


def _require_pr_number(number: int | None) -> int:
    if number is None or number <= 0:
        raise Refused("an explicit positive PR number is required")
    return number


def _run_queue_audit(train: Train, args: argparse.Namespace) -> int:
    if args.number is not None:
        raise Refused("queue-audit takes no PR number")
    if args.apply:
        raise Refused("queue-audit is read-only and refuses --apply")
    problems = train.audit_queue()
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("queue audit: 0 problems")
    return 0


def _run_admit_fork(train: Train, args: argparse.Namespace) -> int:
    number = _require_pr_number(args.number)
    if args.apply:
        raise Refused("admit-fork is read-only and refuses --apply")
    if not args.reviewed_head:
        raise Refused("admit-fork requires --reviewed-head")
    print(json.dumps(train.admit_fork(number, args.reviewed_head), indent=2))
    return 0


def _run_readonly(train: Train, args: argparse.Namespace) -> int | None:
    if args.action == "queue-audit":
        return _run_queue_audit(train, args)
    if args.action == "admit-fork":
        return _run_admit_fork(train, args)
    if args.action == "cycle" and not args.apply:
        train.cycle(False, args.window)
        return 0
    if args.action == "inspect":
        print(json.dumps(train.guard(_require_pr_number(args.number)), indent=2))
        return 0
    return None


def _run_mutating(train: Train, args: argparse.Namespace) -> None:
    if args.action == "cycle":
        if args.window <= 0:
            raise Refused("cycle window must be positive")
        train.cycle(True, args.window)
        return
    number = _require_pr_number(args.number)
    if args.action == "validate":
        if args.checkout is None:
            raise Refused("validate requires an explicit --checkout")
        train.validate(number, args.checkout)
    elif args.action == "promote":
        train.promote(number)
    elif args.action == "rebase":
        train.rebase(number)
    else:
        train.merge(number, auto=args.action == "arm")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    train = Train(args.repo_root, args.state_dir)
    try:
        readonly_rc = _run_readonly(train, args)
        if readonly_rc is not None:
            return readonly_rc
        if args.action != "cycle":
            _require_pr_number(args.number)
        if not args.apply:
            raise Refused(
                "mutation/validation requires --apply; use inspect for read-only eligibility"
            )
        with train.mutation_lock():
            _run_mutating(train, args)
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"merge-train: refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
