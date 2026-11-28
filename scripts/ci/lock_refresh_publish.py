#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Commit a refreshed lock as the release-bot and push it to a Renovate branch.

The Mend-hosted Renovate app blocks ``postUpgradeTasks``, so a Renovate edit of a lock
input arrives with a stale lock.  A workflow regenerates the lock on the ``renovate/**``
branch and calls this script to publish the result (ADR-3555 for the Intel NEO asset lock,
ADR-3560 for the Python hash locks).  One behaviour, one implementation: both workflows
call this script with their own allowed path list and commit message.

Subcommands::

    lock_refresh_publish.py refuse-non-renovate --label NAME
    lock_refresh_publish.py publish --label NAME --subject TEXT [--body-line TEXT]...
        [--current-message TEXT] [--remote-url URL] [--check-strays-when-unchanged] PATH...
    lock_refresh_publish.py stage --label NAME --artifact-dir DIR PATH...
    lock_refresh_publish.py restore --label NAME --artifact-dir DIR PATH...

``refuse-non-renovate`` exits 1 unless ``GITHUB_REF`` is ``refs/heads/renovate/*``.

``stage`` and ``restore`` carry the lock files between the two jobs of a refresh
workflow that keeps the release-bot token out of the job running the regenerated
code (#3702).  Neither reads an environment variable, so the regeneration job
needs no secret.  ``stage`` runs in the repository root after the refresh and
refuses (exit 1) when anything outside ``PATH`` changed or is untracked -- the
stray refusal the publish job cannot make, because it sees a fresh checkout --
then copies each changed ``PATH`` below ``--artifact-dir`` under its
repository-relative path beside ``head.txt`` (the refreshed HEAD) and
``changed.txt`` (the changed paths, possibly empty).  ``restore`` runs in the
repository root of the publish job and copies each listed staged path back into
the work tree, refusing (exit 1) a listed path that is not one of the ``PATH``
entries -- the allow-list --, when the branch head moved while the locks were
regenerated, or when a listed work-tree target is itself a symbolic link, and
refusing (exit 2) a listed path that is not a literal repository-relative file
or that the artifact does not carry as a regular file.  Every staged entry is
validated before the first copy, so a refusal leaves the work tree untouched.

``publish`` runs in the repository root after the refresh.  It refuses a non-Renovate
ref, exits 0 without a commit when no ``PATH`` changed, refuses (exit 1) when anything
outside ``PATH`` changed or is untracked, refuses when the app slug is not the release-bot,
then commits only the changed ``PATH`` entries with the release-bot identity and a
``Signed-off-by`` trailer and pushes ``HEAD`` to ``GITHUB_REF`` with an explicit lease.
A branch that changed meanwhile fails the push (exit 2) instead of being overwritten.  Each
``PATH`` is one literal repository-relative file; no pattern or directory is read.
``--check-strays-when-unchanged`` checks for stray files even when no allowed path changed;
the default preserves the Intel workflow's original early exit.

Environment: ``GH_TOKEN``, ``APP_SLUG``, ``GITHUB_REF``, ``RUNNER_TEMP`` and, without
``--remote-url``, ``GITHUB_REPOSITORY``.  ``--remote-url`` replaces the GitHub URL (tests).

Exit status: 0 published or already current, 1 refusal, 2 usage, environment or git error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.safe_subprocess import CommandOutputLimitExceeded, CommandTimedOut, run

GIT = shutil.which("git") or "/usr/bin/git"
GIT_TIMEOUT_S = 120
MAX_GIT_OUTPUT_BYTES = 1_048_576
RENOVATE_REF_PREFIX = "refs/heads/renovate/"
BOT_NAME = "vmafx-release-bot[bot]"
BOT_EMAIL = "340976456+vmafx-release-bot[bot]@users.noreply.github.com"
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2
GLOB_CHARS = frozenset("*?[]{}")
REQUIRED_ENV = ("GH_TOKEN", "APP_SLUG", "GITHUB_REF", "RUNNER_TEMP")
STAGED_LIST = "changed.txt"
STAGED_HEAD = "head.txt"
STAGED_REPO = "repo"


class Refusal(Exception):
    """The refresh must not be published; the message names the reason."""


class PublishError(Exception):
    """Git or the environment could not answer; never read as a pass."""


def git(args: list[str], cwd: Path) -> str:
    """Run bounded git in ``cwd``; a failure raises, it never becomes an empty answer."""
    try:
        done = run(
            [GIT, *args],
            allowed_executables=(GIT,),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout_seconds=GIT_TIMEOUT_S,
            max_output_bytes=MAX_GIT_OUTPUT_BYTES,
        )
    except (OSError, CommandTimedOut, CommandOutputLimitExceeded) as exc:
        raise PublishError(f"git {args[0]}: {exc}") from exc
    if done.returncode != 0:
        raise PublishError(f"git {args[0]} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def require_env() -> dict[str, str]:
    """Return the required environment, refusing an unset or empty variable."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise PublishError(f"unset environment variable(s): {', '.join(missing)}")
    return {name: os.environ[name] for name in REQUIRED_ENV}


def refuse_non_renovate(label: str, ref: str) -> None:
    """Refuse every ref that is not a ``renovate/`` branch."""
    if not ref.startswith(RENOVATE_REF_PREFIX):
        raise Refusal(f"{label}: refusing non-Renovate ref '{ref}'")


def validate_paths(label: str, paths: list[str], cwd: Path) -> None:
    """Accept only literal repository-relative file paths: no magic, no escape, no directory."""
    for path in paths:
        posix = PurePosixPath(path)
        bad = (
            not path
            or path != path.strip()
            or path.startswith((":", "/", "-"))
            or path.endswith("/")
            or "\\" in path
            or bool(GLOB_CHARS & set(path))
            or ".." in posix.parts
            or "." in posix.parts
            or posix.is_absolute()
            or (cwd / path).is_dir()
        )
        if bad:
            raise PublishError(f"{label}: not a literal repository-relative file path: {path!r}")


def literal(path: str) -> str:
    """The pathspec that names exactly ``path``."""
    return f":(literal){path}"


def status_paths(cwd: Path, pathspec: list[str]) -> list[str]:
    """Return the changed or untracked paths under ``pathspec`` (NUL-delimited porcelain)."""
    out = git(["status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspec], cwd)
    records = out.split("\0")
    paths: list[str] = []
    skip_next = False
    for record in records:
        if skip_next:
            skip_next = False
            continue
        if not record:
            continue
        if record[0] in "RC" or record[1:2] in ("R", "C"):
            skip_next = True
        paths.append(record[3:])
    return paths


def stray_paths(cwd: Path, allowed: list[str]) -> list[str]:
    """Return the changed or untracked paths that are not in ``allowed``."""
    excludes = [f":(exclude,literal){path}" for path in allowed]
    return status_paths(cwd, [".", *excludes])


def stage(args: argparse.Namespace, cwd: Path) -> int:
    """Stage the changed allowed paths for the publish job; needs no secret."""
    label = args.label
    validate_paths(label, args.paths, cwd)
    changed = status_paths(cwd, [literal(path) for path in args.paths])
    stray = stray_paths(cwd, args.paths)
    if stray:
        listing = "\n".join(stray)
        raise Refusal(f"{label}: refresh wrote outside the allowed paths:\n{listing}")
    repo_dir = args.artifact_dir / STAGED_REPO
    for path in changed:
        source = cwd / path
        if not source.is_file():
            raise PublishError(f"{label}: changed path is not a regular file: {path!r}")
        target = repo_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    (args.artifact_dir / STAGED_HEAD).write_text(
        git(["rev-parse", "HEAD"], cwd).strip() + "\n", encoding="utf-8"
    )
    (args.artifact_dir / STAGED_LIST).write_text(
        "".join(f"{path}\n" for path in changed), encoding="utf-8"
    )
    print(f"{label}: staged {len(changed)} changed path(s) for the publish job")
    return EXIT_OK


def restore(args: argparse.Namespace, cwd: Path) -> int:
    """Copy the staged paths back under the allow-list; needs no secret.

    Every staged entry is validated before anything is copied, so a refusal
    leaves the work tree untouched, and a link that survived the artifact
    transfer is refused instead of dereferenced.
    """
    label = args.label
    validate_paths(label, args.paths, cwd)
    staged_head = (args.artifact_dir / STAGED_HEAD).read_text(encoding="utf-8").strip()
    if staged_head != git(["rev-parse", "HEAD"], cwd).strip():
        raise Refusal(f"{label}: the branch moved while the locks were regenerated; rerun")
    listing = (args.artifact_dir / STAGED_LIST).read_text(encoding="utf-8")
    staged = [line for line in listing.splitlines() if line]
    for path in staged:
        validate_paths(label, [path], cwd)
        if path not in args.paths:
            raise Refusal(f"{label}: staged path is not in the allow-list: {path!r}")
        source = args.artifact_dir / STAGED_REPO / path
        if not source.is_file() or source.is_symlink():
            raise PublishError(f"{label}: the artifact does not carry {path!r} as a regular file")
    for path in staged:
        source = args.artifact_dir / STAGED_REPO / path
        target = cwd / path
        if target.is_symlink():
            raise Refusal(f"{label}: refusing to restore over a link: {path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    print(f"{label}: restored {len(staged)} staged path(s) under the allow-list")
    return EXIT_OK


def commit_message(subject: str, body: list[str]) -> str:
    """The commit message: subject, body lines, and the release-bot sign-off."""
    parts = [subject, "", *body, "", f"Signed-off-by: {BOT_NAME} <{BOT_EMAIL}>"]
    return "\n".join(parts) + "\n"


def push_url(remote_url: str | None) -> str:
    """The push target: ``--remote-url``, else the token URL of ``GITHUB_REPOSITORY``."""
    if remote_url:
        return remote_url
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        raise PublishError("unset environment variable(s): GITHUB_REPOSITORY")
    token = os.environ["GH_TOKEN"]
    return f"https://x-access-token:{token}@github.com/{repository}.git"


def publish(args: argparse.Namespace, cwd: Path) -> int:
    """Commit the changed allowed paths as the release-bot and push; see the module doc."""
    env = require_env()
    label = args.label
    refuse_non_renovate(label, env["GITHUB_REF"])
    validate_paths(label, args.paths, cwd)
    url = push_url(args.remote_url)
    changed = status_paths(cwd, [literal(path) for path in args.paths])
    if not changed and not args.check_strays_when_unchanged:
        print(args.current_message)
        return EXIT_OK
    stray = stray_paths(cwd, args.paths)
    if stray:
        listing = "\n".join(stray)
        raise Refusal(f"{label}: refresh wrote outside the allowed paths:\n{listing}")
    if not changed:
        print(args.current_message)
        return EXIT_OK
    if f"{env['APP_SLUG']}[bot]" != BOT_NAME:
        raise Refusal(f"{label}: expected app '{BOT_NAME}', got '{env['APP_SLUG']}[bot]'")
    expected = git(["rev-parse", "HEAD"], cwd).strip()
    message = Path(env["RUNNER_TEMP"]) / "lock-refresh-commit.txt"
    message.write_text(commit_message(args.subject, args.body_line), encoding="utf-8")
    pathspec = [literal(path) for path in changed]
    git(["add", "--", *pathspec], cwd)
    identity = [f"user.name={BOT_NAME}", f"user.email={BOT_EMAIL}"]
    commit = ["-c", identity[0], "-c", identity[1], "commit", "-q"]
    git([*commit, f"--author={BOT_NAME} <{BOT_EMAIL}>", "-F", str(message), "--", *pathspec], cwd)
    ref = env["GITHUB_REF"]
    try:
        git(["push", "-q", f"--force-with-lease={ref}:{expected}", url, f"HEAD:{ref}"], cwd)
    except PublishError:
        raise PublishError(f"{label}: push refused or failed; branch may have changed") from None
    print(f"{label}: pushed {len(changed)} refreshed path(s) to {env['GITHUB_REF']}")
    return EXIT_OK


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the subcommand line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    guard = sub.add_parser("refuse-non-renovate")
    guard.add_argument("--label", required=True)
    for name in ("stage", "restore"):
        carrier = sub.add_parser(name)
        carrier.add_argument("--label", required=True)
        carrier.add_argument("--artifact-dir", type=Path, required=True)
        carrier.add_argument("paths", nargs="+")
    pub = sub.add_parser("publish")
    pub.add_argument("--label", required=True)
    pub.add_argument("--subject", required=True)
    pub.add_argument("--body-line", action="append", default=[])
    pub.add_argument("--current-message", default="lock outputs are already current")
    pub.add_argument("--remote-url", default=None)
    pub.add_argument("--check-strays-when-unchanged", action="store_true")
    pub.add_argument("paths", nargs="+")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Entry point; every failure maps to a named exit code."""
    args = parse_args(argv)
    try:
        if args.command == "refuse-non-renovate":
            refuse_non_renovate(args.label, require_env_ref())
            return EXIT_OK
        if args.command == "stage":
            return stage(args, Path.cwd())
        if args.command == "restore":
            return restore(args, Path.cwd())
        return publish(args, Path.cwd())
    except Refusal as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (PublishError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return EXIT_ERROR


def require_env_ref() -> str:
    """Return ``GITHUB_REF``, refusing an unset one."""
    ref = os.environ.get("GITHUB_REF", "")
    if not ref:
        raise PublishError("unset environment variable(s): GITHUB_REF")
    return ref


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
