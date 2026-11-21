#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Scan built documentation and live search with pa11y-ci and a negative control.

WCAG 2.1 A/AA plus axe's WCAG 2.2 AA target-size rule run in both palettes.
Warnings and notices are retained. Dated, exact-selector exceptions must match.
The fixture must detect every planted rule before site results can be trusted.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.safe_subprocess import (  # noqa: E402
    CommandOutputLimitExceeded,
    CommandTimedOut,
    CommandValidationError,
)
from scripts.lib.safe_subprocess import run as run_command  # noqa: E402

DEFAULT_SITE = REPO_ROOT / "build-docs" / "site"
DEFAULT_FIXTURE = REPO_ROOT / "scripts" / "docs" / "accessibility-fixture.html"
DEFAULT_ALLOWLIST = REPO_ROOT / "scripts" / "docs" / "accessibility-allowlist.json"
DEFAULT_PA11Y = REPO_ROOT / "tools/accessibility/node_modules/pa11y-ci/bin/pa11y-ci.js"
PAGES = (
    "index.html",
    "backends/sycl/overview/index.html",
    "metrics/psnr/index.html",
    "development/netflix-benchmark-baselines/index.html",
    "adr/0001-stash-benchmark-noise-file/index.html",
)
SEARCH_PAGE = "index.html?accessibility-search=psnr"
SCAN_PAGES = (*PAGES, SEARCH_PAGE)
SEARCH_ACTIONS = (
    "click element input.md-search__input",
    "wait for element .md-search__inner to be visible",
    "set field input.md-search__input to psnr",
    "wait for element .md-search-result__item to be added",
    "wait for element .md-search-result__item to be visible",
)
PLANTED_RULES = ("image-alt", "color-contrast", "heading-order", "target-size")
STANDARD = "WCAG2AA"
# These ids have only best-practice tags in the locked axe-core 4.13.0.
# Retain and print them; WCAG warnings/notices still fail the gate.
ADVISORY_RULES = frozenset(("aria-dialog-name", "landmark-unique"))
TIMEOUT_SECONDS = 120


class AccessibilityError(Exception):
    """The check could not run or the allowlist is malformed."""


