#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Shape of .github/workflows/python-lock-refresh.yml (ADR-3560).

Positive: the path filter equals the manifest inputs read through the lock checker's loader;
the workflow is Renovate-only, read-only by default, pinned by SHA, capped, serialised per ref,
pushes through the shared script and calls the lock checker's own write mode.
Negative: planted copies lose a path, gain a pattern, carry an expression in ``run:``, use an
unpinned action, push with a force flag, write a second generator or lose the guard step; each
is reported.
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

ORDER_FINDING = "guard, tooling install, bootstrap, bootstrap install, write and publish are missing or out of order"
REQUIRED_STEP_FINDING = "bootstrap and write must run unconditionally and propagate failures"
SHELL_FINDING = "bootstrap and write must set shell: bash"


def load(path: Path) -> dict[str, Any]:
    """Parse a workflow; YAML 1.1 reads the ``on`` key as ``True``, so fold it back."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    if True in data:
        data["on"] = data.pop(True)
    return data


def steps_of(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps of the workflow's only job."""
    (job,) = workflow["jobs"].values()
    return list(job["steps"])


def run_texts(workflow: dict[str, Any]) -> list[str]:
    """Every ``run:`` script of the workflow."""
    return [step["run"] for step in steps_of(workflow) if "run" in step]


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
    (job,) = workflow["jobs"].values()
    if not isinstance(job.get("timeout-minutes"), int):
        found.append("job needs a literal timeout-minutes")
    found += [
        f"unpinned action {step['uses']}"
        for step in steps_of(workflow)
        if "uses" in step and not SHA_RE.match(step["uses"])
    ]
    texts = run_texts(workflow)
    found += ["expression inside run:" for text in texts if "${{" in text]
    found += ["force push" for text in texts if FORCE_RE.search(text)]
    found += ["second generator" for text in texts if GENERATOR_RE.search(text)]
    found += guard_order_findings(steps_of(workflow))
    return found


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


