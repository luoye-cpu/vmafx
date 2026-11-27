#!/usr/bin/env python3
"""Replay and refresh the ordered FFmpeg series against stable release tags."""

# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ci import ffmpeg_patch_candidates as candidates
from scripts.lib.safe_subprocess import CommandTimedOut
from scripts.lib.safe_subprocess import run as run_command
from scripts.lib.scratch_program import path_absent, remove_tree

STABLE_TAG = re.compile(r"n(\d+)\.(\d+)(?:\.(\d+))?\Z")
PATCH_NAME = re.compile(r"\d{4}-[A-Za-z0-9_.-]+\.patch\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CACHE_LOCK_SECONDS = 600
CACHE_FETCH_SECONDS = 180
REPLAY_MAINTENANCE = (
    "gc.auto=0",
    "gc.autoDetach=false",
    "maintenance.auto=false",
    "maintenance.autoDetach=false",
)
MIRRORS = ("Dockerfile", "Dockerfile.ffmpeg", "dev/Containerfile", "docker/Dockerfile.node")
# The shared FFmpeg fix series every build applies before ffmpeg-patches/
# (ADR-3143): three pins in build-config.env, one script that fetches,
# verifies and applies it.
SHARED_SERIES_KEYS = (
    "FFMPEG_FIX_SERIES_REPO",
    "FFMPEG_FIX_SERIES_TAG",
    "FFMPEG_FIX_SERIES_SHA256",
)
SHARED_SERIES_SCRIPT = Path(__file__).with_name("ffmpeg-shared-series.sh")
SHARED_SERIES_MAX_DOWNLOADS = 3
SHARED_SERIES_DEFAULT_TIMEOUT_SECONDS = 600
# Mirrors DOWNLOAD_SECONDS default in ffmpeg-shared-series.sh.
SHARED_SERIES_DEFAULT_DOWNLOAD_SECONDS = 120


def shared_series_timeout_seconds(env: dict[str, str] | None = None) -> int:
    """Derive the execution timeout for ffmpeg-shared-series.sh from FFMPEG_FIX_SERIES_DOWNLOAD_SECONDS.

    Returns max(600, N * 2 * S + 120) where N = SHARED_SERIES_MAX_DOWNLOADS and
    S = int(val) when FFMPEG_FIX_SERIES_DOWNLOAD_SECONDS matches ^[1-9][0-9]{0,5}$,
    or S = SHARED_SERIES_DEFAULT_DOWNLOAD_SECONDS when unset or empty (840 s),
    or 600 if invalid (the script then exits 2).
    """
    source = os.environ if env is None else env
    val = source.get("FFMPEG_FIX_SERIES_DOWNLOAD_SECONDS", "")
    if not val:
        s = SHARED_SERIES_DEFAULT_DOWNLOAD_SECONDS
    elif re.match(r"^[1-9][0-9]{0,5}$", val):
        s = int(val)
    else:
        return SHARED_SERIES_DEFAULT_TIMEOUT_SECONDS
    return max(
        SHARED_SERIES_DEFAULT_TIMEOUT_SECONDS,
        SHARED_SERIES_MAX_DOWNLOADS * 2 * s + 120,
    )


# Files the series carries whose source of truth is ffmpeg-patches/src/ (RC4
# WP9): FFmpeg path -> file under ffmpeg-patches/src/. The one patch that adds
# or changes the path gets the source's bytes at replay; --check then sees a
# stale patch as drift and --refresh rewrites it. One patch owns each path.
SOURCES = {
    "libavfilter/vf_vmafx.c": "vf_vmafx.c",
    "libavfilter/vf_vmafx_options.h": "vf_vmafx_options.h",
    "libavfilter/vf_vmafx_pre.c": "vf_vmafx_pre.c",
}


def read_repo_text_bytes(path: Path) -> bytes:
    """Read a repository text file with CRLF normalised to LF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def stable_version(tag: str) -> tuple[int, int, int]:
    """Reject development/RC refs, even when their prefix looks released."""
    match = STABLE_TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"not a stable FFmpeg release tag: {tag}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def latest_release(refs: str) -> str:
    tags = [line.split()[1].removeprefix("refs/tags/") for line in refs.splitlines()]
    stable = [tag for tag in tags if STABLE_TAG.fullmatch(tag)]
    if not stable:
        raise ValueError("upstream returned no stable FFmpeg release tags")
    return max(stable, key=stable_version)


def configuration(repo: Path) -> tuple[str, str, str]:
    """Return the remote, the release tag and the upstream commit the tag must name."""
    values = {}
    for line in (repo / "build-config.env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"FFMPEG_REMOTE", "FFMPEG_TAG", "FFMPEG_COMMIT"}:
            parts = shlex.split(value, comments=True)
            if len(parts) != 1 or key in values:
                raise ValueError(f"invalid or duplicate build-config.env {key}")
            values[key] = parts[0]
    remote, tag, commit = values["FFMPEG_REMOTE"], values["FFMPEG_TAG"], values["FFMPEG_COMMIT"]
    stable_version(tag)
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"FFMPEG_COMMIT is not a full lowercase commit id: {commit}")
    return remote, tag, commit


def shared_series_tag(repo: Path) -> str | None:
    """The pinned shared fix series, or None when build-config.env pins none.

    A partial pin is an error: the series is applied with all three keys or
    not at all.
    """
    text = (repo / "build-config.env").read_text()
    present = [key for key in SHARED_SERIES_KEYS if re.search(rf"(?m)^{key}=", text)]
    if not present:
        return None
    if len(present) != len(SHARED_SERIES_KEYS):
        missing = sorted(set(SHARED_SERIES_KEYS) - set(present))
        raise ValueError(f"build-config.env pins the shared FFmpeg series without {missing}")
    match = re.search(r'(?m)^FFMPEG_FIX_SERIES_TAG="?([^"\s#]+)"?', text)
    if match is None:
        raise ValueError("FFMPEG_FIX_SERIES_TAG has no value")
    return match.group(1)


def series(repo: Path) -> list[str]:
    directory = repo / "ffmpeg-patches"
    entries = [
        line.partition("#")[0].strip()
        for line in (directory / "series.txt").read_text().splitlines()
    ]
    entries = [entry for entry in entries if entry]
    if not entries or len(entries) != len(set(entries)):
        raise ValueError("series.txt must contain a nonempty, unique patch list")
    for entry in entries:
        path = directory / entry
        if not PATCH_NAME.fullmatch(entry) or not path.is_file() or path.is_symlink():
            raise ValueError(f"invalid or missing series entry: {entry}")
    actual = {path.name for path in directory.glob("*.patch")}
    if actual != set(entries):
        raise ValueError(f"patches outside series.txt: {sorted(actual - set(entries))}")
    return entries


def isolated_git_environment() -> dict[str, str]:
    """Caller environment without GIT_* (hooks export them; -C cannot override)."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        LC_ALL="C",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    return environment


class Replay:
    """Own only a disposable checkout; never clean an existing workspace."""

    def __init__(self, checkout: Path, output: Path):
        self.checkout = checkout
        self.output = output
        # Hooks export repository/index/object-store variables. Even `git -C`
        # cannot override them: none may escape into the disposable checkout.
        self.environment = isolated_git_environment()
        # A hook in the caller's Git configuration must not execute here.
        git = shutil.which("git")
        if git is None:
            raise RuntimeError("git is required to replay the FFmpeg patch stack")
        self.prefix = [
            str(Path(git).resolve(strict=True)),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=VMAFx patch refresh",
            "-c",
            "user.email=patch-refresh@localhost",
        ]
        # Detached Git maintenance can race checkout cleanup and cause ENOTEMPTY.
        for setting in REPLAY_MAINTENANCE:
            self.prefix.extend(("-c", setting))

    def git(self, *args: str) -> str:
        return self.run_git(["-C", str(self.checkout)], *args)

    def cache_git(self, cache: Path, *args: str, timeout: int = 180) -> str:
        return self.run_git(["--git-dir", str(cache)], *args, timeout=timeout)

    def run_git(self, location: list[str], *args: str, timeout: int = 180) -> str:
        command = [*self.prefix, *location, *args]
        # Fixed Git executable/subcommands and argument lists; no shell expansion.
        result = run_command(
            command,
            allowed_executables=(self.prefix[0],),
            env=self.environment,
            text=True,
            capture_output=True,
            timeout_seconds=timeout,
            max_output_bytes=16 * 1_048_576,
        )
        with (self.output / "replay.log").open("a") as log:
            log.write(f"$ git {' '.join(args)}\n{result.stdout}{result.stderr}")
        if result.returncode:
            if args[0] in {"am", "rebase"}:
                diff = run_command(
                    [*self.prefix, "-C", str(self.checkout), "diff", "--binary"],
                    allowed_executables=(self.prefix[0],),
                    env=self.environment,
                    text=True,
                    capture_output=True,
                    timeout_seconds=30,
                    max_output_bytes=16 * 1_048_576,
                )
                (self.output / "conflict.diff").write_text(diff.stdout)
            raise RuntimeError(
                f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def fetch(self, url: str, ref: str) -> str:
        self.git("fetch", "--no-tags", "--depth=1", "--end-of-options", url, ref)
        return self.git("rev-parse", "FETCH_HEAD^{commit}").strip()

    def apply_shared_series(self, repo: Path) -> None:
        """Apply the pinned shared fix series to the checkout, as every build does."""
        self.shared_command(repo, ["apply", "--method", "am", str(self.checkout)])

    def shared_command(self, repo: Path, args: list[str], timeout: float | None = None) -> None:
        """Run the single shared-series verifier with bounded diagnostics."""
        bash = shutil.which("bash")
        if sys.platform == "win32":
            git_bash = (
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/usr/bin/bash.exe"
            )
            if git_bash.is_file():
                bash = str(git_bash)
        if bash is None:
            raise RuntimeError("bash is required to apply the shared FFmpeg fix series")
        # The shell helper launches its own Git, outside the command prefix.
        for setting in REPLAY_MAINTENANCE:
            key, value = setting.split("=", 1)
            self.git("config", "--local", "--replace-all", key, value)
        result = run_command(
            [bash, str(SHARED_SERIES_SCRIPT), *args],
            allowed_executables=(bash,),
            env={**self.environment, "BUILD_CONFIG": str(repo / "build-config.env")},
            text=True,
            capture_output=True,
            timeout_seconds=timeout or shared_series_timeout_seconds(self.environment),
            max_output_bytes=16 * 1_048_576,
        )
        with (self.output / "replay.log").open("a") as log:
            log.write(f"$ {SHARED_SERIES_SCRIPT.name} {args[0]}\n{result.stdout}{result.stderr}")
        if result.returncode:
            raise RuntimeError(
                f"shared FFmpeg fix series failed: {result.stderr.strip() or result.stdout.strip()}"
            )


def cache_root() -> Path:
    """Per-user cache directory, chosen as the other repo tools choose theirs."""
    explicit = os.environ.get("XDG_CACHE_HOME")
    if explicit:
        base = Path(explicit)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path.home() / ".cache"
    return base / "vmafx" / "ffmpeg-patch-stack"


def _lock_once(handle: IO[bytes], acquire: bool) -> None:
    """Take (or drop) the byte-range lock without blocking; raise OSError when held."""
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415 - platform-specific

        handle.seek(0)
        mode = msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), mode, 1)  # type: ignore[attr-defined]
    else:
        import fcntl  # noqa: PLC0415 - platform-specific

        fcntl.flock(handle.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if acquire else fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Cross-process lock with a bounded wait; the cache has one writer at a time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        for _ in range(CACHE_LOCK_SECONDS * 10):
            try:
                _lock_once(handle, True)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"timed out waiting for the cache lock {path}")
        try:
            yield
        finally:
            _lock_once(handle, False)


