#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Exercise scanner configuration, exception boundaries and CI exit codes."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from http.client import HTTPConnection
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.docs import check_accessibility as gate  # noqa: E402


class TemporaryFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="a11y-test-"))
        self.addCleanup(shutil.rmtree, self.directory)

    def allowlist(self, exceptions: list[dict[str, str]]) -> Path:
        path = self.directory / "allowlist.json"
        path.write_text(
            json.dumps({"schema_version": 1, "exceptions": exceptions}), encoding="utf-8"
        )
        return path

    def exception(self) -> dict[str, str]:
        return {
            "page": "index.html",
            "rule": "color-contrast",
            "selector": "body",
            "reason": "test",
            "expires": (date.today() + timedelta(days=30)).isoformat(),
        }


class Configuration(TemporaryFiles):
    def test_fixture_has_all_four_planted_defects(self) -> None:
        text = gate.DEFAULT_FIXTURE.read_text(encoding="utf-8")
        self.assertIn("<img", text)
        self.assertNotIn("alt=", text)
        self.assertIn("<h1>", text)
        self.assertIn("<h3>", text)
        self.assertEqual(text.count('class="tiny"'), 2)
        self.assertIn("width: 16px; height: 16px", text)
        self.assertEqual(
            set(gate.PLANTED_RULES), {"image-alt", "color-contrast", "heading-order", "target-size"}
        )

    def test_config_enables_wcag22_and_retains_every_severity(self) -> None:
        path = gate.write_config(
            self.directory, gate.site_urls("http://127.0.0.1:1234/"), True, "chrome"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        defaults = config["defaults"]
        self.assertEqual(defaults["standard"], "WCAG2AA")
        self.assertEqual(defaults["runners"], ["axe"])
        self.assertNotIn("ignore", defaults)
        self.assertEqual(defaults["rules"], ["target-size"])
        self.assertTrue(defaults["includeWarnings"])
        self.assertTrue(defaults["includeNotices"])
        self.assertIn("--force-dark-mode", defaults["chromeLaunchConfig"]["args"])
        self.assertEqual(defaults["chromeLaunchConfig"]["executablePath"], "chrome")
        self.assertEqual(len(config["urls"]), 6)
        dark_urls = gate.site_urls("http://127.0.0.1:1234/", True)
        self.assertIn("check field #__palette_1", dark_urls[0]["actions"])
        self.assertIn(
            "wait for element body[data-md-color-scheme=slate] to be added", dark_urls[0]["actions"]
        )
        search = config["urls"][-1]
        self.assertTrue(search["url"].startswith("http://"))
        self.assertIn("set field input.md-search__input to psnr", search["actions"])
        self.assertIn("wait for element .md-search-result__item to be visible", search["actions"])

    def test_page_set_and_search_are_distinct_scenarios(self) -> None:
        pages = " ".join(gate.PAGES)
        for required in ("index.html", "backends/", "development/netflix", "adr/"):
            self.assertIn(required, pages)
        self.assertNotIn("search/index.html", gate.PAGES)
        for page in gate.SCAN_PAGES:
            self.assertEqual(gate.page_for("http://127.0.0.1:1234/" + page, gate.SCAN_PAGES), page)

    def test_http_server_serves_site_and_search_index(self) -> None:
        (self.directory / "index.html").write_text("home", encoding="utf-8")
        search = self.directory / "search"
        search.mkdir()
        (search / "search_index.json").write_text('{"docs": []}', encoding="utf-8")
        with gate.serve_site(self.directory) as url:
            for path, content in (
                ("index.html", b"home"),
                ("search/search_index.json", b'{"docs": []}'),
            ):
                parsed = urlparse(url)
                connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
                try:
                    connection.request("GET", "/" + path)
                    self.assertEqual(connection.getresponse().read(), content)
                finally:
                    connection.close()


class HeroContrast(unittest.TestCase):
    def test_gradient_contrast_evidence_matches_current_stylesheet(self) -> None:
        css = (ROOT / "docs/stylesheets/vmafx.css").read_text(encoding="utf-8")
        gradients = re.findall(r"--vx-hero-bg: ([^;]+);", css)
        self.assertEqual(len(gradients), 2)
        foregrounds = ("#f8fafc", "#c3cad8", "#67e8f9")
        for variable, value in zip(("fg", "fg-2", "accent"), foregrounds, strict=True):
            self.assertEqual(css.count(f"--vx-hero-{variable}: {value};"), 2)

        srgb_threshold = 0.04045

        def luminance(rgb: list[float]) -> float:
            linear = [
                v / 12.92 if v <= srgb_threshold else ((v + 0.055) / 1.055) ** 2.4 for v in rgb
            ]
            return sum(
                v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
            )

        def rgb(color: str) -> list[float]:
            return [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]

        stop_colors = [
            color for gradient in gradients for color in re.findall(r"#[0-9a-f]{6}", gradient)
        ]
        exceptions = gate.load_allowlist(gate.DEFAULT_ALLOWLIST)
        for foreground in foregrounds:
            minimum = min(
                (luminance(rgb(foreground)) + 0.05) / (luminance(rgb(stop)) + 0.05)
                for stop in stop_colors
            )
            self.assertGreaterEqual(minimum, 4.5)
            for entry in exceptions:
                self.assertIn(f"{foreground} approximately {minimum:.2f}:1", entry["reason"])
        for gradient in gradients:
            stops = [rgb(color) for color in re.findall(r"#[0-9a-f]{6}", gradient)]
            for left, right in pairwise(stops):
                for step in range(101):
                    sample = [a + (b - a) * step / 100 for a, b in zip(left, right, strict=True)]
                    for foreground in foregrounds:
                        self.assertGreaterEqual(
                            (luminance(rgb(foreground)) + 0.05) / (luminance(sample) + 0.05), 4.5
                        )


class Runner(TemporaryFiles):
    def test_json_findings_survive_pa11y_exit_statuses(self) -> None:
        report = {"results": {"http://localhost/index.html": [{"code": "meta-viewport"}]}}
        for status in (0, 1, 2):
            with self.subTest(status=status):
                completed = SimpleNamespace(returncode=status, stdout=json.dumps(report), stderr="")
                with patch.object(gate, "run_command", return_value=completed) as run:
                    self.assertEqual(
                        gate.run_pa11y("pa11y-ci.js", "test-node", self.directory / "config.json"),
                        report,
                    )
                self.assertEqual(run.call_args.args[0][:2], ["test-node", "pa11y-ci.js"])
                self.assertTrue(run.call_args.kwargs["capture_output"])
                self.assertGreater(run.call_args.kwargs["timeout_seconds"], 0)

    def test_missing_node_for_js_entry_is_a_setup_error(self) -> None:
        with patch.object(gate, "run_command") as run:
            with self.assertRaisesRegex(gate.AccessibilityError, "--node is required"):
                gate.run_pa11y("pa11y-ci.js", None, self.directory / "config.json")
        run.assert_not_called()

    def test_malformed_json_and_issue_shapes_are_setup_errors(self) -> None:
        payloads = (
            "not JSON",
            "[]",
            '{"results": []}',
            '{"results": {"http://localhost/index.html": null}}',
            '{"results": {"http://localhost/index.html": ["error"]}}',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                completed = SimpleNamespace(returncode=0, stdout=payload, stderr="diagnostic")
                with patch.object(gate, "run_command", return_value=completed):
                    with self.assertRaises(gate.AccessibilityError):
                        gate.run_pa11y("pa11y-ci", None, self.directory / "config.json")

    def test_unexpected_process_exit_is_a_setup_error(self) -> None:
        completed = SimpleNamespace(returncode=3, stdout='{"results": {}}', stderr="crashed")
        with patch.object(gate, "run_command", return_value=completed):
            with self.assertRaisesRegex(gate.AccessibilityError, "exited 3: crashed"):
                gate.run_pa11y("pa11y-ci", None, self.directory / "config.json")


class Allowlist(TemporaryFiles):
    def test_an_expired_entry_is_refused(self) -> None:
        exception = self.exception()
        exception["expires"] = (date.today() - timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(gate.AccessibilityError, "expired"):
            gate.load_allowlist(self.allowlist([exception]))

    def test_a_valid_entry_is_returned(self) -> None:
        exception = self.exception()
        self.assertEqual(gate.load_allowlist(self.allowlist([exception])), [exception])

    def test_an_entry_expiring_today_remains_valid(self) -> None:
        exception = self.exception()
        exception["expires"] = date.today().isoformat()
        self.assertEqual(gate.load_allowlist(self.allowlist([exception])), [exception])

    def test_exceptions_match_page_rule_and_selector_exactly(self) -> None:
        for field, value in (
            ("page", gate.SEARCH_PAGE),
            ("rule", "image-alt"),
            ("selector", "body > p"),
        ):
            with self.subTest(field=field):
                exception = self.exception()
                exception[field] = value
                used: set[int] = set()
                findings = gate.filter_issues(
                    {
                        "http://localhost/index.html": [
                            {"code": "color-contrast", "selector": "body"}
                        ]
                    },
                    ("index.html",),
                    [exception],
                    used,
                )
                self.assertEqual(len(findings), 1)
                self.assertEqual(used, set())

    def test_a_matching_issue_is_retained(self) -> None:
        used: set[int] = set()
        findings = gate.filter_issues(
            {"http://localhost/index.html": [{"code": "color-contrast", "selector": "body"}]},
            ("index.html",),
            [self.exception()],
            used,
        )
        self.assertEqual(findings, [])
        self.assertEqual(used, {0})

    def test_warnings_and_notices_are_findings(self) -> None:
        for severity, rule in (("warning", "meta-viewport"), ("notice", "aria-deprecated-role")):
            with self.subTest(severity=severity):
                findings = gate.filter_issues(
                    {
                        "http://localhost/index.html": [
                            {"code": rule, "type": severity, "selector": "body"}
                        ]
                    },
                    ("index.html",),
                    [],
                    set(),
                )
                self.assertIn(rule, findings[0])

    def test_incomplete_exception_never_masks_an_actual_violation(self) -> None:
        exception = self.exception()
        exception["scope"] = "incomplete"
        self.assertEqual(gate.load_allowlist(self.allowlist([exception])), [exception])
        for needs_review in (False, True):
            used: set[int] = set()
            findings = gate.filter_issues(
                {
                    "http://localhost/index.html": [
                        {
                            "code": "color-contrast",
                            "selector": "body",
                            "runnerExtras": {"needsFurtherReview": needs_review},
                        }
                    ]
                },
                ("index.html",),
                [exception],
                used,
            )
            self.assertEqual(bool(findings), not needs_review)
            self.assertEqual(used, {0} if needs_review else set())

    def test_best_practice_advisories_are_printed(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            findings = gate.filter_issues(
                {
                    "http://localhost/index.html": [
                        {"code": "landmark-unique", "selector": "nav", "type": "warning"}
                    ]
                },
                ("index.html",),
                [],
                set(),
            )
        self.assertEqual(findings, [])
        self.assertIn("advisory: index.html: landmark-unique", output.getvalue())

    def test_a_missing_result_is_a_finding(self) -> None:
        self.assertIn("missing pa11y result", gate.filter_issues({}, ("index.html",), [], set())[0])


class Main(TemporaryFiles):
    def setUp(self) -> None:
        super().setUp()
        self.site = self.directory / "site"
        self.site.mkdir()
        self.fixture = self.directory / "fixture.html"
        self.fixture.write_text("fixture", encoding="utf-8")
        self.allowlist_path = self.allowlist([])
        self.configs: list[dict[str, object]] = []

    def report(
        self, fixture: bool = False, issues: list[dict[str, object]] | None = None
    ) -> dict[str, object]:
        if fixture:
            return {
                "results": {self.fixture.as_uri(): [{"code": rule} for rule in gate.PLANTED_RULES]}
            }
        return {
            "results": {
                "http://localhost/" + page: issues if index == 0 and issues else []
                for index, page in enumerate(gate.SCAN_PAGES)
            }
        }

    def invoke(
        self, reports: list[dict[str, object]], failure: Exception | None = None
    ) -> tuple[int, str, object]:
        def canned(pa11y: str, node: str | None, config: Path) -> dict[str, object]:
            self.configs.append(json.loads(config.read_text(encoding="utf-8")))
            if failure is not None:
                raise failure
            return reports.pop(0)

        output = io.StringIO()
        with patch.object(gate, "run_pa11y", side_effect=canned) as runner:
            with redirect_stderr(output), redirect_stdout(output):
                rc = gate.main(
                    [
                        "--site",
                        str(self.site),
                        "--fixture",
                        str(self.fixture),
                        "--allowlist",
                        str(self.allowlist_path),
                        "--chrome",
                        "test-chrome",
                    ]
                )
        return rc, output.getvalue(), runner

    def test_fixture_missing_planted_rule_exits_two(self) -> None:
        report = self.report(fixture=True)
        report["results"][self.fixture.as_uri()].pop()
        rc, output, runner = self.invoke([report])
        self.assertEqual(rc, 2)
        self.assertIn("target-size", output)
        self.assertEqual(runner.call_count, 1)

    def test_rules_on_another_url_never_prove_fixture_detection(self) -> None:
        report = self.report(fixture=True)
        report["results"]["file:///other/fixture.html"] = report["results"].pop(
            self.fixture.as_uri()
        )
        rc, output, runner = self.invoke([report])
        self.assertEqual(rc, 2)
        self.assertIn("exactly the planted-defect fixture URL", output)
        self.assertEqual(runner.call_count, 1)

    def test_fixture_page_load_failure_exits_two(self) -> None:
        report = {"results": {self.fixture.as_uri(): [{"message": "navigation failed"}]}}
        rc, output, runner = self.invoke([report])
        self.assertEqual(rc, 2)
        for rule in gate.PLANTED_RULES:
            self.assertIn(rule, output)
        self.assertEqual(runner.call_count, 1)

    def test_incomplete_fixture_result_does_not_prove_detection(self) -> None:
        report = self.report(fixture=True)
        report["results"][self.fixture.as_uri()][-1]["runnerExtras"] = {"needsFurtherReview": True}
        rc, output, _ = self.invoke([report])
        self.assertEqual(rc, 2)
        self.assertIn("target-size", output)

    def test_site_issue_exits_one(self) -> None:
        issue = {"code": "image-alt", "selector": "img", "message": "missing alt"}
        rc, output, _ = self.invoke([self.report(True), self.report(issues=[issue]), self.report()])
        self.assertEqual(rc, 1)
        self.assertIn("light: index.html: image-alt", output)

    def test_wcag_moderate_and_minor_issues_exit_one(self) -> None:
        for severity, rule in (("warning", "meta-viewport"), ("notice", "aria-deprecated-role")):
            with self.subTest(severity=severity):
                issue = {"code": rule, "type": severity, "selector": "body"}
                rc, output, _ = self.invoke(
                    [self.report(True), self.report(), self.report(issues=[issue])]
                )
                self.assertEqual(rc, 1)
                self.assertIn(f"dark: index.html: {rule}", output)

    def test_used_allowlist_exits_zero(self) -> None:
        self.allowlist_path = self.allowlist([self.exception()])
        issue = {"code": "color-contrast", "selector": "body"}
        rc, output, _ = self.invoke([self.report(True), self.report(issues=[issue]), self.report()])
        self.assertEqual(rc, 0)
        self.assertIn("1 exception(s)", output)

    def test_missing_search_result_exits_one(self) -> None:
        report = self.report()
        del report["results"]["http://localhost/" + gate.SEARCH_PAGE]
        rc, output, _ = self.invoke([self.report(True), self.report(), report])
        self.assertEqual(rc, 1)
        self.assertIn(f"dark: {gate.SEARCH_PAGE}: missing pa11y result", output)

    def test_unexpected_page_exits_one(self) -> None:
        report = self.report()
        report["results"]["http://localhost/unscanned.html"] = []
        rc, output, _ = self.invoke([self.report(True), report, self.report()])
        self.assertEqual(rc, 1)
        self.assertIn("outside the fixed set", output)

    def test_unused_allowlist_exits_one(self) -> None:
        self.allowlist_path = self.allowlist([self.exception()])
        rc, output, _ = self.invoke([self.report(True), self.report(), self.report()])
        self.assertEqual(rc, 1)
        self.assertIn("matches no issue", output)

    def test_page_load_error_exits_one(self) -> None:
        rc, output, _ = self.invoke(
            [
                self.report(True),
                self.report(issues=[{"message": "navigation failed"}]),
                self.report(),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("page-load", output)
        self.assertIn("navigation failed", output)

    def test_clean_fixture_and_site_exit_zero(self) -> None:
        rc, output, runner = self.invoke([self.report(True), self.report(), self.report()])
        self.assertEqual(rc, 0)
        self.assertIn("5 pages + live search light+dark", output)
        self.assertEqual(runner.call_count, 3)
        self.assertNotIn(
            "--force-dark-mode", self.configs[1]["defaults"]["chromeLaunchConfig"]["args"]
        )
        self.assertIn(
            "--force-dark-mode", self.configs[2]["defaults"]["chromeLaunchConfig"]["args"]
        )

    def test_missing_site_exits_two_without_scanning(self) -> None:
        self.site = self.directory / "missing-site"
        rc, output, runner = self.invoke([])
        self.assertEqual(rc, 2)
        self.assertIn("built site is missing", output)
        runner.assert_not_called()

    def test_missing_fixture_exits_two_without_scanning(self) -> None:
        self.fixture = self.directory / "missing-fixture"
        rc, output, runner = self.invoke([])
        self.assertEqual(rc, 2)
        self.assertIn("fixture is missing", output)
        runner.assert_not_called()

    def test_missing_allowlist_exits_two_without_scanning(self) -> None:
        self.allowlist_path = self.directory / "missing-allowlist.json"
        rc, output, runner = self.invoke([])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", output)
        runner.assert_not_called()

    def test_invalid_allowlist_exits_two_without_scanning(self) -> None:
        self.allowlist_path.write_text('{"schema_version": 2}', encoding="utf-8")
        rc, output, runner = self.invoke([])
        self.assertEqual(rc, 2)
        self.assertIn("schema_version must be 1", output)
        runner.assert_not_called()

    def test_process_setup_timeout_and_output_limit_exit_two(self) -> None:
        failures = (
            gate.CommandValidationError("executable unavailable"),
            gate.CommandTimedOut(("node",), 1, "", ""),
            gate.CommandOutputLimitExceeded(("node",), 10, "", ""),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                rc, output, runner = self.invoke([], failure=failure)
                self.assertEqual(rc, 2)
                self.assertIn("docs-accessibility: error:", output)
                self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