def guard_order_findings(steps: list[dict[str, Any]]) -> list[str]:
    """Check command order and keep the bootstrap install in its producer's shell."""
    bootstrap = "python3 scripts/ci/check_python_dependency_locks.py bootstrap-uv"
    install = 'python -m pip install --no-build-isolation --require-hashes -r "$bootstrap"'
    write = "python3 scripts/ci/check_python_dependency_locks.py write"
    markers = (
        "python3 scripts/ci/lock_refresh_publish.py refuse-non-renovate",
        "python -m pip install --require-hashes -r requirements/locks/package-build.txt",
        "python -m pip install --no-build-isolation --require-hashes -r requirements/locks/tooling-tests.txt",
        bootstrap,
        install,
        write,
        "python3 scripts/ci/lock_refresh_publish.py publish",
    )
    positions: dict[str, tuple[int, int]] = {}
    for index, step in enumerate(steps):
        for line, command in lock_checker.logical_lines(step.get("run", "")):
            marker = (
                command.partition(" --")[0]
                if command.startswith("python3 scripts/ci/")
                else command
            )
            if marker in markers:
                if marker in positions:
                    return [ORDER_FINDING]
                positions[marker] = (index, line)
    if (
        sorted(positions, key=positions.__getitem__) != list(markers)
        or positions[bootstrap][0] != positions[install][0]
    ):
        return [ORDER_FINDING]
    for marker in (bootstrap, write):
        index, line = positions[marker]
        if steps[index].get("shell") != "bash":
            return [SHELL_FINDING]
        if not unconditional_refresh_command(steps[index], line, marker, install):
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

    def test_checked_in_workflow_has_the_required_shape(self) -> None:
        """Positive: no finding, and the filter is the manifest input set."""
        self.assertEqual(shape_findings(self.workflow, self.inputs), [])
        paths = self.workflow["on"]["push"]["paths"]
        self.assertEqual(sorted(paths), sorted(self.inputs))
        self.assertGreater(len(paths), 20)

    def test_publish_step_reads_outputs_from_the_manifest_and_names_the_bot_commit(self) -> None:
        """Positive: outputs come from the checker, the subject is the DCO subject."""
        text = "\n".join(run_texts(self.workflow))
        self.assertIn("check_python_dependency_locks.py outputs", text)
        self.assertIn("--check-strays-when-unchanged", text)
        self.assertIn('-- "${outputs[@]}"', text)
        self.assertNotIn("requirements/locks/*.txt", text)
        dco = (ROOT / "scripts" / "ci" / "check-dco.py").read_text(encoding="utf-8")
        subject = re.search(r'PYTHON_LOCK_SUBJECT = "([^"]+)"', dco)
        assert subject is not None
        self.assertIn(f'--subject "{subject.group(1)}"', text)

    def test_checkout_keeps_no_credentials_and_the_token_comes_last(self) -> None:
        """Positive: checkout drops credentials; the token is minted after the regeneration."""
        steps = steps_of(self.workflow)
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
        self.assertIs(checkout["with"]["persist-credentials"], False)
        names = [s.get("name", "") for s in steps]
        mint = next(i for i, n in enumerate(names) if n.startswith("Mint"))
        write = next(i for i, s in enumerate(steps) if "write" in s.get("run", ""))
        self.assertLess(write, mint)

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
                steps_of(wf)[-1]["run"] += f"\n{line}\n"
                self.assertIn(finding, shape_findings(wf, self.inputs))

    def test_planted_unpinned_action_and_missing_guard_are_reported(self) -> None:
        """Negative: a tag-pinned action and a removed guard step are reported."""
        wf = self.planted()
        steps_of(wf)[0]["uses"] = "actions/checkout@v7"
        self.assertIn("unpinned action actions/checkout@v7", shape_findings(wf, self.inputs))
        wf = self.planted()
        steps = steps_of(wf)
        wf["jobs"]["refresh-locks"]["steps"] = [
            s for s in steps if "refuse" not in s.get("run", "")
        ]
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_missing_bootstrap_is_reported(self) -> None:
        """Negative: dropping the bootstrap step leaves the old generator installed."""
        wf = self.planted()
        wf["jobs"]["refresh-locks"]["steps"] = [
            step for step in steps_of(wf) if "bootstrap-uv" not in step.get("run", "")
        ]
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def test_planted_unreachable_bootstrap_install_is_reported(self) -> None:
        wf = self.planted()
        step = next(s for s in steps_of(wf) if "bootstrap-uv" in s.get("run", ""))
        step["run"] = step["run"].replace('if [ -s "$bootstrap" ]', "if false")
        self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_unreachable_write_is_reported(self) -> None:
        wf = self.planted()
        write = "python3 scripts/ci/check_python_dependency_locks.py write"
        step = next(step for step in steps_of(wf) if write in step.get("run", ""))
        step["run"] = step["run"].replace(write, f"if false; then\n  {write}\nfi")
        self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_optional_refresh_steps_are_reported(self) -> None:
        for command in ("bootstrap-uv", "check_python_dependency_locks.py write"):
            for key, value in (("if", False), ("continue-on-error", True)):
                with self.subTest(command=command, key=key):
                    wf = self.planted()
                    step = next(s for s in steps_of(wf) if command in s.get("run", ""))
                    step[key] = value
                    self.assertIn(REQUIRED_STEP_FINDING, shape_findings(wf, self.inputs))

    def test_planted_non_bash_or_missing_refresh_shell_is_reported(self) -> None:
        """Negative: a non-bash or omitted shell on bootstrap or write is reported."""
        for command in ("bootstrap-uv", "check_python_dependency_locks.py write"):
            for step_shell in ("python", None):
                with self.subTest(command=command, step_shell=step_shell):
                    wf = self.planted()
                    step = next(s for s in steps_of(wf) if command in s.get("run", ""))
                    if step_shell is None:
                        step.pop("shell", None)
                    else:
                        step["shell"] = step_shell
                    self.assertIn(SHELL_FINDING, shape_findings(wf, self.inputs))

    def test_planted_late_tooling_install_is_reported(self) -> None:
        """Negative: installing the tooling lock after bootstrap can restore the old uv."""
        wf = self.planted()
        steps = steps_of(wf)
        tooling = next(
            s for s in steps if s.get("name") == "Install the pinned uv from the tooling lock"
        )
        steps.remove(tooling)
        bootstrap = next(i for i, s in enumerate(steps) if "bootstrap-uv" in s.get("run", ""))
        steps.insert(bootstrap + 1, tooling)
        wf["jobs"]["refresh-locks"]["steps"] = steps
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

    def detach_tooling_tests_install(self, wf: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Remove only the continued tooling-tests install from the shared step."""
        step = next(s for s in steps_of(wf) if "package-build.txt" in s.get("run", ""))
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
        steps = steps_of(wf)
        bootstrap = next(i for i, s in enumerate(steps) if "bootstrap-uv" in s.get("run", ""))
        steps.insert(bootstrap + 1, {"run": install})
        wf["jobs"]["refresh-locks"]["steps"] = steps
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
        step = next(s for s in steps_of(wf) if "bootstrap-uv" in s.get("run", ""))
        step["run"] = "\n".join(
            line for line in step["run"].splitlines() if '-r "$bootstrap"' not in line
        )
        self.assertIn(ORDER_FINDING, shape_findings(wf, self.inputs))

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
