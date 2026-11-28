#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Shape of .github/workflows/python-lock-refresh.yml (ADR-3560).

Positive: the path filter equals the manifest inputs read through the lock checker's loader;
the workflow is Renovate-only, read-only by default, pinned by SHA, capped, serialised per ref,
regenerates the locks in a secret-free job and publishes them from a second job that alone
mints the release-bot token with contents: write, restores the staged locks under the manifest
allow-list and pushes through the shared script (#3702).
Negative: planted copies lose a path, gain a pattern, carry an expression in ``run:``, use an
unpinned action, push with a force flag, write a second generator, read a secret in the
regeneration job (a step, its job ``env:`` or the workflow ``env:``), mint the token without
the contents grant, restore outside the manifest allow-list or lose the guard step; each is
reported.
Boundary: both lock refresh workflows commit and push only through
scripts/ci/lock_refresh_publish.py.
"""

from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ci import check_python_dependency_locks as lock_checker  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
PYTHON_WORKFLOW = WORKFLOWS / "python-lock-refresh.yml"
NEO_WORKFLOW = WORKFLOWS / "intel-neo-lock-refresh.yml"
SHA_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
FORCE_RE = re.compile(r"(?:^|\s)(?:--force\S*|-f|\+HEAD\S*)(?:\s|$)")
GENERATOR_RE = re.compile(r"pip-compile|uv\s+pip\s+compile|uv\s+lock")
MINT_ACTION = "actions/create-github-app-token@"
STAGE_COMMAND = "python3 scripts/ci/lock_refresh_publish.py stage"
RESTORE_COMMAND = "python3 scripts/ci/lock_refresh_publish.py restore"

REGENERATE_JOB = "regenerate"
PUBLISH_JOB = "publish-locks"

PUBLISH_COMMAND = "python3 scripts/ci/lock_refresh_publish.py publish"
ORDER_FINDING = (
    "guard, tooling install, bootstrap, bootstrap install, write, stage, restore "
    "and publish are missing or out of order"
)
REQUIRED_STEP_FINDING = "bootstrap and write must run unconditionally and propagate failures"
SHELL_FINDING = "bootstrap and write must set shell: bash"
MINT_FINDING = "only the publish job may hold the app-token step"
MINT_PERMISSION_FINDING = "the mint step must grant the token contents: write"
SEALED_FINDING = "the regeneration job must not reference secrets"
NEEDS_FINDING = "the publish job must need the regeneration job"
ALLOW_LIST_FINDING = "the restore step must take its allow-list from the manifest outputs"


def load(path: Path) -> dict[str, Any]:
    """Parse a workflow; YAML 1.1 reads the ``on`` key as ``True``, so fold it back."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    if True in data:
        data["on"] = data.pop(True)
    return data


def steps_of(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    """The steps of one job."""
    return list(workflow["jobs"][job]["steps"])


def all_steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every (job, step) pair, in job order."""
    return [
        (name, step) for name, body in workflow["jobs"].items() for step in body.get("steps", [])
    ]


def run_texts(workflow: dict[str, Any]) -> list[str]:
    """Every ``run:`` script of the workflow."""
    return [step["run"] for _, step in all_steps(workflow) if "run" in step]


def references_secret(value: Any) -> bool:
    """Whether any scalar of the value tree reads ``secrets.``.

    The walk uses an explicit bounded stack instead of a recursive scan, so a
    deeply nested value cannot exhaust the interpreter stack (HISS-01).
    """
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if "secrets." in current:
                return True
        elif isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def sealed_findings(job: dict[str, Any]) -> list[str]:
    """The regeneration job stays sealed: no scalar of it may read ``secrets.``.

    The whole job object is scanned, so a secret planted at the job's ``env:``
    level is caught as well as one inside a step.
    """
    return [SEALED_FINDING] if references_secret(job) else []


def job_findings(jobs: dict[str, Any]) -> list[str]:
    """The per-job shape: presence, timeout, permissions, sealing, needs and mint holder."""
    found: list[str] = []
    for job in (REGENERATE_JOB, PUBLISH_JOB):
        body = jobs.get(job)
        if not isinstance(body, dict):
            found.append(f"the {job} job is missing")
            continue
        if not isinstance(body.get("timeout-minutes"), int):
            found.append(f"the {job} job needs a literal timeout-minutes")
        if body.get("permissions") != {"contents": "read"}:
            found.append(f"the {job} job permissions must be contents: read")
    if jobs.get(PUBLISH_JOB, {}).get("needs") != REGENERATE_JOB:
        found.append(NEEDS_FINDING)
    token_holders = [
        name
        for name, body in jobs.items()
        if isinstance(body, dict)
        and any(str(step.get("uses", "")).startswith(MINT_ACTION) for step in body.get("steps", []))
    ]
    if token_holders != [PUBLISH_JOB]:
        found.append(MINT_FINDING)
    regenerate = jobs.get(REGENERATE_JOB, {})
    if isinstance(regenerate, dict):
        found += sealed_findings(regenerate)
    publish = jobs.get(PUBLISH_JOB, {})
    if isinstance(publish, dict):
        found += mint_findings(publish.get("steps", []))
    return found


def mint_findings(publish_steps: list[dict[str, Any]]) -> list[str]:
    """The token mint must ask the App for exactly the permission the push needs."""
    mint = next(
        (step for step in publish_steps if str(step.get("uses", "")).startswith(MINT_ACTION)), None
    )
    if mint is not None and mint.get("with", {}).get("permission-contents") != "write":
        return [MINT_PERMISSION_FINDING]
    return []


def shape_findings(workflow: dict[str, Any], inputs: list[str]) -> list[str]:
    """Return what deviates from the required shape; an empty list is a pass."""
    found: list[str] = []
    push = workflow["on"]["push"]
    if push.get("branches") != ["renovate/**"]:
        found.append("branches must be exactly renovate/**")
    paths = push.get("paths", [])
    if sorted(paths) != sorted(inputs) or len(set(paths)) != len(paths):
        found.append("path filter is not the manifest input set")
    if workflow.get("permissions") != {"contents": "read"}:
        found.append("top-level permissions must be contents: read")
    group = workflow.get("concurrency", {}).get("group", "")
    if "github.ref" not in group or workflow["concurrency"].get("cancel-in-progress") is not False:
        found.append("concurrency must be per ref without cancel-in-progress")
    jobs = workflow.get("jobs", {})
    found += job_findings(jobs)
    if references_secret(workflow.get("env")):
        found.append(SEALED_FINDING)
    found += [
        f"unpinned action {step['uses']}"
        for _, step in all_steps(workflow)
        if "uses" in step and not SHA_RE.match(step["uses"])
    ]
    texts = run_texts(workflow)
    found += ["expression inside run:" for text in texts if "${{" in text]
    found += ["force push" for text in texts if FORCE_RE.search(text)]
    found += ["second generator" for text in texts if GENERATOR_RE.search(text)]
    found += restore_findings(workflow)
    found += publish_findings(workflow)
    found += guard_order_findings(workflow)
    return found


def restore_findings(workflow: dict[str, Any]) -> list[str]:
    """The restore step must accept exactly the checker's manifest outputs."""
    step = next(
        (
            step
            for step in steps_of(workflow, PUBLISH_JOB)
            if RESTORE_COMMAND in step.get("run", "")
        ),
        None,
    )
    if step is None:
        return [ALLOW_LIST_FINDING]
    text = step["run"]
    if "check_python_dependency_locks.py outputs" not in text or '-- "${outputs[@]}"' not in text:
        return [ALLOW_LIST_FINDING]
    return []


def publish_findings(workflow: dict[str, Any]) -> list[str]:
    """The publish step must read the manifest outputs and name the bot commit."""
    text = next(
        (
            step.get("run", "")
            for step in steps_of(workflow, PUBLISH_JOB)
            if PUBLISH_COMMAND in step.get("run", "")
        ),
        "",
    )
    if not text:
        return ["the publish step is missing"]
    problems: list[str] = []
    if "check_python_dependency_locks.py outputs" not in text or '-- "${outputs[@]}"' not in text:
        problems.append("the publish step must push the checker's manifest outputs")
    if "--check-strays-when-unchanged" not in text:
        problems.append("the publish step must check strays when unchanged")
    dco = (ROOT / "scripts" / "ci" / "check-dco.py").read_text(encoding="utf-8")
    subject = re.search(r'PYTHON_LOCK_SUBJECT = "([^"]+)"', dco)
    if subject is None or f'--subject "{subject.group(1)}"' not in text:
        problems.append("the publish step must use the DCO-check subject")
    return problems


def unconditional_refresh_command(
    step: dict[str, Any], line: int, command: str, install: str
) -> bool:
    """Only the reviewed setup commands may precede the required invocation."""
    if "if" in step or "continue-on-error" in step:
        return False
    lines = step["run"].splitlines()
    prefix = lines[:line]
    commands = [
        text.strip() for text in prefix if text.strip() and not text.lstrip().startswith("#")
    ]
    setup = ["set -euo pipefail"]
    if command.endswith("bootstrap-uv"):
        setup.append('bootstrap="$RUNNER_TEMP/uv-bootstrap.txt"')
        command += ' --output "$bootstrap"'
        block = ['if [ -s "$bootstrap" ]; then', install, "fi"]
        if [text.strip() for text in lines[line : line + len(block)]] != block:
            return False
    return commands == [*setup, command]


def guard_order_findings(workflow: dict[str, Any]) -> list[str]:
    """Check command order across the two jobs; keep the bootstrap install in its producer's shell."""
    bootstrap = "python3 scripts/ci/check_python_dependency_locks.py bootstrap-uv"
    install = 'python -m pip install --no-build-isolation --require-hashes -r "$bootstrap"'
    write = "python3 scripts/ci/check_python_dependency_locks.py write"
    job_markers = (
        (
            REGENERATE_JOB,
            (
                "python3 scripts/ci/lock_refresh_publish.py refuse-non-renovate",
                "python -m pip install --require-hashes -r requirements/locks/package-build.txt",
                "python -m pip install --no-build-isolation --require-hashes -r requirements/locks/tooling-tests.txt",
                bootstrap,
                install,
                write,
                STAGE_COMMAND,
            ),
        ),
        (PUBLISH_JOB, (RESTORE_COMMAND, "python3 scripts/ci/lock_refresh_publish.py publish")),
    )
    positions: dict[str, tuple[int, int, int]] = {}
    for rank, (job, markers) in enumerate(job_markers):
        for index, step in enumerate(steps_of(workflow, job)):
            for line, command in lock_checker.logical_lines(step.get("run", "")):
                marker = (
                    command.partition(" --")[0]
                    if command.startswith("python3 scripts/ci/")
                    else command
                )
                if marker in markers:
                    if marker in positions:
                        return [ORDER_FINDING]
                    positions[marker] = (rank, index, line)
    flat = [marker for _, markers in job_markers for marker in markers]
    if (
        sorted(positions, key=positions.__getitem__) != flat
        or positions[bootstrap][:2] != positions[install][:2]
    ):
        return [ORDER_FINDING]
    for marker in (bootstrap, write):
        _, index, line = positions[marker]
        step = steps_of(workflow, REGENERATE_JOB)[index]
        if step.get("shell") != "bash":
            return [SHELL_FINDING]
        if not unconditional_refresh_command(step, line, marker, install):
            return [REQUIRED_STEP_FINDING]
    return []


class PythonLockRefreshWorkflowTest(unittest.TestCase):
    """The checked-in workflow and planted variants of it."""

    def setUp(self) -> None:
        """Load the workflow and the manifest input set."""
        self.workflow = load(PYTHON_WORKFLOW)
        self.inputs = lock_checker.manifest_inputs(ROOT)

    def planted(self) -> dict[str, Any]:
        """A deep copy to plant a defect in."""
        return copy.deepcopy(self.workflow)

    def regenerate_steps(self) -> list[dict[str, Any]]:
        """The steps of the secret-free regeneration job."""
        return steps_of(self.workflow, REGENERATE_JOB)

    def publish_steps(self) -> list[dict[str, Any]]:
        """The steps of the publish job."""
        return steps_of(self.workflow, PUBLISH_JOB)

    def test_checked_in_workflow_has_the_required_shape(self) -> None:
        """Positive: no finding, and the filter is the manifest input set."""
        self.assertEqual(shape_findings(self.workflow, self.inputs), [])
        paths = self.workflow["on"]["push"]["paths"]
        self.assertEqual(sorted(paths), sorted(self.inputs))
        self.assertGreater(len(paths), 20)

    def test_publish_step_reads_outputs_from_the_manifest_and_names_the_bot_commit(self) -> None:
        """Positive: the publish step alone reads the manifest outputs and names the subject."""
        self.assertEqual(publish_findings(self.workflow), [])

    def test_planted_publish_step_reading_the_restore_step_is_reported(self) -> None:
        """Negative: a publish step that leans on other steps' texts is still checked alone."""
        wf = self.planted()
        publish = next(
            step for step in steps_of(wf, PUBLISH_JOB) if PUBLISH_COMMAND in step.get("run", "")
        )
        publish["run"] = (
            publish["run"]
            .replace(
                "python3 scripts/ci/check_python_dependency_locks.py outputs"
                ' >"$outputs_file"\nmapfile -t outputs <"$outputs_file"\n',
                "",
            )
            .replace('-- "${outputs[@]}"', "-- requirements/locks/*.txt")
        )
        problems = publish_findings(wf)
        self.assertIn("the publish step must push the checker's manifest outputs", problems)
        self.assertEqual(publish_findings(self.workflow), [])

    def test_checkout_keeps_no_credentials_and_the_token_stays_behind_the_publish_job(self) -> None:
        """Positive: both checkouts drop credentials; restore precedes mint precedes push."""
        for job in (REGENERATE_JOB, PUBLISH_JOB):
            checkout = next(
                step
                for step in steps_of(self.workflow, job)
                if step.get("uses", "").startswith("actions/checkout@")
            )
            self.assertIs(checkout["with"]["persist-credentials"], False)
        steps = self.publish_steps()
        restore = next(i for i, s in enumerate(steps) if RESTORE_COMMAND in s.get("run", ""))
        mint = next(
            i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(MINT_ACTION)
        )
        push = next(
            i for i, s in enumerate(steps) if "lock_refresh_publish.py publish" in s.get("run", "")
        )
        self.assertLess(restore, mint)
        self.assertLess(mint, push)

    def test_only_the_publish_job_holds_the_app_token_step(self) -> None:
        """Positive: one mint step, in the publish job; the regeneration job holds none."""
        mints = [
            step
            for step in self.publish_steps()
            if str(step.get("uses", "")).startswith(MINT_ACTION)
        ]
        self.assertEqual(len(mints), 1)
        for step in self.regenerate_steps():
            self.assertFalse(
                str(step.get("uses", "")).startswith(MINT_ACTION), step.get("name", "")
            )

    def test_the_mint_step_grants_the_token_contents_write(self) -> None:
        """Positive: the mint step requests contents: write for the push it serves."""
        mint = next(
            step
            for step in self.publish_steps()
            if str(step.get("uses", "")).startswith(MINT_ACTION)
        )
        self.assertEqual(mint["with"]["permission-contents"], "write")

    def test_planted_mint_step_without_the_permission_grant_is_reported(self) -> None:
        """Negative: a mint step that drops permission-contents is reported."""
        wf = self.planted()
        mint = next(
            step
            for step in steps_of(wf, PUBLISH_JOB)
            if str(step.get("uses", "")).startswith(MINT_ACTION)
        )
        self.assertTrue(mint["with"].pop("permission-contents"))
        self.assertIn(MINT_PERMISSION_FINDING, shape_findings(wf, self.inputs))

    def test_the_regeneration_job_references_no_secret(self) -> None:
        """Positive: no scalar of the whole regeneration job, job ``env:`` included, reads secrets."""
        self.assertFalse(references_secret(self.workflow["jobs"][REGENERATE_JOB]))

    def test_the_staged_locks_travel_through_a_named_artifact(self) -> None:
        """Positive: the regeneration job stages and uploads; the publish job downloads."""
        stage = next(
            step for step in self.regenerate_steps() if STAGE_COMMAND in step.get("run", "")
        )
        self.assertIn("--artifact-dir", stage["run"])
        uploads = [
            step
            for step in self.regenerate_steps()
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        downloads = [
            step
            for step in self.publish_steps()
            if str(step.get("uses", "")).startswith("actions/download-artifact@")
        ]
        self.assertEqual(len(uploads), 2)
        self.assertEqual(len(downloads), 2)
        self.assertEqual(uploads[0]["with"]["name"], downloads[0]["with"]["name"])

    def test_planted_missing_path_is_reported(self) -> None:
        """Negative: dropping one manifest input from the filter is reported."""
        wf = self.planted()
        wf["on"]["push"]["paths"].pop()
        self.assertIn("path filter is not the manifest input set", shape_findings(wf, self.inputs))

    def test_planted_pattern_or_duplicate_path_is_reported(self) -> None:
        """Negative: a glob in place of an input, and a duplicated input, are reported."""
        wf = self.planted()
        wf["on"]["push"]["paths"][0] = "requirements/locks/*.in"
        self.assertIn("path filter is not the manifest input set", shape_findings(wf, self.inputs))
        wf = self.planted()
        wf["on"]["push"]["paths"].append(wf["on"]["push"]["paths"][0])
        self.assertIn("path filter is not the manifest input set", shape_findings(wf, self.inputs))

    def test_planted_run_defects_are_reported(self) -> None:
        """Negative: an expression, a force flag and a second generator in ``run:``."""
        cases = {
            "expression inside run:": 'echo "${{ github.ref }}"',
            "force push": "git push --force origin HEAD",
            "second generator": "uv pip compile requirements/locks/build.in",
        }
        for finding, line in cases.items():
            with self.subTest(finding=finding):
                wf = self.planted()
                steps_of(wf, PUBLISH_JOB)[-1]["run"] += f"\n{line}\n"
                self.assertIn(finding, shape_findings(wf, self.inputs))

    def test_planted_unpinned_action_and_missing_guard_are_reported(self) -> None:
        """Negative: a tag-pinned action and a removed guard step are reported."""
        wf = self.planted()
        steps_of(wf, REGENERATE_JOB)[0]["uses"] = "actions/checkout@v7"
        self.assertIn("unpinned action actions/checkout@v7", shape_findings(wf, self.inputs))
        wf = self.planted()
        wf["jobs"][REGENERATE_JOB]["steps"] = [
            step for step in steps_of(wf, REGENERATE_JOB) if "refuse" not in step.get("run", "")
        ]
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_bootstrap_is_reported(self) -> None:
        """Negative: dropping the bootstrap step leaves the old generator installed."""
        wf = self.planted()
        wf["jobs"][REGENERATE_JOB]["steps"] = [
            step
            for step in steps_of(wf, REGENERATE_JOB)
            if "bootstrap-uv" not in step.get("run", "")
        ]
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_unreachable_bootstrap_install_is_reported(self) -> None:
        wf = self.planted()
        step = next(
            step for step in steps_of(wf, REGENERATE_JOB) if "bootstrap-uv" in step.get("run", "")
        )
        step["run"] = step["run"].replace('if [ -s "$bootstrap" ]', "if false")
        self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_unreachable_write_is_reported(self) -> None:
        wf = self.planted()
        write = "python3 scripts/ci/check_python_dependency_locks.py write"
        step = next(step for step in steps_of(wf, REGENERATE_JOB) if write in step.get("run", ""))
        step["run"] = step["run"].replace(write, f"if false; then\n  {write}\nfi")
        self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_optional_refresh_steps_are_reported(self) -> None:
        for command in ("bootstrap-uv", "check_python_dependency_locks.py write"):
            for key, value in (("if", False), ("continue-on-error", True)):
                with self.subTest(command=command, key=key):
                    wf = self.planted()
                    step = next(
                        step
                        for step in steps_of(wf, REGENERATE_JOB)
                        if command in step.get("run", "")
                    )
                    step[key] = value
                    self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_non_bash_or_missing_refresh_shell_is_reported(self) -> None:
        """Negative: a non-bash or omitted shell on bootstrap or write is reported."""
        for command in ("bootstrap-uv", "check_python_dependency_locks.py write"):
            for step_shell in ("python", None):
                with self.subTest(command=command, step_shell=step_shell):
                    wf = self.planted()
                    step = next(
                        step
                        for step in steps_of(wf, REGENERATE_JOB)
                        if command in step.get("run", "")
                    )
                    if step_shell is None:
                        step.pop("shell", None)
                    else:
                        step["shell"] = step_shell
                    self.assertIn(SHELL_FINDING, shape_findings(wf, self.inputs))

    def test_planted_late_tooling_install_is_reported(self) -> None:
        """Negative: installing the tooling lock after bootstrap can restore the old uv."""
        wf = self.planted()
        steps = steps_of(wf, REGENERATE_JOB)
        tooling = next(
            s for s in steps if s.get("name") == "Install the pinned uv from the tooling lock"
        )
        steps.remove(tooling)
        bootstrap = next(i for i, s in enumerate(steps) if "bootstrap-uv" in s.get("run", ""))
        steps.insert(bootstrap + 1, tooling)
        wf["jobs"][REGENERATE_JOB]["steps"] = steps
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def detach_tooling_tests_install(self, wf: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Remove only the continued tooling-tests install from the shared step."""
        step = next(
            s for s in steps_of(wf, REGENERATE_JOB) if "package-build.txt" in s.get("run", "")
        )
        lines = step["run"].splitlines(keepends=True)
        target = next(
            i
            for i, line in enumerate(lines)
            if line.strip() == "-r requirements/locks/tooling-tests.txt"
        )
        self.assertGreater(target, 0)
        self.assertEqual(
            lines[target - 1].strip(),
            "python -m pip install --no-build-isolation --require-hashes \\",
        )
        install = "".join(lines[target - 1 : target + 1])
        del lines[target - 1 : target + 1]
        step["run"] = "".join(lines)
        return step, install

    def test_planted_late_tooling_tests_install_is_reported(self) -> None:
        """Negative: moving only the tooling-tests install after bootstrap restores old uv."""
        wf = self.planted()
        _, install = self.detach_tooling_tests_install(wf)
        steps = steps_of(wf, REGENERATE_JOB)
        bootstrap = next(i for i, s in enumerate(steps) if "bootstrap-uv" in s.get("run", ""))
        steps.insert(bootstrap + 1, {"run": install})
        wf["jobs"][REGENERATE_JOB]["steps"] = steps
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_tooling_tests_install_is_reported(self) -> None:
        """Negative: the package-build install cannot stand in for the tooling-tests install."""
        wf = self.planted()
        self.detach_tooling_tests_install(wf)
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_swapped_installs_in_step_are_reported(self) -> None:
        """Boundary: reversing the two installs within one step changes the command order."""
        wf = self.planted()
        step, install = self.detach_tooling_tests_install(wf)
        lines = step["run"].splitlines(keepends=True)
        package = next(
            i for i, line in enumerate(lines) if "requirements/locks/package-build.txt" in line
        )
        lines.insert(package, install)
        step["run"] = "".join(lines)
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_bootstrap_install_is_reported(self) -> None:
        """Negative: compiling the bootstrap lock alone leaves the old uv installed."""
        wf = self.planted()
        step = next(
            step for step in steps_of(wf, REGENERATE_JOB) if "bootstrap-uv" in step.get("run", "")
        )
        step["run"] = "\n".join(
            line for line in step["run"].splitlines() if '-r "$bootstrap"' not in line
        )
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_stage_or_restore_is_reported(self) -> None:
        """Negative: dropping the staging or the restore step breaks the refresh chain."""
        for job, command in ((REGENERATE_JOB, STAGE_COMMAND), (PUBLISH_JOB, RESTORE_COMMAND)):
            with self.subTest(job=job, command=command):
                wf = self.planted()
                wf["jobs"][job]["steps"] = [
                    step for step in steps_of(wf, job) if command not in step.get("run", "")
                ]
                self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_needs_is_reported(self) -> None:
        """Negative: a publish job that does not need the regeneration job is reported."""
        wf = self.planted()
        wf["jobs"][PUBLISH_JOB]["needs"] = ["something-else"]
        self.assertIn(NEEDS_FINDING, shape_findings(wf, self.inputs))

    def test_planted_token_step_in_the_regeneration_job_is_reported(self) -> None:
        """Negative: minting the token beside the freshly compiled tool is reported."""
        wf = self.planted()
        mint = next(
            step
            for step in steps_of(wf, PUBLISH_JOB)
            if str(step.get("uses", "")).startswith(MINT_ACTION)
        )
        wf["jobs"][PUBLISH_JOB]["steps"] = [
            step
            for step in steps_of(wf, PUBLISH_JOB)
            if not str(step.get("uses", "")).startswith(MINT_ACTION)
        ]
        wf["jobs"][REGENERATE_JOB]["steps"].append(copy.deepcopy(mint))
        self.assertIn(MINT_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_token_step_is_reported(self) -> None:
        """Negative: a publish job without the mint step is reported."""
        wf = self.planted()
        wf["jobs"][PUBLISH_JOB]["steps"] = [
            step
            for step in steps_of(wf, PUBLISH_JOB)
            if not str(step.get("uses", "")).startswith(MINT_ACTION)
        ]
        self.assertIn(MINT_FINDING, shape_findings(wf, self.inputs))

    def test_planted_secret_in_the_regeneration_job_is_reported(self) -> None:
        """Negative: a secret in an ``env:`` or a ``run:`` of the regeneration job."""
        wf = self.planted()
        steps_of(wf, REGENERATE_JOB)[0]["env"] = {"TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
        self.assertIn(SEALED_FINDING, shape_findings(wf, self.inputs))
        wf = self.planted()
        step = next(
            step for step in steps_of(wf, REGENERATE_JOB) if STAGE_COMMAND in step.get("run", "")
        )
        step["run"] += '\necho "${{ secrets.RELEASE_BOT_APP_ID }}"\n'
        self.assertIn(SEALED_FINDING, shape_findings(wf, self.inputs))

    def test_planted_secret_in_the_regeneration_job_env_is_reported(self) -> None:
        """Negative: a job-level ``env:`` of the regeneration job cannot reference a secret."""
        wf = self.planted()
        wf["jobs"][REGENERATE_JOB]["env"] = {"KEY": "${{ secrets.RELEASE_BOT_PRIVATE_KEY }}"}
        self.assertIn(SEALED_FINDING, shape_findings(wf, self.inputs))

    def test_planted_secret_in_the_workflow_env_is_reported(self) -> None:
        """Negative: a top-level ``env:`` cannot smuggle a secret into the regeneration job."""
        wf = self.planted()
        wf["env"] = {"TOKEN": "${{ secrets.RELEASE_BOT_PRIVATE_KEY }}"}
        self.assertIn(SEALED_FINDING, shape_findings(wf, self.inputs))

    def test_planted_restore_outside_the_manifest_allow_list_is_reported(self) -> None:
        """Negative: an allow-list the checker does not derive is reported."""
        wf = self.planted()
        step = next(
            step for step in steps_of(wf, PUBLISH_JOB) if RESTORE_COMMAND in step.get("run", "")
        )
        step["run"] = step["run"].replace('-- "${outputs[@]}"', "-- requirements/locks/build.txt")
        self.assertIn(ALLOW_LIST_FINDING, shape_findings(wf, self.inputs))

    def test_boundary_both_lock_workflows_publish_through_the_shared_script(self) -> None:
        """Boundary: neither workflow runs git commit or git push itself."""
        for path in (PYTHON_WORKFLOW, NEO_WORKFLOW):
            with self.subTest(workflow=path.name):
                text = "\n".join(run_texts(load(path)))
                self.assertIn("lock_refresh_publish.py publish", text)
                self.assertIn("lock_refresh_publish.py refuse-non-renovate", text)
                self.assertNotRegex(text, r"git\s+(?:-c\s+\S+\s+)*(?:commit|push)\b")


if __name__ == "__main__":
    unittest.main()