class SourceCache:
    """Bare repository of fetched release commits, one per remote, keyed by tag.

    A tag is trusted only when its commit equals the pinned FFMPEG_COMMIT; a
    mismatch is an error, never a silent use. Writers hold a lock, and a ref
    moves only after its commit was fetched and verified, so a reader never
    sees a half-written entry.
    """

    def __init__(self, replay: Replay, root: Path, remote: str):
        key = hashlib.sha256(remote.encode()).hexdigest()[:16]
        self.replay = replay
        self.directory = root / key
        self.repo = self.directory / "source.git"
        self.lock = self.directory / "source.lock"
        self.remote = remote

    @staticmethod
    def ref(tag: str) -> str:
        return f"refs/vmafx/tags/{tag}"

    def healthy(self) -> bool:
        try:
            return (
                self.replay.cache_git(self.repo, "rev-parse", "--is-bare-repository").strip()
                == "true"
            )
        except RuntimeError:
            return False

    def reset(self) -> None:
        try:
            remove_tree(self.repo)
        except FileNotFoundError as error:
            if error.filename not in (self.repo, str(self.repo)) or not path_absent(self.repo):
                raise
        self.repo.parent.mkdir(parents=True, exist_ok=True)
        self.replay.run_git(
            ["-C", str(self.repo.parent)], "init", "--quiet", "--bare", "source.git"
        )

    def cached(self, tag: str) -> str | None:
        try:
            return self.replay.cache_git(
                self.repo, "rev-parse", "--verify", "--quiet", f"{self.ref(tag)}^{{commit}}"
            ).strip()
        except RuntimeError:
            return None

    def populate(self, tag: str, expected: str | None) -> str:
        incoming = f"refs/vmafx/incoming/{tag}"
        self.replay.cache_git(
            self.repo,
            "fetch",
            "--no-tags",
            "--depth=1",
            "--end-of-options",
            self.remote,
            f"+refs/tags/{tag}:{incoming}",
            timeout=CACHE_FETCH_SECONDS,
        )
        commit = self.replay.cache_git(self.repo, "rev-parse", f"{incoming}^{{commit}}").strip()
        try:
            if expected is not None and commit != expected:
                raise ValueError(
                    f"{tag} on {self.remote} names commit {commit}, the pinned FFMPEG_COMMIT is "
                    f"{expected}; the tag moved upstream or the pin is stale"
                )
            self.replay.cache_git(self.repo, "update-ref", self.ref(tag), commit)
        finally:
            self.replay.cache_git(self.repo, "update-ref", "-d", incoming)
        return commit

    def obtain(self, tag: str, expected: str | None) -> tuple[str, str]:
        """Return (commit, origin) with the commit present and verified in the cache."""
        with exclusive_lock(self.lock):
            if not self.healthy():
                self.reset()
            commit = self.cached(tag) if expected is not None else None
            if commit is None:
                label = "fetched into cache" if expected is not None else "fetched (unpinned)"
                return self.populate(tag, expected), label
            if commit != expected:
                raise ValueError(
                    f"cached {tag} is commit {commit}, the pinned FFMPEG_COMMIT is {expected}; "
                    f"clear {self.directory} if the pin was bumped on purpose"
                )
            return commit, "cache hit"

    def fetch(self, tag: str, expected: str | None) -> tuple[str, str]:
        """Fetch the tag into the replay checkout from the cache, healing a corrupt entry."""
        commit, origin = self.obtain(tag, expected)
        url = self.repo.resolve().as_uri()
        try:
            fetched = self.replay.fetch(url, self.ref(tag))
        except RuntimeError:
            if origin != "cache hit":
                raise
            with exclusive_lock(self.lock):
                self.reset()
            commit, origin = self.obtain(tag, expected)
            origin = "fetched into cache (corrupt entry dropped)"
            fetched = self.replay.fetch(url, self.ref(tag))
        if fetched != commit:
            raise RuntimeError(f"cache returned {fetched} for {tag}, expected {commit}")
        return commit, origin


