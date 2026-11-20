"""Bounded discovery of signed shared series for a future FFmpeg base."""

# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import TypedDict

from scripts.lib.safe_subprocess import run as run_command

RELEASE_LIMIT = 64
DISCOVERY_SECONDS = 1200
SERIES_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
REPOSITORY = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\Z")


class Candidate(TypedDict):
    tag: str
    sha256: str
    base_tag: str
    base_commit: str


def pin_values(repo: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    keys = {"FFMPEG_FIX_SERIES_REPO", "FFMPEG_FIX_SERIES_TAG", "FFMPEG_FIX_SERIES_SHA256"}
    for line in (repo / "build-config.env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key in keys:
            parts = shlex.split(value, comments=True)
            if len(parts) != 1 or key in values:
                raise ValueError(f"invalid or duplicate shared-series pin: {key}")
            values[key] = parts[0]
    if set(values) != keys or not all(values.values()):
        raise ValueError("--scan-latest requires all shared-series pins")
    if not re.fullmatch(r"[0-9a-f]{64}", values["FFMPEG_FIX_SERIES_SHA256"]):
        raise ValueError("invalid shared-series SHA256 pin")
    if not REPOSITORY.fullmatch(values["FFMPEG_FIX_SERIES_REPO"]):
        raise ValueError("scan discovery requires a canonical github.com series repository")
    return values


def release_tags(repository: str) -> list[str]:
    """One bounded page; truncation is an error, never evidence of no candidate."""
    match = REPOSITORY.fullmatch(repository)
    if match is None:
        raise ValueError("invalid shared-series repository")
    gh = shutil.which("gh")
    if gh is None:
        raise RuntimeError("gh is required to enumerate published shared-series releases")
    result = run_command(
        [gh, "api", f"repos/{match.group(1)}/releases?per_page={RELEASE_LIMIT + 1}"],
        allowed_executables=(gh,),
        timeout_seconds=30,
        max_output_bytes=1_048_576,
        capture_output=True,
        text=True,
        check=True,
    )
    releases = json.loads(result.stdout)
    if not isinstance(releases, list) or len(releases) > RELEASE_LIMIT:
        raise ValueError("shared-series release discovery exceeded its bounded page")
    tags: list[str] = []
    for release in releases:
        if (
            not isinstance(release, dict)
            or not isinstance(release.get("tag_name"), str)
            or not isinstance(release.get("draft"), bool)
            or not isinstance(release.get("prerelease"), bool)
        ):
            raise ValueError("invalid shared-series release metadata")
        if release.get("draft") is False and release.get("prerelease") is False:
            tag = release["tag_name"]
            if SERIES_TAG.fullmatch(tag):
                tags.append(tag)
    if len(set(tags)) != len(tags):
        raise ValueError("duplicate shared-series release tags")
    return sorted(tags, key=lambda tag: tuple(map(int, tag[1:].split("."))), reverse=True)


def write_config(directory: Path, pins: dict[str, str], tag: str, commit: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    values = {**pins, "FFMPEG_TAG": tag, "FFMPEG_COMMIT": commit}
    (directory / "build-config.env").write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()), encoding="utf-8"
    )


def metadata(directory: Path) -> Candidate:
    values = dict(
        line.split("=", 1) for line in (directory / "verified.env").read_text().splitlines()
    )
    candidate: Candidate = {
        "tag": values["FFMPEG_FIX_SERIES_TAG"],
        "sha256": values["FFMPEG_FIX_SERIES_SHA256"],
        "base_tag": values["FFMPEG_TAG"],
        "base_commit": values["FFMPEG_COMMIT"],
    }
    if not SERIES_TAG.fullmatch(candidate["tag"]):
        raise ValueError("invalid verified series tag")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate["sha256"]):
        raise ValueError("invalid verified series digest")
    if not re.fullmatch(r"n\d+\.\d+(?:\.\d+)?", candidate["base_tag"]):
        raise ValueError("invalid verified FFmpeg base tag")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate["base_commit"]):
        raise ValueError("invalid verified FFmpeg base commit")
    return candidate


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("shared-series candidate discovery deadline exceeded")
    return remaining