class QuietHandler(SimpleHTTPRequestHandler):
    """Serve only the built site, without contaminating scanner output."""

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def serve_site(site: Path) -> Iterator[str]:
    handler = partial(QuietHandler, directory=str(site.resolve()))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def load_allowlist(path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessibilityError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise AccessibilityError(f"{path}: schema_version must be 1")
    exceptions = data.get("exceptions")
    if not isinstance(exceptions, list):
        raise AccessibilityError(f"{path}: exceptions must be a list")
    parsed: list[dict[str, str]] = []
    for index, entry in enumerate(exceptions):
        if not isinstance(entry, dict):
            raise AccessibilityError(f"{path}: exceptions[{index}] must be an object")
        for field in ("page", "rule", "selector", "reason", "expires"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise AccessibilityError(f"{path}: exceptions[{index}].{field} must be a string")
        try:
            expiry = date.fromisoformat(entry["expires"])
        except ValueError as exc:
            raise AccessibilityError(
                f"{path}: exceptions[{index}].expires must be YYYY-MM-DD"
            ) from exc
        if expiry < date.today():
            raise AccessibilityError(f"{path}: exceptions[{index}] expired on {entry['expires']}")
        parsed_entry = {
            field: entry[field] for field in ("page", "rule", "selector", "reason", "expires")
        }
        if "scope" in entry:
            if entry["scope"] != "incomplete":
                raise AccessibilityError(f"{path}: exceptions[{index}].scope must be incomplete")
            parsed_entry["scope"] = "incomplete"
        parsed.append(parsed_entry)
    return parsed


def page_for(url: str, pages: tuple[str, ...]) -> str | None:
    parsed = urlparse(url)
    path = unquote(parsed.path).replace("\\", "/")
    if parsed.query:
        path += "?" + parsed.query
    matches = [page for page in pages if path.endswith("/" + page) or path == page]
    return max(matches, key=len) if matches else None


def issue_key(page: str, issue: dict[str, object]) -> tuple[str, str, str]:
    return page, str(issue.get("code", "")), str(issue.get("selector", ""))


def needs_review(issue: dict[str, object]) -> bool:
    extras = issue.get("runnerExtras")
    return isinstance(extras, dict) and extras.get("needsFurtherReview") is True


def filter_issues(
    results: dict[str, list[dict[str, object]]],
    pages: tuple[str, ...],
    exceptions: list[dict[str, str]],
    used: set[int],
) -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()
    for url, issues in results.items():
        page = page_for(url, pages)
        if page is None:
            findings.append(f"pa11y returned a page outside the fixed set: {url}")
            continue
        seen.add(page)
        for issue in issues:
            key = issue_key(page, issue)
            if key[1] in ADVISORY_RULES:
                print(f"docs-accessibility: advisory: {page}: {key[1]} at {key[2]}")
                continue
            matched = next(
                (
                    index
                    for index, exception in enumerate(exceptions)
                    if (exception["page"], exception["rule"], exception["selector"]) == key
                    and (exception.get("scope") != "incomplete" or needs_review(issue))
                ),
                None,
            )
            if matched is None:
                findings.append(
                    f"{page}: {key[1] or 'page-load'} at {key[2]}: "
                    f"{issue.get('message', 'no message')}"
                )
            else:
                used.add(matched)
    findings.extend(f"{page}: missing pa11y result" for page in pages if page not in seen)
    return findings


def site_urls(base_url: str, dark: bool = False) -> tuple[str | dict[str, object], ...]:
    scheme = "slate" if dark else "default"
    palette = "1" if dark else "0"
    actions = [
        f"check field #__palette_{palette}",
        f"wait for element body[data-md-color-scheme={scheme}] to be added",
    ]
    return (
        *({"url": base_url + page, "actions": actions} for page in PAGES),
        {"url": base_url + SEARCH_PAGE, "actions": [*actions, *SEARCH_ACTIONS]},
    )


def write_config(
    directory: Path,
    urls: tuple[str | dict[str, object], ...],
    dark: bool,
    chrome: str | None,
) -> Path:
    args = ["--no-sandbox", "--disable-gpu"]
    if dark:
        args.append("--force-dark-mode")
    launch: dict[str, object] = {"args": args}
    if chrome:
        launch["executablePath"] = chrome
    config = {
        "defaults": {
            "standard": STANDARD,
            "runners": ["axe"],
            "includeWarnings": True,
            "includeNotices": True,
            "rules": ["target-size"],
            # Match a desktop pointer: Material manages overflowing code tabindex
            # only when the device has hover. Tables are handled by our script.
            "viewport": {"width": 1280, "height": 1024, "hasTouch": False, "isMobile": False},
            "timeout": 30000,
            "chromeLaunchConfig": launch,
        },
        "urls": list(urls),
    }
    path = directory / ("dark.json" if dark else "light.json")
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def run_pa11y(pa11y_ci: str, node: str | None, config: Path) -> dict[str, object]:
    binary = Path(pa11y_ci)
    if binary.suffix == ".js":
        if node is None:
            raise AccessibilityError("--node is required when --pa11y-ci names a .js file")
        command = [node, str(binary)]
    else:
        command = [pa11y_ci]
    completed = run_command(
        [*command, "--config", str(config), "--json", "--threshold", "999"],
        allowed_executables=(command[0],),
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout_seconds=TIMEOUT_SECONDS * 4,
    )
    if completed.returncode not in (0, 1, 2):
        raise AccessibilityError(
            f"pa11y-ci exited {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AccessibilityError(f"pa11y-ci produced no JSON: {completed.stderr.strip()}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("results"), dict):
        raise AccessibilityError("pa11y-ci JSON has no results object")
    if not all(
        isinstance(url, str)
        and isinstance(issues, list)
        and all(isinstance(issue, dict) for issue in issues)
        for url, issues in report["results"].items()
    ):
        raise AccessibilityError("pa11y-ci results must map URLs to lists of issue objects")
    return report


def scan(
    pa11y_ci: str,
    node: str | None,
    chrome: str | None,
    urls: tuple[str | dict[str, object], ...],
    pages: tuple[str, ...],
    exceptions: list[dict[str, str]],
    used: set[int],
    directory: Path,
    dark: bool,
) -> tuple[list[str], dict[str, object]]:
    config = write_config(directory, urls, dark, chrome)
    report = run_pa11y(pa11y_ci, node, config)
    results = report["results"]
    assert isinstance(results, dict)
    findings = filter_issues(results, pages, exceptions, used)
    return findings, report


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument(
        "--pa11y-ci", default=str(DEFAULT_PA11Y), help="pa11y-ci binary or .js entry"
    )
    parser.add_argument("--node", default="node", help="Node.js executable")
    parser.add_argument("--chrome", required=True, help="installed Chrome/Edge executable")
    return parser.parse_args(argv)


def check_fixture(args: argparse.Namespace, directory: Path) -> None:
    _, report = scan(
        args.pa11y_ci,
        args.node,
        args.chrome,
        (args.fixture.resolve().as_uri(),),
        (args.fixture.name,),
        [],
        set(),
        directory,
        dark=True,
    )
    results = report["results"]
    assert isinstance(results, dict)
    fixture_url = args.fixture.resolve().as_uri()
    if set(results) != {fixture_url}:
        raise AccessibilityError("pa11y-ci did not return exactly the planted-defect fixture URL")
    rules = {issue.get("code") for issue in results[fixture_url] if not needs_review(issue)}
    missing = set(PLANTED_RULES) - rules
    if missing:
        raise AccessibilityError(
            "the planted-defect fixture did not fail on: " + ", ".join(sorted(missing))
        )


def check_site(
    args: argparse.Namespace, directory: Path, exceptions: list[dict[str, str]], used: set[int]
) -> list[str]:
    findings: list[str] = []
    with serve_site(args.site) as base_url:
        for dark in (False, True):
            site_findings, _ = scan(
                args.pa11y_ci,
                args.node,
                args.chrome,
                site_urls(base_url, dark),
                SCAN_PAGES,
                exceptions,
                used,
                directory,
                dark=dark,
            )
            findings.extend(f"{'dark' if dark else 'light'}: {item}" for item in site_findings)
    return findings


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.site.is_dir():
            raise AccessibilityError(f"built site is missing: {args.site}; run mkdocs build first")
        if not args.fixture.is_file():
            raise AccessibilityError(f"planted-defect fixture is missing: {args.fixture}")
        exceptions = load_allowlist(args.allowlist)
        used: set[int] = set()
        with tempfile.TemporaryDirectory(prefix="docs-a11y-") as tmp:
            directory = Path(tmp)
            check_fixture(args, directory)
            findings = check_site(args, directory, exceptions, used)
        for index, exception in enumerate(exceptions):
            if index not in used:
                findings.append(
                    f"allowlist entry for {exception['page']} {exception['rule']} "
                    f"({exception['selector']}) matches no issue"
                )
    except (
        AccessibilityError,
        OSError,
        CommandValidationError,
        CommandTimedOut,
        CommandOutputLimitExceeded,
    ) as exc:
        print(f"docs-accessibility: error: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(f"docs-accessibility: {finding}", file=sys.stderr)
        print(
            f"docs-accessibility: {len(findings)} finding(s); "
            "fix them or add a dated reason to scripts/docs/accessibility-allowlist.json",
            file=sys.stderr,
        )
        return 1
    print(
        f"docs-accessibility: OK ({len(PAGES)} pages + live search light+dark, "
        f"{len(exceptions)} exception(s), {len(PLANTED_RULES)} planted rules detected)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