def sync_sources(repo: Path, replay: Replay, name: str, owners: dict[str, str]) -> None:
    """Give the patch just applied the bytes of every source file it touches."""
    touched = replay.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
    synced = []
    for path in touched:
        source = SOURCES.get(path)
        if source is None:
            continue
        if path in owners:
            raise ValueError(f"{path}: changed by {owners[path]} and {name}; one patch owns it")
        owners[path] = name
        content = read_repo_text_bytes(repo / "ffmpeg-patches" / "src" / source)
        target = replay.checkout / path
        if target.read_bytes() != content:
            target.write_bytes(content)
            synced.append(path)
    if synced:
        replay.git("add", "--", *synced)
        replay.git("commit", "--quiet", "--amend", "--no-edit")


def check_sources_owned(repo: Path, owners: dict[str, str]) -> None:
    """Every source file present under ffmpeg-patches/src/ belongs to a patch."""
    for path, source in SOURCES.items():
        if (repo / "ffmpeg-patches" / "src" / source).is_file() and path not in owners:
            raise ValueError(f"ffmpeg-patches/src/{source}: no patch in series.txt adds {path}")


def mirrors(repo: Path, remote: str, tag: str) -> dict[Path, bytes]:
    """Generate the unavoidable Docker ARG defaults from the shared owner."""
    changed = {}
    for name in MIRRORS:
        path = repo / name
        original = read_repo_text_bytes(path).decode("utf-8")
        updated = original
        for key, value in {"FFMPEG_TAG": tag, "FFMPEG_REMOTE": remote}.items():
            def literal_arg(_match: re.Match[str], line: str = f"ARG {key}={value}") -> str:
                return line

            updated, count = re.subn(rf"(?m)^ARG {key}=\S+$", literal_arg, updated)
            if count != 1:
                raise ValueError(f"expected one {key} mirror in {name}, found {count}")
        changed[path] = updated.encode()
    return changed


