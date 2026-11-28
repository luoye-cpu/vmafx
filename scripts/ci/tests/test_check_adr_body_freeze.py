#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Exercise ADR freeze checks against isolated Git histories."""

from __future__ import annotations

import importlib
import io
import os
import runpy
import shlex
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
links = importlib.import_module("scripts.ci.tests.symlink_test_support")
run = importlib.import_module("scripts.lib.safe_subprocess").run
yaml = importlib.import_module("yaml")
GIT = shutil.which("git") or "git"

if TYPE_CHECKING:
    from scripts.lib.safe_subprocess import TextCommandResult

SCRIPT = Path(__file__).resolve().parents[1] / "check-adr-body-freeze.py"
ADR = "docs/adr/1234-example.md"
NOTE = "> [!NOTE]\n> Superseded by [ADR-9999](9999-successor.md)\n"
BASE = (
    "# ADR-1234: Example\n\n- **Status**: Accepted\n\n## Context\nOld context.\n\n"
    "## Decision\nOld decision.\n\n## Consequences\nOld consequence.\n\n"
    "## References\n[Source](old.md)\n"
)


class FreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.org",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.org",
        )
        self.git("init", "-q")
        self.write(ADR, BASE)
        self.write("docs/adr/9999-successor.md", BASE)
        self.write("docs/adr/0000-template.md", BASE)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        return str(
            run(
                [GIT, "-c", "commit.gpgsign=false", *args],
                allowed_executables=(GIT,),
                cwd=self.repo,
                env=self.env,
                text=True,
                capture_output=True,
                check=True,
                timeout_seconds=30,
            ).stdout
        )

    def write(self, name: str, content: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message: str = "fixture") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def check_change(self, content: str, expected: int, section: str = "") -> None:
        self.write(ADR, content)
        self.commit()
        result = run(
            [sys.executable, str(SCRIPT), "--base", self.base, "--head", "HEAD"],
            allowed_executables=(sys.executable,),
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
            timeout_seconds=30,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if section:
            self.assertRegex(result.stdout, r"docs/adr/1234-example\.md:\d+: " + section)

    def test_status_line(self) -> None:
        self.check_change(BASE.replace("Accepted", "Superseded"), 1)

    def test_dated_updates(self) -> None:
        for heading in ("##", "###"):
            with self.subTest(heading=heading):
                self.check_change(BASE + f"\n{heading} Status update 2026-11-15: x\nUpdate.\n", 0)

    def test_supersession_note(self) -> None:
        self.check_change(
            BASE.replace("Accepted\n", "Superseded\n\n" + NOTE),
            0,
        )

    def test_note_after_status_metadata(self) -> None:
        self.write(ADR, BASE.replace("Accepted\n", "Accepted\n- **Date**: 2026-01-01\n"))
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(
            BASE.replace(
                "Accepted\n",
                "Superseded\n- **Date**: 2026-01-01\n\n" + NOTE,
            ),
            0,
        )

    def test_references(self) -> None:
        self.check_change(BASE.replace("[Source](old.md)", "New references."), 0)

    def test_link_target(self) -> None:
        self.check_change(BASE.replace("Old context.", "See [source](old.md)."), 1)

    def test_link_repair(self) -> None:
        self.check_change(
            BASE.replace("# ADR-1234: Example", "# ADR-1234: Example [link](old.md)"), 1
        )

    def test_link_repair_allowed(self) -> None:
        self.write(ADR, BASE.replace("Old context.", "See [source](old.md)."))
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(BASE.replace("Old context.", "See [source](new.md)."), 0)

    def test_proposed(self) -> None:
        self.write(ADR, BASE.replace("Accepted", "Proposed"))
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(BASE.replace("Accepted", "Proposed").replace("Old context.", "New."), 0)

    def test_changed_consequences(self) -> None:
        self.check_change(
            BASE.replace("Old consequence.", "New consequence."), 1, "## Consequences"
        )

    def test_removed_decision(self) -> None:
        self.check_change(BASE.replace("Old decision.\n", ""), 1, "## Decision")

    def test_added_context_line(self) -> None:
        self.check_change(
            BASE.replace("Old context.\n", "Old context.\nNew line.\n"), 1, "## Context"
        )

    def test_insert_blank_and_consequence(self) -> None:
        self.write(
            ADR, BASE.replace("Old consequence.\n", "Old consequence.\n\nNew consequence.\n")
        )
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("frozen", result.stdout)

    def test_undated_heading(self) -> None:
        self.check_change(BASE + "\n## Status update\nNew line.\n", 1)

    def commit_frozen_edit(self, path: str = ADR) -> None:
        self.write(path, BASE.replace("Old decision.", "Changed decision."))
        self.commit()

    def test_new_file_and_template(self) -> None:
        self.write("docs/adr/9999-new.md", BASE)
        self.write("docs/adr/0000-template.md", "changed\n")
        self.commit()
        clean = self.run_check()
        self.assertEqual((clean.returncode, clean.stdout, clean.stderr), (0, "", ""))

        self.commit_frozen_edit()
        refused = self.run_check()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(ADR, refused.stdout)

    def run_check(
        self, base: str | None = None, head_args: tuple[str, ...] = ("--head", "HEAD")
    ) -> TextCommandResult:
        return cast(
            "TextCommandResult",
            run(
                [sys.executable, str(SCRIPT), "--base", base or self.base, *head_args],
                allowed_executables=(sys.executable,),
                cwd=self.repo,
                env=self.env,
                text=True,
                capture_output=True,
                check=False,
                timeout_seconds=30,
            ),
        )

    def test_directive_changed(self) -> None:
        self.write(ADR, "<!-- markdownlint-disable MD060 -->\n" + BASE)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write(ADR, "<!-- markdownlint-disable MD060 MD013 -->\n" + BASE)
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("frozen", result.stdout)

    def test_directive_before_title(self) -> None:
        self.write(ADR, "<!-- markdownlint-disable-next-line MD013 -->\n" + BASE)
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("frozen", result.stdout)

    def reflow(self, changed: bool) -> TextCommandResult:
        self.write(ADR, BASE.replace("Old context.", "One two three\nfour five six."))
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        end = "seven" if changed else "six"
        self.write(ADR, BASE.replace("Old context.", f"One two\nthree four\nfive {end}."))
        self.commit()
        return self.run_check()

    def test_reflow_same_words(self) -> None:
        result = self.reflow(False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("frozen", result.stdout)

    def test_reflow_changed_word(self) -> None:
        result = self.reflow(True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("## Context", result.stdout)

    def behind_master(self, edit: bool) -> TextCommandResult:
        self.git("checkout", "-q", "-b", "feature")
        self.write("other.txt", "branch change\n")
        if edit:
            self.write(ADR, BASE.replace("Old consequence.", "Changed consequence."))
        self.commit()
        self.git("checkout", "-q", "-b", "base", self.base)
        self.write(
            ADR,
            BASE.replace(
                "## References", "### Status update 2026-11-20\nMaster update.\n\n## References"
            ),
        )
        self.commit()
        base_tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "feature")
        return self.run_check(base_tip)

    def test_behind_master(self) -> None:
        result = self.behind_master(False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("frozen", result.stdout)

    def test_behind_master_consequences(self) -> None:
        result = self.behind_master(True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("## Consequences", result.stdout)
        self.assertNotIn("Status update", result.stdout.split(": body of")[0])

    def test_blank_after_directive(self) -> None:
        directive = "<!-- markdownlint-disable MD013 -->\n"
        self.write(ADR, directive + BASE)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write(ADR, directive + "\n" + BASE)
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("frozen", result.stdout)

    def table_change(self, row: str) -> TextCommandResult:
        table = "| Name | Value |\n|---|---|\n| one two | value |"
        self.write(ADR, BASE.replace("Old context.", table))
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write(ADR, BASE.replace("Old context.", row))
        self.commit()
        return self.run_check()

    def test_table_spacing(self) -> None:
        result = self.table_change("| Name | Value |\n| --- | --- |\n|one two|value|")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("frozen", result.stdout)

    def test_table_changed_word(self) -> None:
        result = self.table_change("| Name | Value |\n| --- | --- |\n|one too|value|")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("## Context", result.stdout)

    def test_override(self) -> None:
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit("fixture\n\nADR-Body-Edit: ADR-1234 redact a development host name")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("note: docs/adr/1234-example.md:", result.stdout)
        self.assertIn("1 hunk(s) accepted under ADR-Body-Edit in", result.stdout)
        self.assertIn("ADR-1234: 1 hunk(s) accepted", result.stdout)
        self.assertIn("redact a development host name", result.stdout)

    def test_override_names_other_adr(self) -> None:
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit("fixture\n\nADR-Body-Edit: ADR-9999 redact a development host name")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("## Consequences", result.stdout)
        self.assertNotIn("note:", result.stdout)

    def test_override_without_adr_id(self) -> None:
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit("fixture\n\nADR-Body-Edit: redact a development host name from the record")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("must name each waived ADR", result.stderr)

    def test_override_covers_named_adr_only(self) -> None:
        other = "docs/adr/1235-other.md"
        self.write(other, BASE)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        for name in (ADR, other):
            self.write(name, BASE.replace("Old consequence.", "New consequence."))
        self.commit("fixture\n\nADR-Body-Edit: ADR-1234 redact a development host name")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("note: docs/adr/1234-example.md:", result.stdout)
        self.assertIn("\ndocs/adr/1235-other.md:", "\n" + result.stdout)
        self.assertIn("ADR-1234: 1 hunk(s) accepted", result.stdout)

    def test_override_short_reason(self) -> None:
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit("fixture\n\nADR-Body-Edit: fix")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("at least 20 characters", result.stderr)
        self.assertIn(self.git("rev-parse", "HEAD").strip(), result.stderr)

    def test_override_outside_range(self) -> None:
        self.commit("fixture\n\nADR-Body-Edit: ADR-1234 redact a development host name")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("## Consequences", result.stdout)
        self.assertNotIn("note:", result.stdout)

    def test_rename_unchanged(self) -> None:
        renamed = "docs/adr/1234-renamed.md"
        self.git("mv", ADR, renamed)
        self.commit()
        clean = self.run_check()
        self.assertEqual((clean.returncode, clean.stdout, clean.stderr), (0, "", ""))

        self.commit_frozen_edit(renamed)
        refused = self.run_check()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(renamed, refused.stdout)

    def test_rename_changed(self) -> None:
        self.git("mv", ADR, "docs/adr/1234-renamed.md")
        self.write("docs/adr/1234-renamed.md", BASE.replace("Old context.", "New context."))
        self.commit()
        self.assertEqual(self.run_check().returncode, 1)

    def given(self, content: str) -> None:
        self.write(ADR, content)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def test_frozen_at_fork(self) -> None:
        self.git("checkout", "-qb", "topic")
        self.write(ADR, BASE.replace("Old decision.", "Changed."))
        self.commit()
        self.git("checkout", "-qb", "base-tip", self.base)
        self.given(BASE.replace("Accepted", "Proposed"))
        self.git("checkout", "-q", "topic")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("## Decision", result.stdout)

    def test_deprecated_note(self) -> None:
        note = NOTE + "> Deprecated: The successor replaces this decision.\n"
        changed = BASE.replace("Accepted\n", "Deprecated\n" + note)
        self.check_change(changed, 0)
        self.check_change(changed.replace("decision.", "decision. Extra policy.", 1), 1)
        self.check_change(changed.replace("> [!NOTE]", "> [!NOTE]\n> Status"), 1)

    def test_multiline_code_link_is_literal(self) -> None:
        original = BASE.replace("Old context.", "Use ``\n[mode](deny)\n`` here.")
        self.given(original)
        self.check_change(original.replace("(deny)", "(allow)"), 1)

    def test_code_in_link_label(self) -> None:
        original = BASE.replace("Old context.", "See [the `x  y` flag](old(v1).md).")
        self.given(original)
        self.check_change(original.replace("old(v1).md", "new.md"), 0)
        self.check_change(original.replace("x  y", "x y"), 1)

    def test_retained_references_in_order(self) -> None:
        original = BASE + "[Second](two.md)\n"
        self.given(original)
        update = "### Status update 2026-11-23\nUpdate.\n"
        reordered = original.replace(
            "[Source](old.md)\n[Second](two.md)", "[Second](changed.md)\n[Source](updated.md)"
        )
        self.check_change(reordered + update, 1)
        self.check_change(original.replace("[Source](old.md)", "[Source](new.md)") + update, 0)

    def test_git_errors_name_operation_and_revision(self) -> None:
        checker = runpy.run_path(str(SCRIPT))
        errors = (TimeoutError("command timed out"), RuntimeError("output limit"))
        for error in errors:
            with (
                self.subTest(error=error),
                mock.patch.dict(checker["git"].__globals__, {"run": mock.Mock(side_effect=error)}),
                self.assertRaisesRegex(RuntimeError, "git show HEAD:docs/adr/"),
            ):
                checker["git"]("show", f"HEAD:{ADR}")

    def test_status_downgrade(self) -> None:
        self.check_change(BASE.replace("Accepted", "Proposed"), 1)
        self.assertIn("transition", self.run_check().stdout)

    def test_fenced_preamble_status(self) -> None:
        original = BASE.replace("- **Status**", "```md\n- **Status**: Proposed\n```\n- **Status**")
        self.given(original)
        self.check_change(original.replace("Old decision.", "Changed."), 1)

    def test_accepted_on_base_after_fork(self) -> None:
        proposed = BASE.replace("Accepted", "Proposed")
        self.given(proposed)
        self.git("checkout", "-qb", "topic")
        self.write(ADR, proposed.replace("Old decision.", "Changed."))
        self.commit()
        self.git("checkout", "-qb", "base-tip", self.base)
        self.given(BASE)
        self.git("checkout", "-q", "topic")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("## Decision", result.stdout)

    def test_conflicting_adr_names_path(self) -> None:
        self.git("checkout", "-qb", "topic")
        self.write(ADR, BASE.replace("[Source](old.md)", "Topic source."))
        self.commit()
        self.git("checkout", "-qb", "base-tip", self.base)
        self.given(BASE.replace("[Source](old.md)", "Base source."))
        self.git("checkout", "-q", "topic")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(ADR, result.stdout)
        self.assertIn("conflict", result.stdout)

    def test_indented_and_setext_headings(self) -> None:
        for heading in ("  ## Decision", "Decision\n-------", "> ## Decision", "> Decision\n> ==="):
            with self.subTest(heading=heading):
                self.check_change(BASE + "\n" + heading + "\nChanged.\n", 1)

    def test_references_cannot_swallow_update(self) -> None:
        update = "### Status update 2026-11-22\nFrozen.\n"
        self.given(BASE + "\n" + update)
        self.check_change(BASE + "\n```text\n" + update, 1)

    def test_update_before_repaired_reference(self) -> None:
        self.check_change(
            BASE.replace(
                "[Source](old.md)", "### Status update 2026-11-22\nUpdate.\n\n[Source](fixed.md)"
            ),
            1,
        )

    def test_quoted_fence_spacing(self) -> None:
        original = BASE.replace("Old context.", "> > ~~~\n> > x  y\n> > ~~~")
        self.given(original)
        self.check_change(original.replace("x  y", "x y"), 1)

    def test_fence_closer_ascii_only(self) -> None:
        original = BASE.replace("Old context.", "````\n````\u00a0\nx  y\n````")
        self.given(original)
        self.check_change(original.replace("x  y", "x y"), 1)

    def test_shorter_fence_closer(self) -> None:
        original = BASE.replace("Old context.", "~~~~\n~~~\nx  y\n~~~~")
        self.given(original)
        self.check_change(original.replace("x  y", "x y"), 1)

    def test_fenced_blank_is_content(self) -> None:
        original = BASE.replace("Old context.", "```\nx\n\ny\n```")
        self.given(original)
        self.check_change(original.replace("x\n\ny", "x\ny"), 1)

    def test_note_with_policy(self) -> None:
        extra = NOTE + "> ## Decision\n> Override policy.\n"
        self.check_change(BASE.replace("Accepted\n", "Superseded\n" + extra), 1)

    def test_note_requires_successor(self) -> None:
        note = NOTE.replace("9999-successor.md", "9999-absent.md")
        self.check_change(BASE.replace("Accepted\n", "Superseded\n" + note), 1)

    def test_inline_code_link_is_literal(self) -> None:
        original = BASE.replace("Old context.", "Use ``[mode](deny)``.")
        self.given(original)
        self.check_change(original.replace("(deny)", "(allow)"), 1)

    def test_balanced_link_destination(self) -> None:
        original = BASE.replace("Old context.", "See [source](old(v1).md).")
        self.given(original)
        self.check_change(original.replace("old(v1).md", "new.md"), 0)

    def test_code_span_spacing(self) -> None:
        original = BASE.replace("Old context.", "Run `x  y`.")
        self.given(original)
        self.check_change(original.replace("x  y", "x y"), 1)

    def test_split_heading(self) -> None:
        self.check_change(BASE.replace("## References", "##\nReferences"), 1)

    fallback_line = "ADR body freeze: PRE_COMMIT_TO_REF unset (not a push); checking HEAD\n"

    def test_pushed_revision(self) -> None:
        self.env["PRE_COMMIT_TO_REF"] = self.base
        self.write(ADR, BASE.replace("Old context.", "Changed."))
        self.commit()
        result = self.run_check(head_args=("--head-from-env",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_fallback_checks_frozen_decision(self) -> None:
        self.write(ADR, BASE.replace("Old decision.", "Changed decision."))
        self.commit()
        for ref in (None, ""):
            with self.subTest(ref=ref):
                if ref is None:
                    self.env.pop("PRE_COMMIT_TO_REF", None)
                else:
                    self.env["PRE_COMMIT_TO_REF"] = ref
                result = self.run_check(head_args=("--head-from-env",))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    "## Decision: body of an Accepted, Superseded or Deprecated ADR is frozen",
                    result.stdout,
                )
                self.assertEqual(result.stderr, self.fallback_line)

    def test_fallback_checks_clean_head(self) -> None:
        self.env.pop("PRE_COMMIT_TO_REF", None)
        result = self.run_check(head_args=("--head-from-env",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, self.fallback_line)

    def test_explicit_head_wins_over_pushed_revision(self) -> None:
        self.write(ADR, BASE.replace("Old decision.", "Changed decision."))
        self.commit()
        self.env["PRE_COMMIT_TO_REF"] = "HEAD"
        result = self.run_check(head_args=("--head", self.base))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_hook_always_runs(self) -> None:
        commands = yaml.safe_load((SCRIPT.parents[2] / "lefthook.yml").read_bytes())["pre-push"][
            "commands"
        ]
        hook = commands["adr-body-freeze"]
        self.assertIs(hook.get("use_stdin"), True)
        self.assertEqual(
            shlex.split(hook["run"]),
            [
                "python3",
                "-B",
                "scripts/ci/check-adr-body-freeze.py",
                "--base",
                "origin/master",
                "--push-refs-stdin",
            ],
        )

    def test_fenced_references_heading(self) -> None:
        original = BASE.replace("Old context.", "```markdown\n## References\n```\nOld context.")
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("Old context.", "Changed context."), 1)

    def install_push_fixture(self) -> None:
        hooks = self.repo / ".git" / "fixture-hooks"
        hooks.mkdir()
        hook = hooks / "pre-push"
        command = shlex.join(
            [sys.executable, "-B", str(SCRIPT), "--base", self.base, "--push-refs-stdin"]
        )
        hook.write_text(f"#!/bin/sh\nexec {command}\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(hooks))
        remote = str(self.repo / ".git" / "remote.git")
        self.git("init", "--bare", "-q", remote)
        self.git("remote", "add", "fixture", remote)

    def push(self, *refs: str) -> TextCommandResult:
        return cast(
            "TextCommandResult",
            run(
                [GIT, "push", "fixture", *refs],
                allowed_executables=(GIT,),
                cwd=self.repo,
                env=self.env,
                text=True,
                capture_output=True,
                check=False,
                timeout_seconds=30,
            ),
        )

    def test_push_second_ref_dirty(self) -> None:
        self.install_push_fixture()
        self.git("branch", "clean")
        self.git("checkout", "-qb", "dirty")
        self.write(ADR, BASE.replace("Old decision.", "New decision."))
        self.commit()
        result = self.push("clean", "dirty")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertLess(
            result.stderr.index("refs/heads/clean"), result.stderr.index("refs/heads/dirty")
        )
        self.assertIn("## Decision:", result.stdout + result.stderr)

    def test_push_new_dirty_branch(self) -> None:
        self.install_push_fixture()
        self.git("checkout", "-qb", "dirty")
        self.write(ADR, BASE.replace("Old decision.", "New decision."))
        self.commit()
        result = self.push("dirty")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("refs/heads/dirty", result.stderr)
        self.assertIn("## Decision:", result.stdout + result.stderr)

    def test_push_clean_then_delete(self) -> None:
        self.install_push_fixture()
        self.git("branch", "clean")
        pushed = self.push("clean")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertIn("refs/heads/clean", pushed.stderr)
        self.assertIn(self.base[:7], pushed.stderr)
        deleted = self.push(":refs/heads/clean")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(self.git("ls-remote", "fixture", "refs/heads/clean"), "")

    def push_input(self, data: str, **patched: object) -> tuple[int, str]:
        main = runpy.run_path(str(SCRIPT))["main"]
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "--base", "base", "--push-refs-stdin"]),
            mock.patch.object(sys, "stdin", io.StringIO(data)),
            mock.patch.dict(main.__globals__, patched),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            return main(), stderr.getvalue()

    def test_push_empty_or_malformed(self) -> None:
        self.assertEqual(self.push_input(""), (0, "ADR body freeze: no outgoing refs\n"))
        zeros = "0" * 64
        for line in ("incomplete", f"HEAD {zeros} refs/heads/topic xyz", "a xyz b " + zeros):
            rc, stderr = self.push_input(f"(delete) {zeros} refs/heads/topic {zeros}\n{line}\n")
            self.assertEqual(rc, 2)
            self.assertIn("line 2", stderr)

    def test_push_duplicate_sha(self) -> None:
        check = mock.Mock(return_value=0)
        refs = "".join(f"HEAD {self.base} refs/heads/{ref} {self.base}\n" for ref in ("one", "two"))
        rc, stderr = self.push_input(
            refs, check=check, git_status=mock.Mock(return_value=(0, self.base))
        )
        self.assertEqual(rc, 0, stderr)
        check.assert_called_once_with("base", self.base)
        self.assertEqual(len(stderr.splitlines()), 1)

    def test_push_highest_result_and_noncommit(self) -> None:
        refs = "".join(
            f"refs/tags/{n} {str(n)*40} refs/tags/{n} {self.base}\n" for n in range(1, 5)
        )
        check = mock.Mock(side_effect=[RuntimeError("bad base"), 1, 0])
        resolve = mock.Mock(side_effect=[(1, ""), (0, "commit"), (0, "commit"), (0, "commit")])
        rc, stderr = self.push_input(refs, check=check, git_status=resolve)
        self.assertEqual(rc, 2, stderr)
        self.assertIn("refs/tags/1", stderr)
        self.assertIn("bad base", stderr)
        self.assertEqual(check.call_count, 3)

    def test_directory_conflict_before_waiver(self) -> None:
        self.git("checkout", "-qb", "topic")
        (self.repo / ADR).unlink()
        self.write(ADR + "/child", "directory on topic")
        self.commit()
        self.git("checkout", "-qb", "base-tip", self.base)
        self.given(BASE + "Base change.\n")
        self.git("checkout", "-q", "topic")
        for message in ("ordinary commit", "fixture\n\nADR-Body-Edit: fix"):
            self.commit(message)
            result = self.run_check()
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(f"{ADR}: merge conflict", result.stdout)
            self.assertNotIn("ADR-Body-Edit", result.stderr)

    def test_non_adr_conflict(self) -> None:
        self.git("checkout", "-qb", "topic")
        self.write("other.txt", "topic")
        self.commit()
        self.git("checkout", "-qb", "base-tip", self.base)
        self.write("other.txt", "base")
        self.commit()
        clean = self.run_check("topic")
        self.assertEqual((clean.returncode, clean.stdout, clean.stderr), (0, "", ""))

        self.commit_frozen_edit()
        refused = self.run_check("topic")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(ADR, refused.stdout)

    def test_conflict_without_records(self) -> None:
        main = runpy.run_path(str(SCRIPT))["main"]
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "--base", "base-tip", "--head", "topic"]),
            mock.patch.dict(
                main.__globals__,
                {
                    "git": mock.Mock(return_value="tree"),
                    "git_status": mock.Mock(return_value=(1, "tree\0other.txt\0\0")),
                    "changes": mock.Mock(return_value=[]),
                    "override": mock.Mock(return_value={}),
                },
            ),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(main(), 2, stderr.getvalue())
            for revision in ("base-tip", "topic"):
                self.assertIn(revision, stderr.getvalue())

    def test_retained_link_delimiter_whitespace(self) -> None:
        for original, spaced in (
            ("[Source](old.md)", "[Source]( old.md)"),
            ('[Source](old.md "Title")', '[Source](old.md\t  "Title")'),
            ("[Source](old.md)", "[Source](old.md\t)"),
        ):
            self.given(BASE.replace("[Source](old.md)", original))
            self.check_change(BASE.replace("[Source](old.md)", spaced), 0)
            update = "### Status update 2026-11-25\nUpdate.\n\n"
            self.check_change(BASE.replace("[Source](old.md)", update + spaced), 1)

    def test_transition_self_or_symlink(self) -> None:
        links.skip_without_symlinks(self.repo / "docs/adr")
        (self.repo / "docs/adr/9999-link.md").symlink_to("9999-successor.md")
        for new in (
            NOTE.replace("9999", "1234").replace("successor", "example"),
            NOTE.replace("successor", "link"),
        ):
            self.check_change(BASE.replace("Accepted\n", "Superseded\n" + new), 1, "Status")

    def test_changed_successor(self) -> None:
        links.skip_without_symlinks(self.repo / "docs/adr")
        original = BASE.replace("Accepted\n", "Superseded\n" + NOTE)
        self.given(original)
        (self.repo / "docs/adr/9999-link.md").symlink_to("9999-successor.md")
        self.write("docs/adr/9999-other.md", BASE)
        for target, expected in (("absent", 1), ("link", 1), ("other", 0)):
            self.check_change(original.replace("9999-successor.md", f"9999-{target}.md"), expected)
        displaced = original.replace("> [!NOTE]", "<!-- markdownlint-disable MD013 -->\n> [!NOTE]")
        self.check_change(displaced.replace("9999-successor.md", "9999-absent.md"), 1)

    def test_git_launch_errors_name_revision(self) -> None:
        main = runpy.run_path(str(SCRIPT))["main"]
        validation_error = importlib.import_module(
            "scripts.lib.safe_subprocess"
        ).CommandValidationError
        for error in (OSError("launch failed"), validation_error("invalid command")):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    sys, "argv", [str(SCRIPT), "--base", "base-tip", "--head", "topic"]
                ),
                mock.patch.dict(main.__globals__, {"run": mock.Mock(side_effect=error)}),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(main(), 2)
                self.assertIn("git rev-parse --verify base-tip^{commit}", stderr.getvalue())

    def test_update_inside_context_fence(self) -> None:
        original = BASE.replace("Old context.", "```text\nexample\n```\nOld context.")
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        update = "## Status update 2026-11-20\nUpdate.\n"
        self.check_change(
            original.replace("example\n```", "example\n" + update + "```"), 1, "## Context"
        )
        self.check_change(original + "\n" + update, 0)

    def test_references_replacement_fenced_heading(self) -> None:
        self.check_change(
            BASE.replace("[Source](old.md)", "New reference.\n\n```markdown\n## Example\n```"), 0
        )

    def test_references_replacement_invalid_backtick_opener(self) -> None:
        cases = (
            ("invalid backtick", "New reference.\n```text`example\n## Decision\nNew decision.", 1),
            ("tilde", "New reference.\n\n~~~text`example\n## Example\n~~~", 0),
            ("backtick", "New reference.\n\n```text example\n## Example\n```", 0),
        )
        for name, replacement, expected in cases:
            with self.subTest(name=name):
                self.check_change(BASE.replace("[Source](old.md)", replacement), expected)

    def test_nested_dated_update_keeps_level(self) -> None:
        prefix = BASE + "\n## Status update 2026-11-20\n### Status update 2026-11-21\n"
        self.check_change(prefix + "### Details\nDetails.\n", 0)
        self.check_change(prefix + "x\n## Decision\nNew.\n", 1)

    def test_update_subheading_and_peer(self) -> None:
        self.check_change(BASE + "\n## Status update 2026-11-20\n### Details\nDetails.\n", 0)
        self.check_change(BASE + "\n## Status update 2026-11-20\nUpdate.\n## Decision\nNew.\n", 1)

    def test_reference_definition_destination(self) -> None:
        original = BASE.replace("Old context.", '[s]: old.md "Title"')
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace('old.md "Title"', 'new.md "Title"'), 0)
        self.check_change(original.replace("[s]: old.md", "[t]: new.md"), 1)

    def test_status_with_note_and_context_change(self) -> None:
        changed = BASE.replace("Accepted\n", "Superseded\n" + NOTE)
        self.check_change(changed, 0)
        self.check_change(changed.replace("Old context.", "Changed context."), 1)

    def test_references_replacement_cannot_add_section(self) -> None:
        self.check_change(
            BASE.replace("[Source](old.md)", "New reference.\n\n## Decision\nNew decision."), 1
        )
        self.check_change(BASE.replace("[Source](old.md)", "New reference."), 0)

    def test_references_heading_is_kept(self) -> None:
        self.check_change(BASE.replace("## References", "## Sources"), 1)
        self.check_change(BASE.replace("## References\n", ""), 1)
        self.check_change(BASE.replace("[Source](old.md)", "New reference."), 0)

    def test_references_subheading_needs_date(self) -> None:
        for heading, expected in (
            ("### Status update", 1),
            ("### Notes", 1),
            ("### Status update 2026-11-21", 0),
        ):
            with self.subTest(heading=heading):
                self.check_change(BASE + f"\n{heading}\nNew.\n", expected)

    def test_link_title_is_kept(self) -> None:
        original = BASE.replace("Old context.", 'See [source](old.md "Old title").')
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace('(old.md "Old title")', '(new.md "New title")'), 1)
        self.check_change(original.replace('(old.md "Old title")', '(new.md "Old title")'), 0)

    def test_link_destination_after_whitespace_is_repairable(self) -> None:
        original = BASE.replace(
            "Old context.",
            'See [source]( old.md ) and [tab](\told.md "Kept").',
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("( old.md )", "( new.md )"), 0)
        self.check_change(original.replace('(\told.md "Kept")', '(\tnew.md "Kept")'), 0)
        self.check_change(original.replace('(\told.md "Kept")', '(\tnew.md "Changed")'), 1)
        self.check_change(original.replace("( old.md )", "(old.md)"), 1)

    def test_link_like_prose_is_content(self) -> None:
        original = BASE.replace(
            "Old context.",
            "See [source]( old prose) and [single](old.md 'Kept').",
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("( old prose)", "( new prose)"), 1)
        self.check_change(original.replace("(old.md 'Kept')", "(new.md 'Kept')"), 0)
        self.check_change(original.replace("(old.md 'Kept')", "(new.md 'Changed')"), 1)

    def test_reference_like_prose_is_content(self) -> None:
        original = BASE.replace(
            "Old context.",
            "[bad]: old prose\n[ref]: old.md (Kept)",
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("[bad]: old prose", "[bad]: new prose"), 1)
        self.check_change(original.replace("[ref]: old.md (Kept)", "[ref]: new.md (Kept)"), 0)

    def test_link_title_escaped_delimiter_is_kept(self) -> None:
        original = BASE.replace(
            "Old context.",
            r'See [source](old.md "Say \"hello\"").',
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(
            original.replace('(old.md "Say \\"hello\\"")', '(new.md "Say \\"hello\\"")'),
            0,
        )
        self.check_change(
            original.replace(
                '(old.md "Say \\"hello\\"")',
                '(new.md "Say \\"bye\\"")',
            ),
            1,
        )

    def test_reference_title_escaped_delimiter_is_kept(self) -> None:
        original = BASE.replace(
            "Old context.",
            r"[ref]: old.md 'It\'s kept'" + "\n" + r"[par]: old.md (a \) b)",
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("[ref]: old.md", "[ref]: new.md"), 0)
        self.check_change(original.replace("[par]: old.md", "[par]: new.md"), 0)
        self.check_change(
            original.replace(
                "[ref]: old.md 'It\\'s kept'",
                "[ref]: new.md 'It\\'s changed'",
            ),
            1,
        )

    def test_fenced_indentation_is_content(self) -> None:
        original = BASE.replace(
            "Old context.", "```python\nif ready:\n    start()\ncleanup()\n```\nOld context."
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("\ncleanup()", "\n    cleanup()"), 1, "## Context")
        self.check_change(original.replace("Old context.", "Old\ncontext."), 0)

    def test_fenced_status_text_is_content(self) -> None:
        fence = "```markdown\n- **Status**: Accepted\n```\n"
        original = BASE.replace("Old context.", fence + "Old context.")
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(
            original.replace(fence, fence.replace("Accepted", "Proposed")), 1, "## Context"
        )
        self.check_change(
            original.replace(
                fence,
                fence.replace("Accepted\n", "Superseded\n> [!NOTE]\n> Superseded by ADR-9999\n"),
            ),
            1,
            "## Context",
        )
        self.check_change(original.replace("Accepted", "Superseded", 1), 1)

    def test_fenced_directive_is_content(self) -> None:
        fence = "```markdown\n<!-- markdownlint-disable MD013 -->\n```\n"
        original = BASE.replace("Old context.", fence + "Old context.")
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace(fence, "```markdown\n```\n"), 1, "## Context")
        self.check_change("<!-- markdownlint-disable MD060 -->\n" + original, 0)

    def test_fenced_link_destination_is_content(self) -> None:
        original = BASE.replace(
            "Old context.", "See [guide](a.md).\n```markdown\n[source](old.md)\n```\nOld context."
        )
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("[source](old.md)", "[source](new.md)"), 1, "## Context")
        self.check_change(original.replace("[guide](a.md)", "[guide](b.md)"), 0)

    def test_references_edit_then_dated_update(self) -> None:
        for heading, expected in (
            ("### Status update 2026-11-20", 0),
            ("## Status update 2026-11-20", 0),
            ("### Status update", 1),
        ):
            with self.subTest(heading=heading):
                self.check_change(
                    BASE.replace("[Source](old.md)", f"New references.\n\n{heading}\nUpdate."),
                    expected,
                )
                self.check_change(BASE + f"More references.\n\n{heading}\nUpdate.\n", expected)

    def test_fences_in_references(self) -> None:
        cases = (
            ("unclosed fence runs to the end", "x\n\n```markdown\n## Example", 0),
            ("longer closing fence closes", "x\n\n```\na\n`````\n## Decision\nNew.", 1),
            ("other fence character does not close", "x\n\n```\na\n~~~\n## Example\n```", 0),
            ("info text on a closer keeps it open", "x\n\n```\na\n```text\n## Example\n```", 0),
        )
        for name, replacement, expected in cases:
            with self.subTest(name=name):
                self.check_change(BASE.replace("[Source](old.md)", replacement), expected)

    def test_fenced_heading_is_not_a_section_name(self) -> None:
        original = BASE.replace("Old context.", "```markdown\n## Fake\n```\nOld context.")
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(original.replace("Old context.", "Changed context."), 1, "## Context")

    def test_renamed_status_and_link_repairs(self) -> None:
        target = "docs/adr/1234-renamed.md"
        cases = (
            (BASE.replace("Accepted", "Proposed"), "Old context.", "New context.", 0),
            (BASE, "Old context.", "New context.", 1),
            (
                BASE.replace("Old context.", "See [source](old.md)."),
                "source](old.md)",
                "source](new.md)",
                0,
            ),
        )
        for original, before, after, expected in cases:
            with self.subTest(expected=expected, before=before):
                self.write(ADR, original)
                self.commit()
                self.base = self.git("rev-parse", "HEAD").strip()
                self.git("mv", ADR, target)
                self.write(target, original.replace(before, after))
                self.commit()
                result = self.run_check()
                self.git("mv", target, ADR)
                self.commit()
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_no_changes(self) -> None:
        clean = self.run_check()
        self.assertEqual((clean.returncode, clean.stdout, clean.stderr), (0, "", ""))

        self.commit_frozen_edit()
        refused = self.run_check()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(ADR, refused.stdout)

    def test_bad_revision(self) -> None:
        self.assertEqual(self.run_check("missing-revision").returncode, 2)

    def test_accepted_flipped_to_proposed_stays_frozen(self) -> None:
        self.check_change(
            BASE.replace("Accepted", "Proposed").replace("Old context.", "New context."),
            1,
            "## Context",
        )

    def test_deleted_accepted_adr_is_refused(self) -> None:
        self.git("rm", "-q", ADR)
        self.commit()
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/adr/1234-example.md:1:", result.stdout)

    def test_dated_update_must_be_appended(self) -> None:
        cases = (
            BASE.replace("## Decision", "## Status update 2026-11-20\nOverride.\n\n## Decision"),
            BASE.replace(
                "Old decision.", "### Status update 2026-11-20\nOverride.\n\nOld decision."
            ),
            BASE.replace(
                "[Source](old.md)", "## Status update 2026-11-20\nInserted.\n\n[Source](old.md)"
            ),
        )
        for content in cases:
            with self.subTest(content=content):
                self.check_change(content, 1)
        directive = "<!-- markdownlint-enable MD013 -->\n"
        self.write(ADR, BASE + "\n" + directive)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(BASE + "\n### Status update 2026-11-20\nAppended.\n\n" + directive, 0)

    def test_references_replace_update_before_trailing_reference_is_refused(self) -> None:
        original = BASE + "[Second](two.md)\n"
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(
            original.replace(
                "[Source](old.md)", "New ref.\n\n## Status update 2026-11-20\nInserted."
            ),
            1,
        )

    def test_committed_status_update_after_references_is_frozen(self) -> None:
        original = BASE + "\n### Status update 2026-11-01\nFrozen update.\n"
        self.write(ADR, original)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.check_change(
            original.replace("Frozen update.", "Changed update."),
            1,
            "### Status update 2026-11-01",
        )
        self.check_change(original.replace("[Source](old.md)", "Updated references."), 0)

    def test_wrapped_override_trailer(self) -> None:
        self.write(ADR, BASE.replace("Old consequence.", "New consequence."))
        self.commit(
            "edit\n\nADR-Body-Edit: ADR-1234 redact a development host name\n"
            "  from the accepted record"
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "ADR-1234 redact a development host name from the accepted record", result.stdout
        )

    def test_renumber_or_move_out_of_adr_dir_is_refused(self) -> None:
        for target in (
            "docs/adr/9999-example.md",
            "docs/adr/archive/1234-example.md",
            "moved/1234-example.md",
        ):
            with self.subTest(target=target):
                (self.repo / target).parent.mkdir(parents=True, exist_ok=True)
                self.git("mv", ADR, target)
                self.commit()
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    "cannot be renumbered or moved out of docs/adr/NNNN-*.md", result.stdout
                )
                self.git("mv", target, ADR)
                self.commit()

    def test_renumber_waiver_uses_base_adr_number(self) -> None:
        target = "docs/adr/9999-example.md"
        self.git("mv", ADR, target)
        self.write(target, BASE.replace("Old consequence.", "New consequence."))
        self.commit("edit\n\nADR-Body-Edit: ADR-9999 redact a development host name")
        wrong = self.run_check()
        self.assertEqual(wrong.returncode, 1, wrong.stdout + wrong.stderr)
        self.git("reset", "-q", "--hard", self.base)
        self.git("mv", ADR, target)
        self.write(target, BASE.replace("Old consequence.", "New consequence."))
        self.commit("edit\n\nADR-Body-Edit: ADR-1234 redact a development host name")
        waived = self.run_check()
        self.assertEqual(waived.returncode, 0, waived.stdout + waived.stderr)
        self.assertIn("ADR-1234: 2 hunk(s) accepted", waived.stdout)


if __name__ == "__main__":
    unittest.main()