JOURNAL_NAME = "ffmpeg-refresh-journal.json"

README_CLAIM_PATTERN = re.compile(
    r"(?m)^> `[^`]+` checkout \(cumulative `git am --3way`\), NOT a per-patch\r?\n"
    r"> `git apply --check`\. The latter rejects `0002\+` because they\r?\n"
    r"> reference cumulative-state hunks that don't exist in pristine `[^`]+`\.\r?\n"
    r"> The (?:(?:\d+) patches of the shared fix series `[^`]+` and the (?:\d+)(?:[ ]+|\r?\n> )patches|\d+ patches) here replay cumulatively on upstream commit\r?\n"
    r"> `[0-9a-f]{40,}`(?![0-9a-f]); the resulting tree is\r?\n"
    r"> `[0-9a-f]{40,}`(?![0-9a-f])(?:\r?\n|\Z)"
)


def readme_claim(repo: Path, receipt: Receipt) -> dict[Path, bytes]:
    """Mirror the replayed patch counts, checkout tag, and tree hash into ffmpeg-patches/README.md."""
    path = repo / "ffmpeg-patches" / "README.md"
    if not path.is_file():
        raise ValueError("expected one replay claim in ffmpeg-patches/README.md, file missing")
    with path.open("r", encoding="utf-8", newline="") as stream:
        original = stream.read()
    ending = "\r\n" if "\r\n" in original else "\n"
    tag = receipt["tag"]
    gate = (
        f"> `{tag}` checkout (cumulative `git am --3way`), NOT a per-patch\n"
        f"> `git apply --check`. The latter rejects `0002+` because they\n"
        f"> reference cumulative-state hunks that don't exist in pristine `{tag}`.\n"
    )
    shared_series = receipt.get("shared_series", "none")
    if shared_series and shared_series != "none":
        claim_body = (
            f"{gate}"
            f"> The {receipt['shared_patch_count']} patches of the shared fix series `{shared_series}` and the {receipt['patch_count']}\n"
            f"> patches here replay cumulatively on upstream commit\n"
            f"> `{receipt['upstream_commit']}`; the resulting tree is\n"
            f"> `{receipt['patched_tree']}`\n"
        )
    else:
        claim_body = (
            f"{gate}"
            f"> The {receipt['patch_count']} patches here replay cumulatively on upstream commit\n"
            f"> `{receipt['upstream_commit']}`; the resulting tree is\n"
            f"> `{receipt['patched_tree']}`\n"
        )
    claim = claim_body.replace("\n", "\r\n") if ending == "\r\n" else claim_body
    updated, count = README_CLAIM_PATTERN.subn(lambda _match: claim, original)
    if count != 1:
        raise ValueError(f"expected one replay claim in ffmpeg-patches/README.md, found {count}")
    return {path: updated.encode("utf-8")}


class NotAGitCheckout(RuntimeError):
    """The directory is not a Git checkout."""


def repo_git_dir(repo: Path) -> Path:
    """Return the Git directory of the checkout (a linked worktree has its own)."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to locate the refresh lock and journal")
    result = run_command(
        [git, "-C", str(repo), "rev-parse", "--absolute-git-dir"],
        allowed_executables=(git,),
        env=isolated_git_environment(),
        capture_output=True,
        text=True,
        timeout_seconds=30,
    )
    if result.returncode != 0:
        raise NotAGitCheckout(f"{repo} is not a Git checkout: {result.stderr.strip()}")
    return Path(result.stdout.strip())


@contextmanager
def maintain_lock(repo: Path, enabled: bool) -> Iterator[None]:
    """Hold an exclusive repository-wide lock for refresh runs."""
    if not enabled:
        recover_journal(repo, False)
        yield
        return
    lockpath = repo_git_dir(repo) / "ffmpeg-patch-stack.lock"
    try:
        fd = os.open(str(lockpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = ""
        with suppress(OSError):
            pid = lockpath.read_text(encoding="utf-8").strip()
        raise RuntimeError(
            f"refresh lock {lockpath} is held by process {pid or 'unknown'}; "
            "if that process is no longer running, delete the lock file and retry the refresh"
        ) from None
    with os.fdopen(fd, "w") as stream:
        stream.write(f"{os.getpid()}\n")
    try:
        recover_journal(repo, True)
        yield
    finally:
        with suppress(OSError):
            lockpath.unlink(missing_ok=True)


def write_journal_atomically(journal: Path, data: list[dict[str, str]]) -> None:
    """Write journal entries atomically using a temporary file and sync."""
    handle, name = tempfile.mkstemp(prefix=f".{JOURNAL_NAME}-", dir=journal.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(journal)
    finally:
        temporary.unlink(missing_ok=True)


def _journal_entry(
    target: Path,
    candidate: Path,
    backup: Path,
    original_bytes: bytes,
    candidate_bytes: bytes,
) -> dict[str, str]:
    """Build a serialisable journal record for a single target replacement."""
    return {
        "target": str(target),
        "temp": str(candidate),
        "backup": str(backup),
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
    }


def _verify_entries(entries: list[dict[str, str]], journal: Path) -> list[tuple[Path, Path, bool]]:
    """Verify target state and backups for all journal entries without disk mutations.

    Returns a list of (target, backup, needs_restore) tuples.
    """
    verified: list[tuple[Path, Path, bool]] = []
    for entry in entries:
        target = Path(entry["target"])
        backup = Path(entry["backup"])
        if not target.is_file():
            raise ValueError(
                f"{target} changed since the interrupted refresh; "
                f"resolve by hand, then delete {journal}"
            )
        target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        orig_hash = entry["original_sha256"]
        cand_hash = entry["candidate_sha256"]
        if target_hash not in (orig_hash, cand_hash):
            raise ValueError(
                f"{target} changed since the interrupted refresh; "
                f"resolve by hand, then delete {journal}"
            )
        needs_restore = target_hash == cand_hash
        if needs_restore and not backup.is_file():
            raise ValueError(
                f"{target} changed since the interrupted refresh; "
                f"resolve by hand, then delete {journal}"
            )
        verified.append((target, backup, needs_restore))
    return verified


def _restore_entries(
    entries: list[dict[str, str]],
    verified: list[tuple[Path, Path, bool]],
    journal: Path,
) -> None:
    """Restore original files, remove residual journal temporaries, and unlink journal."""
    for target, backup, needs_restore in verified:
        if needs_restore:
            backup.replace(target)
    for entry in entries:
        Path(entry["temp"]).unlink(missing_ok=True)
        Path(entry["backup"]).unlink(missing_ok=True)
    journal.unlink()


def recover_journal(repo: Path, refresh: bool) -> None:
    """Roll back an interrupted refresh to restore originals before anything else runs."""
    try:
        git_dir = repo_git_dir(repo)
    except NotAGitCheckout:
        if not refresh:
            return
        raise
    journal = git_dir / JOURNAL_NAME
    if not journal.exists():
        return
    if not refresh:
        raise ValueError(
            f"interrupted refresh journal {journal} found; "
            "run python3 scripts/ci/ffmpeg_patch_stack.py --refresh to finish it"
        )
    entries = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"refresh journal {journal} is not a list")
    verified = _verify_entries(entries, journal)
    _restore_entries(entries, verified, journal)


def _stage_file(path: Path, content: bytes, role: str) -> Path:
    """Create a temporary staged file next to path with identical permissions."""
    handle, name = tempfile.mkstemp(prefix=f".ffmpeg-refresh-{path.name}-{role}-", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(handle, "wb") as stream:
        stream.write(content)
    temporary.chmod(path.stat().st_mode)
    return temporary


def _rollback_files(written: list[Path], backups: dict[Path, Path]) -> list[str]:
    """Restore replaced files in reverse order and collect failed restores."""
    failures = []
    for path in reversed(written):
        try:
            backups[path].replace(path)
        except OSError:
            failures.append(f"{path} <- {backups[path]}")
    return failures


def replace_files(updates: dict[Path, bytes], repo: Path) -> None:
    """Prepare every file first; record journal, perform renames, delete journal."""
    before = {path: path.read_bytes() for path in updates}
    staged = {}
    backups = {}
    temporaries = []
    written = []
    preserve_backups = False
    journal = repo_git_dir(repo) / JOURNAL_NAME

    try:
        for path, content in updates.items():
            if before[path] == content:
                continue
            if path.is_symlink():
                raise ValueError(f"refusing to replace a symlink: {path}")
            staged[path] = _stage_file(path, content, "candidate")
            temporaries.append(staged[path])
            backups[path] = _stage_file(path, before[path], "original")
            temporaries.append(backups[path])
        if staged:
            journal_data = [
                _journal_entry(p, staged[p], backups[p], before[p], updates[p]) for p in staged
            ]
            write_journal_atomically(journal, journal_data)
        for path, temporary in staged.items():
            temporary.replace(path)
            written.append(path)
        journal.unlink(missing_ok=True)
    except BaseException as error:
        # Roll back even when Ctrl-C interrupts a multi-file replacement.
        # Keep originals if recovery itself is interrupted a second time.
        preserve_backups = True
        failures = _rollback_files(written, backups)
        if failures:
            raise RuntimeError(
                "refresh rollback failed; original backups retained: " + "; ".join(failures)
            ) from error
        journal.unlink(missing_ok=True)
        preserve_backups = False
        raise
    finally:
        for temporary in temporaries:
            if preserve_backups and temporary in backups.values():
                continue
            temporary.unlink(missing_ok=True)


class Receipt(TypedDict, total=False):
    shared_series: str
    shared_patch_count: int
    remote: str
    configured_tag: str
    patch_count: int
    applying: str
    tag: str
    upstream_commit: str
    pinned_commit: str
    source: str
    target_source: str
    cache: str
    patched_tree: str
    kept_tree: str
    changed: list[str]
    status: str
    error: str
    cleanup_error: str
    reason: str
    reason_code: str
    candidate_shared: candidates.Candidate
    verified_candidates: list[candidates.Candidate]


def select_target_tag(replay: Replay, remote: str, current_tag: str, latest: bool) -> str:
    """Resolve the requested stable tag without permitting a downgrade."""
    if not latest:
        return current_tag
    target_tag = latest_release(replay.git("ls-remote", "--tags", "--refs", remote, "refs/tags/n*"))
    if stable_version(target_tag) < stable_version(current_tag):
        raise ValueError("upstream latest stable release would downgrade the maintained tag")
    return target_tag


def format_patch_updates(
    repo: Path, output: Path, replay: Replay, names: list[str], commits: list[str]
) -> dict[Path, bytes]:
    """Render the replayed commits into candidate patch bytes."""
    updates = {}
    candidate = output / "patches"
    candidate.mkdir(exist_ok=True)
    for name, commit in zip(names, commits, strict=True):
        patch = replay.git(
            "format-patch",
            "-1",
            "--stdout",
            "--zero-commit",
            "--no-signature",
            "--no-numbered",
            "--full-index",
            commit,
        ).encode()
        updates[repo / "ffmpeg-patches" / name] = patch
        (candidate / name).write_bytes(patch)
    return updates


def pinned_config(text: str, tag: str, commit: str) -> str:
    """Move the tag and the commit it names together."""
    text = re.sub(r"(?m)^FFMPEG_TAG=.*$", lambda _match: f'FFMPEG_TAG="{tag}"', text)
    return re.sub(r"(?m)^FFMPEG_COMMIT=.*$", lambda _match: f'FFMPEG_COMMIT="{commit}"', text)


def apply_stack(
    repo: Path,
    replay: Replay,
    base: str,
    names: list[str],
    receipt: Receipt,
    shared_config: Path | None = None,
) -> tuple[str | None, int]:
    """Apply the shared fix series, then ours, at *base*; return its tag and patch count."""
    replay.git("switch", "--quiet", "--detach", base)
    config_repo = shared_config or repo
    shared = shared_series_tag(config_repo)
    if shared is not None:
        receipt["applying"] = f"shared fix series {shared}"
        replay.apply_shared_series(config_repo)
    shared_count = len(replay.git("rev-list", f"{base}..HEAD").splitlines())
    receipt.update({"shared_series": shared or "none", "shared_patch_count": shared_count})
    owners: dict[str, str] = {}
    for name in names:
        receipt["applying"] = name
        replay.git("am", "--3way", str(repo / "ffmpeg-patches" / name))
        sync_sources(repo, replay, name, owners)
    check_sources_owned(repo, owners)
    return shared, shared_count


def unpinned_release(shared: str, target_tag: str, current_tag: str, output: Path) -> str:
    """Why a newer FFmpeg release cannot be taken while the series targets the old one."""
    return (
        f"FFmpeg {target_tag} is released, but the pinned shared fix series {shared} "
        f"targets {current_tag}; the rebased candidates are in {output / 'patches'}. "
        f"Pin a series release for {target_tag} (FFMPEG_FIX_SERIES_TAG and "
        "FFMPEG_FIX_SERIES_SHA256) together with FFMPEG_TAG and FFMPEG_COMMIT, "
        "then run --refresh"
    )


def keep_patched_tree(checkout: Path, destination: Path) -> None:
    """Copy the patched FFmpeg checkout, without its Git data, to a new directory.

    The integrations tidy lane compiles and measures the fork's FFmpeg sources
    in this tree (ADR-2796 lane list, issue #2860); it refuses an existing
    destination rather than mixing two trees.
    """
    if destination.exists():
        raise ValueError(f"{destination}: --keep-tree destination exists")
    shutil.copytree(checkout, destination, symlinks=True, ignore=shutil.ignore_patterns(".git"))


def proposed_patch(repo: Path, updates: dict[Path, bytes]) -> bytes:
    """Render text-only candidate updates as a reviewable unified diff."""
    chunks: list[str] = []
    for path, data in updates.items():
        # Keep exact worktree context so plain git apply also works in CRLF checkouts.
        original = path.read_bytes() if path.exists() else b""
        candidate = data.replace(b"\r\n", b"\n")
        if original.replace(b"\r\n", b"\n") == candidate:
            continue
        try:
            before = original.decode("utf-8").splitlines(keepends=True)
            after = candidate.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError as error:
            raise ValueError(f"artifact-only proposal cannot render binary file: {path}") from error
        diff = difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{path.relative_to(repo).as_posix()}" if path.exists() else "/dev/null",
            tofile=f"b/{path.relative_to(repo).as_posix()}",
        )
        for line in diff:
            chunks.append(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            )
    return "".join(chunks).encode("utf-8")


class PendingWrites(TypedDict):
    """Files a successful replay wants written after its checkout is gone."""

    updates: dict[Path, bytes]
    refresh: bool
    artifact_only: bool


def settle_updates(
    repo: Path,
    updates: dict[Path, bytes],
    refresh: bool,
    receipt: Receipt,
    artifact_only: bool = False,
    output: Path | None = None,
) -> None:
    """Write maintained files, check for drift, or retain an artifact-only proposal."""
    artifact_updates = {
        path: data
        for path, data in updates.items()
        if path.relative_to(repo).as_posix().startswith("ffmpeg-patches/")
    }
    selected = artifact_updates if artifact_only else updates
    changed = [
        str(path.relative_to(repo))
        for path, data in selected.items()
        if not path.exists() or read_repo_text_bytes(path) != data.replace(b"\r\n", b"\n")
    ]
    if artifact_only:
        status = "proposed" if changed else "no-op"
    else:
        status = "refreshed" if refresh else "checked"
    receipt.update({"changed": changed, "status": status})
    if artifact_only:
        if output is None:
            raise ValueError("artifact-only mode requires an output directory")
        (output / "proposed-refresh.patch").write_bytes(proposed_patch(repo, artifact_updates))
    elif refresh:
        replace_files(updates, repo=repo)
    elif changed:
        raise ValueError(
            "patch/configuration drift; run python3 scripts/ci/ffmpeg_patch_stack.py --refresh"
        )


def configured_series(repo: Path, receipt: Receipt) -> tuple[str, str, str, list[str]]:
    """Read the pinned remote, tag, commit and patch series into the receipt."""
    remote, current_tag, pinned = configuration(repo)
    names = series(repo)
    receipt.update({"remote": remote, "configured_tag": current_tag, "patch_count": len(names)})
    receipt["pinned_commit"] = pinned
    return remote, current_tag, pinned, names


@contextmanager
def replay_checkout(receipt: Receipt) -> Iterator[Path]:
    """Clean the checkout while preserving any replay exception already in flight."""
    temporary = tempfile.TemporaryDirectory(prefix="vmafx-ffmpeg-")
    try:
        yield Path(temporary.name)
    except BaseException as error:
        try:
            temporary.cleanup()
        except Exception as cleanup_error:
            receipt["cleanup_error"] = str(cleanup_error)
            error.add_note(f"Checkout cleanup failed: {cleanup_error}")
        raise
    else:
        temporary.cleanup()


def write_receipt(output: Path, receipt: Receipt) -> None:
    """Write the machine-readable result alongside the replay log."""
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def record_failure(output: Path, receipt: Receipt, error: BaseException) -> None:
    """Record the primary error even if writing its receipt also fails."""
    receipt.update({"status": "failed", "error": str(error)})
    (output / "proposed-refresh.patch").unlink(missing_ok=True)
    try:
        write_receipt(output, receipt)
    except OSError as receipt_error:
        error.add_note(f"Could not write failure receipt: {receipt_error}")


def scan_noop(receipt: Receipt, code: str, reason: str) -> PendingWrites:
    receipt.update({"status": "no-op", "changed": [], "reason_code": code, "reason": reason})
    return {"updates": {}, "refresh": False, "artifact_only": True}


def scan_candidate(
    replay: Replay, repo: Path, target_tag: str, target: str, receipt: Receipt
) -> Path | None:
    """Inspect canonical signed releases; verification errors never mean incompatibility."""
    pins = candidates.pin_values(repo)
    deadline = time.monotonic() + candidates.DISCOVERY_SECONDS
    tags = candidates.release_tags(pins["FFMPEG_FIX_SERIES_REPO"])
    if len(tags) > candidates.RELEASE_LIMIT:
        raise ValueError("shared-series release limit exceeded")
    receipt["verified_candidates"] = []
    directory = replay.checkout / ".git" / "candidate"
    for tag in tags:
        pins["FFMPEG_FIX_SERIES_TAG"] = tag
        candidates.write_config(directory, pins, target_tag, target)
        unpacked = directory / tag
        replay.shared_command(
            directory, ["inspect", str(unpacked)], candidates.remaining_seconds(deadline)
        )
        candidate = candidates.metadata(unpacked)
        if candidate["tag"] != tag:
            raise ValueError("inspected shared-series tag differs from requested release")
        receipt["verified_candidates"].append(candidate)
        if (candidate["base_tag"], candidate["base_commit"]) == (target_tag, target):
            receipt["candidate_shared"] = candidate
            pins["FFMPEG_FIX_SERIES_SHA256"] = candidate["sha256"]
            candidates.write_config(directory, pins, target_tag, target)
            return directory
    return None


def scan_release(
    repo: Path,
    output: Path,
    replay: Replay,
    cache: SourceCache,
    names: list[str],
    receipt: Receipt,
    keep_tree: Path | None,
) -> PendingWrites:
    target_tag, current_tag = receipt["tag"], receipt["configured_tag"]
    if target_tag == current_tag:
        return scan_noop(receipt, "no-new-release", "no newer stable FFmpeg release candidate")
    target, receipt["target_source"] = cache.fetch(target_tag, None)
    receipt["upstream_commit"] = target
    candidate_config = scan_candidate(replay, repo, target_tag, target, receipt)
    if candidate_config is None:
        return scan_noop(
            receipt,
            "no-compatible-series",
            f"FFmpeg {target_tag} ({target}) has no verified compatible shared-series release",
        )
    _, shared_count = apply_stack(repo, replay, target, names, receipt, candidate_config)
    receipt["patched_tree"] = replay.git("rev-parse", "HEAD^{tree}").strip()
    commits = replay.git("rev-list", "--reverse", f"{target}..HEAD").splitlines()
    if len(commits) != shared_count + len(names):
        raise ValueError("candidate replay changed the number of patches")
    updates = format_patch_updates(repo, output, replay, names, commits[shared_count:])
    updates.update(readme_claim(repo, receipt))
    if keep_tree is not None:
        keep_patched_tree(replay.checkout, keep_tree)
        receipt["kept_tree"] = str(keep_tree)
    return {"updates": updates, "refresh": False, "artifact_only": True}


def replay_release(
    repo: Path,
    output: Path,
    replay: Replay,
    cache: SourceCache,
    names: list[str],
    receipt: Receipt,
    refresh: bool,
    keep_tree: Path | None,
    artifact_only: bool,
) -> PendingWrites:
    base, receipt["source"] = cache.fetch(receipt["configured_tag"], receipt["pinned_commit"])
    shared, shared_count = apply_stack(repo, replay, base, names, receipt)
    receipt["upstream_commit"] = base
    if artifact_only:
        return scan_release(repo, output, replay, cache, names, receipt, keep_tree)
    target = base
    if receipt["tag"] != receipt["configured_tag"]:
        target, receipt["target_source"] = cache.fetch(receipt["tag"], None)
        replay.git("rebase", "--onto", target, base)
    receipt.update(
        {"upstream_commit": target, "patched_tree": replay.git("rev-parse", "HEAD^{tree}").strip()}
    )
    if keep_tree is not None:
        keep_patched_tree(replay.checkout, keep_tree)
        receipt["kept_tree"] = str(keep_tree)
    commits = replay.git("rev-list", "--reverse", f"{target}..HEAD").splitlines()
    if len(commits) != shared_count + len(names):
        raise ValueError("refresh changed the number of patches; review upstreamed/empty patches")
    updates = format_patch_updates(repo, output, replay, names, commits[shared_count:])
    if shared is not None and receipt["tag"] != receipt["configured_tag"]:
        raise ValueError(
            unpinned_release(shared, receipt["tag"], receipt["configured_tag"], output)
        )
    updates.update(mirrors(repo, receipt["remote"], receipt["tag"]))
    config = repo / "build-config.env"
    updates[config] = pinned_config(
        read_repo_text_bytes(config).decode("utf-8"), receipt["tag"], target
    ).encode("utf-8")
    updates.update(readme_claim(repo, receipt))
    return {"updates": updates, "refresh": refresh, "artifact_only": False}


def maintain(
    repo: Path,
    output: Path,
    refresh: bool,
    latest: bool,
    cache_dir: Path | None = None,
    keep_tree: Path | None = None,
    artifact_only: bool = False,
) -> Receipt:
    """Replay the stack, apply requested updates and retain diagnostics."""
    receipt: Receipt = {}
    output.mkdir(parents=True, exist_ok=True)
    (output / "replay.log").write_text("")
    (output / "proposed-refresh.patch").unlink(missing_ok=True)
    try:
        if artifact_only and os.environ.get("FFMPEG_FIX_SERIES_VERIFY") != "cosign":
            raise ValueError("--scan-latest requires FFMPEG_FIX_SERIES_VERIFY=cosign")
        with maintain_lock(repo, refresh):
            remote, current_tag, _pinned, names = configured_series(repo, receipt)
            if artifact_only:
                candidates.pin_values(repo)
            with replay_checkout(receipt) as checkout:
                replay = Replay(checkout, output)
                replay.git("init", "--quiet")
                target_tag = select_target_tag(replay, remote, current_tag, latest)
                cache = SourceCache(replay, cache_dir or cache_root(), remote)
                receipt["cache"] = str(cache.repo)
                receipt["tag"] = target_tag
                pending = replay_release(
                    repo, output, replay, cache, names, receipt, refresh, keep_tree, artifact_only
                )
            # Apply only after the checkout (and its cleanup) has succeeded; a
            # failed cleanup therefore leaves the maintained files untouched.
            settle_updates(
                repo, pending["updates"], pending["refresh"], receipt,
                pending["artifact_only"], output,
            )
    except BaseException as error:
        record_failure(output, receipt, error)
        raise
    write_receipt(output, receipt)
    return receipt


def parse_args() -> argparse.Namespace:
    """Parse the command line and validate the refresh mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="fail on stale patch or configuration mirrors (default)",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="regenerate patches and configuration mirrors after complete replay",
    )
    mode.add_argument(
        "--scan-latest",
        action="store_true",
        help="scan the latest stable release and write an artifact-only proposal",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="refresh onto the latest stable release, for scheduled updates",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="FFmpeg source cache (default: <user cache>/vmafx/ffmpeg-patch-stack)",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="retain logs, candidate patches and exact upstream receipt"
    )
    parser.add_argument(
        "--keep-tree",
        type=Path,
        help="copy the patched FFmpeg source tree (no .git) to this new directory",
    )
    args = parser.parse_args()
    if args.latest and not (args.refresh or args.scan_latest):
        parser.error("--latest requires --refresh or --scan-latest")
    if args.scan_latest and not args.latest:
        parser.error("--scan-latest requires --latest")
    if args.scan_latest and args.output_dir is None:
        parser.error("--scan-latest requires --output-dir to retain its receipt and proposal")
    return args


def main() -> int:
    """Run the requested check or refresh and report its outcome."""
    args = parse_args()
    repo = Path(__file__).resolve().parents[2]
    output: Path = args.output_dir or Path(tempfile.mkdtemp(prefix="vmafx-ffmpeg-report-"))
    try:
        result = maintain(
            repo,
            output.resolve(),
            args.refresh,
            args.latest or args.scan_latest,
            args.cache_dir,
            args.keep_tree.resolve() if args.keep_tree else None,
            args.scan_latest,
        )
        if args.output_dir is None:
            try:
                shutil.rmtree(output)
            except OSError as error:
                record_failure(output, result, error)
                raise
    except (KeyError, ValueError, RuntimeError, OSError, CommandTimedOut) as error:
        messages = [
            f"FFmpeg patch stack FAILED: {error}",
            *getattr(error, "__notes__", ()),
            f"Diagnostics: {output}",
        ]
        print("\n".join(messages))
        return 1
    print(
        f"FFmpeg {result['tag']} ({result['upstream_commit']}): {result['patch_count']} patches "
        f"{result['status']} on shared fix series {result.get('shared_series', 'not replayed')} "
        f"({result.get('shared_patch_count', 0)} patches); source: {result.get('source', 'not fetched')}"
        + (f", {result['target_source']}" if "target_source" in result else "")
        + (f" [{result['cache']}]" if "cache" in result else "")
    )
    if result.get("reason"):
        reason = result["reason"]
        print(f"Reason: {reason}")
        if result.get("reason_code") == "no-compatible-series":
            escaped = reason.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::warning title=FFmpeg release scan::{escaped}")
    if args.output_dir:
        print(f"Diagnostics: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
