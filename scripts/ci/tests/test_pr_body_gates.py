#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/ci/pr-body-gates.sh and rule-enforcement.yml body-gate parity."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # no PyYAML stubs in the hook environment

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "rule-enforcement.yml"
DEFAULT_WRAPPER = ROOT / "scripts" / "ci" / "pr-body-gates.sh"
DEFAULT_MAKEFILE = ROOT / "Makefile"
BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git") or "/usr/bin/git"
MAKE = shutil.which("make") or "/usr/bin/make"
SUBPROCESS_TIMEOUT_S = 60
RC_OK = 0
RC_FAIL = 1
RC_USAGE = 2
STUB_EXIT_THREE = 3
STUB_GATE_NAMES = (
    "deliverables-check.sh",
    "state-md-touch-check.sh",
    "ffmpeg-patches-surface-check.sh",
)
BODY_STEP_SCRIPT_RE = re.compile(r"\bbash\s+scripts/ci/([A-Za-z0-9_.-]+\.sh)\b")
GATES_ARRAY_RE = re.compile(r"(?m)^GATES=\(([^)]*)\)")
CLEAN_ENV = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def _isolated_env() -> dict[str, str]:
    env: dict[str, str] = {
        **CLEAN_ENV,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for key in ("PR_TITLE", "PR_BODY", "BASE_SHA", "HEAD_SHA", "PR_BODY_GATES_DIR"):
        env.pop(key, None)
    return env


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603
        [GIT, *args],
        cwd=cwd,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=check,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _head_sha() -> str:
    return _git(ROOT, "rev-parse", "HEAD").stdout.strip()


def _wrapper_path() -> Path:
    override = os.environ.get("PR_BODY_GATES_WRAPPER")
    return Path(override) if override else DEFAULT_WRAPPER


def _run_wrapper(
    *,
    title: str | None,
    body: str | None,
    base_sha: str | None = None,
    head_sha: str | None = None,
    gates_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _isolated_env()
    if title is not None:
        env["PR_TITLE"] = title
    if body is not None:
        env["PR_BODY"] = body
    if base_sha is not None:
        env["BASE_SHA"] = base_sha
    if head_sha is not None:
        env["HEAD_SHA"] = head_sha
    if gates_dir is not None:
        env["PR_BODY_GATES_DIR"] = str(gates_dir)
    return subprocess.run(  # noqa: S603  # nosec B603
        [BASH, str(_wrapper_path())],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _step_body_gates(step: object, job_env: dict[str, Any]) -> list[str]:
    if not isinstance(step, dict):
        return []
    raw_env = step.get("env")
    step_env = raw_env if isinstance(raw_env, dict) else {}
    merged_env = {**job_env, **step_env}
    run_cmd = step.get("run")
    if "PR_BODY" in merged_env and isinstance(run_cmd, str):
        return BODY_STEP_SCRIPT_RE.findall(run_cmd)
    return []


def collect_workflow_body_gates(workflow_doc: dict[str, Any]) -> set[str]:
    """Collect scripts/ci/<name>.sh steps whose env defines PR_BODY."""
    found: set[str] = set()
    jobs = workflow_doc.get("jobs")
    if not isinstance(jobs, dict):
        return found
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        raw_job_env = job.get("env")
        job_env = raw_job_env if isinstance(raw_job_env, dict) else {}
        steps = job.get("steps")
        if isinstance(steps, list):
            for step in steps:
                found.update(_step_body_gates(step, job_env))
    return found


def parse_wrapper_gates(wrapper_text: str) -> list[str]:
    """Read the fixed GATES=(...) array from scripts/ci/pr-body-gates.sh."""
    match = GATES_ARRAY_RE.search(wrapper_text)
    assert match is not None, "GATES=(...) array missing in pr-body-gates.sh"
    return shlex.split(match.group(1), comments=True)


def _write_stubs(directory: Path, codes: dict[str, int]) -> None:
    for name, code in codes.items():
        script = directory / name
        script.write_text(
            f"#!/usr/bin/env bash\necho {name}\nexit {code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)


def test_t1_workflow_parity() -> None:
    """Every rule-enforcement.yml PR_BODY shell gate has a local mirror in GATES."""
    workflow_doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow_doc, dict)
    workflow_gates = collect_workflow_body_gates(workflow_doc)
    wrapper_gates = parse_wrapper_gates(DEFAULT_WRAPPER.read_text(encoding="utf-8"))
    assert len(wrapper_gates) == len(set(wrapper_gates))
    assert workflow_gates == set(wrapper_gates)

    synthetic_doc: dict[str, Any] = {
        "jobs": {
            "unmirrored": {
                "steps": [
                    {
                        "env": {"PR_BODY": "${{ github.event.pull_request.body }}"},
                        "run": "bash scripts/ci/unmirrored-body-gate.sh",
                    }
                ]
            }
        }
    }
    assert collect_workflow_body_gates(synthetic_doc) | workflow_gates != set(wrapper_gates)


def test_t2_planted_bug_shaped_pr_without_state_delta_fails() -> None:
    """Real wrapper runs state-md-touch-check.sh and reports rc 1 on fix: without state delta."""
    sha = _head_sha()
    res = _run_wrapper(
        title="fix: planted",
        body="planted body",
        base_sha=sha,
        head_sha=sha,
    )
    assert res.returncode != RC_OK
    assert "pr-body-gates: state-md-touch-check.sh rc 1" in res.stdout.splitlines()


def test_t3_negative_control_state_md_passes() -> None:
    """Real wrapper reports state-md-touch-check.sh rc 0 when title is ci: with opt-out."""
    sha = _head_sha()
    res = _run_wrapper(
        title="ci: planted",
        body="no state delta: planted",
        base_sha=sha,
        head_sha=sha,
    )
    assert "pr-body-gates: state-md-touch-check.sh rc 0" in res.stdout.splitlines()


def test_t4_no_early_stop_runs_all_gates(tmp_path: Path) -> None:
    """First stub gate exiting 1 does not stop subsequent gates; wrapper exits 1."""
    _write_stubs(
        tmp_path,
        {
            "deliverables-check.sh": RC_FAIL,
            "state-md-touch-check.sh": RC_OK,
            "ffmpeg-patches-surface-check.sh": RC_OK,
        },
    )
    res = _run_wrapper(title="ci: stub", body="stub", gates_dir=tmp_path)
    lines = res.stdout.splitlines()
    assert res.returncode == RC_FAIL
    for echoed in (
        "deliverables-check.sh",
        "state-md-touch-check.sh",
        "ffmpeg-patches-surface-check.sh",
        "pr-body-gates: deliverables-check.sh rc 1",
        "pr-body-gates: state-md-touch-check.sh rc 0",
        "pr-body-gates: ffmpeg-patches-surface-check.sh rc 0",
    ):
        assert echoed in lines


def test_t5_boundary_exit_codes_and_missing_title(tmp_path: Path) -> None:
    """Stub gate exiting >2 maps to wrapper rc 2; unset PR_TITLE exits 2; empty PR_BODY works."""
    _write_stubs(
        tmp_path,
        {
            "deliverables-check.sh": RC_FAIL,
            "state-md-touch-check.sh": STUB_EXIT_THREE,
            "ffmpeg-patches-surface-check.sh": RC_OK,
        },
    )
    high_rc = _run_wrapper(title="ci: stub", body="stub", gates_dir=tmp_path)
    assert high_rc.returncode == RC_USAGE
    assert "pr-body-gates: state-md-touch-check.sh rc 3" in high_rc.stdout.splitlines()

    missing_title = _run_wrapper(title=None, body="stub", gates_dir=tmp_path)
    assert missing_title.returncode == RC_USAGE
    assert "Usage:" in missing_title.stderr

    missing_body = _run_wrapper(title="ci: stub", body=None, gates_dir=tmp_path)
    assert missing_body.returncode == RC_USAGE

    _write_stubs(
        tmp_path,
        {
            "deliverables-check.sh": RC_OK,
            "state-md-touch-check.sh": RC_OK,
            "ffmpeg-patches-surface-check.sh": RC_OK,
        },
    )
    empty_body = _run_wrapper(title="ci: stub", body="", gates_dir=tmp_path)
    assert empty_body.returncode == RC_OK


def _makefile_path() -> Path:
    override = os.environ.get("PR_CHECK_MAKEFILE")
    return Path(override).resolve() if override else DEFAULT_MAKEFILE


def _head_subject() -> str:
    return _git(ROOT, "log", "-1", "--format=%s", "HEAD").stdout.rstrip("\r\n")


def _init_fixture_repo(repo_dir: Path) -> tuple[str, str]:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q", "-b", "main")
    wrapper_dst = repo_dir / "scripts" / "ci" / "pr-body-gates.sh"
    wrapper_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEFAULT_WRAPPER, wrapper_dst)
    _git(repo_dir, "add", "-A")
    commit_ident = (
        "-c",
        "user.name=Ada",
        "-c",
        "user.email=ada@example.org",
        "-c",
        "commit.gpgsign=false",
    )
    _git(repo_dir, *commit_ident, "commit", "-q", "-m", "base")
    base_sha = _git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    (repo_dir / "change.txt").write_text("delta\n", encoding="utf-8")
    _git(repo_dir, "add", "-A")
    _git(repo_dir, *commit_ident, "commit", "-q", "-m", "head")
    head_sha = _git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    return base_sha, head_sha


def _write_echo_stubs(directory: Path, marker: Path | None = None) -> None:
    directory.mkdir(exist_ok=True)
    marker_cmd = f"printf 'ran\\n' >> {shlex.quote(str(marker))}\n" if marker else ""
    script_body = (
        "#!/usr/bin/env bash\n"
        f"{marker_cmd}"
        'printf \'%s\\n\' "title=${PR_TITLE}" "body=${PR_BODY}" '
        '"base=${BASE_SHA-<unset>}" "head=${HEAD_SHA-<unset>}"\n'
        "exit 0\n"
    )
    for name in STUB_GATE_NAMES:
        script = directory / name
        script.write_text(script_body, encoding="utf-8")
        script.chmod(0o700)


def _write_fake_gh(
    bin_dir: Path,
    payload: dict[str, object],
    marker: Path | None = None,
) -> None:
    bin_dir.mkdir(exist_ok=True)
    marker_cmd = f"printf 'called\\n' > {shlex.quote(str(marker))}\n" if marker else ""
    encoded = shlex.quote(json.dumps(payload))
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        f"#!/usr/bin/env bash\n{marker_cmd}printf '%s\\n' {encoded}\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)


def _run_pr_check(
    *,
    gates_dir: Path,
    pr: str | None = None,
    body: str | None = None,
    title: str | None = None,
    extra_path: Path | None = None,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    env = _isolated_env()
    for key in ("PR", "BODY", "TITLE", "MAKEFLAGS", "MFLAGS"):
        env.pop(key, None)
    env["PR_BODY_GATES_DIR"] = str(gates_dir)
    if extra_path is not None:
        cur = env.get("PATH", "")
        env["PATH"] = f"{extra_path}{os.pathsep}{cur}" if cur else str(extra_path)
    cmd = [MAKE, "-s", "-C", str(cwd), "-f", str(_makefile_path()), "pr-check"]
    if pr is not None:
        cmd.append(f"PR={pr}")
    if body is not None:
        cmd.append(f"BODY={body}")
    if title is not None:
        cmd.append(f"TITLE={title}")
    return subprocess.run(  # noqa: S603  # nosec B603
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _parse_stub_lines(stdout: str) -> tuple[list[str], list[str]]:
    lines = stdout.split("\n")
    titles = [line.removeprefix("title=") for line in lines if line.startswith("title=")]
    bodies = [line.removeprefix("body=") for line in lines if line.startswith("body=")]
    return titles, bodies


def _parse_stub_revisions(stdout: str) -> tuple[list[str], list[str]]:
    lines = stdout.split("\n")
    bases = [line.removeprefix("base=") for line in lines if line.startswith("base=")]
    heads = [line.removeprefix("head=") for line in lines if line.startswith("head=")]
    return bases, heads


def test_t6_planted_metacharacters_in_title_and_body_path(tmp_path: Path) -> None:
    """TITLE metacharacters and BODY path with space/$ reach gates literally."""
    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    _write_echo_stubs(stubs_dir)

    body_dir = tmp_path / "dir with $dollar"
    body_dir.mkdir()
    body_file = body_dir / "pr-body.md"
    body_text = "body payload with `echo no` $(echo no) $HOME \"dq\" 'sq'"
    body_file.write_text(body_text, encoding="utf-8")

    planted_title = "fix(ci): keep `echo injected` $(echo injected) $HOME \"dq\" 'sq'"
    res = _run_pr_check(gates_dir=stubs_dir, body=str(body_file), title=planted_title)
    assert res.returncode == RC_OK
    titles, bodies = _parse_stub_lines(res.stdout)
    assert len(titles) == len(STUB_GATE_NAMES)
    for got_title in titles:
        assert got_title.encode("utf-8") == planted_title.encode("utf-8")
    assert bodies == [body_file.read_text(encoding="utf-8")] * len(STUB_GATE_NAMES)


def test_t7_negative_control_unset_and_empty_title_use_head(tmp_path: Path) -> None:
    """Unset or empty TITLE falls back to git log -1 --format=%s HEAD."""
    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    _write_echo_stubs(stubs_dir)

    body_file = tmp_path / "pr-body.md"
    body_file.write_text("clean body", encoding="utf-8")
    expected_subject = _head_subject()

    for candidate in (None, ""):
        res = _run_pr_check(gates_dir=stubs_dir, body=str(body_file), title=candidate)
        assert res.returncode == RC_OK
        titles, bodies = _parse_stub_lines(res.stdout)
        assert titles == [expected_subject] * len(STUB_GATE_NAMES)
        assert bodies == ["clean body"] * len(STUB_GATE_NAMES)


def test_t8_boundary_unreadable_body_bad_pr_and_no_args(tmp_path: Path) -> None:
    """Unreadable BODY, invalid PR (no gh call), missing args, and failed gate exit 2."""
    stubs_dir = tmp_path / "stubs"
    _write_echo_stubs(stubs_dir)

    fake_bin = tmp_path / "bin"
    marker = tmp_path / "gh-called.marker"
    sha = _head_sha()
    _write_fake_gh(
        fake_bin,
        {"title": "ci: stub", "body": "stub body", "baseRefOid": sha, "headRefOid": sha},
        marker=marker,
    )

    unreadable = _run_pr_check(gates_dir=stubs_dir, body=str(tmp_path / "missing.md"))
    assert unreadable.returncode == RC_USAGE

    for bad_pr in ("12abc", "1; true", "0"):
        bad = _run_pr_check(gates_dir=stubs_dir, pr=bad_pr, extra_path=fake_bin)
        assert bad.returncode == RC_USAGE
        assert "Usage:" in bad.stderr
        assert not marker.exists()

    neither = _run_pr_check(gates_dir=stubs_dir, extra_path=fake_bin)
    assert neither.returncode == RC_USAGE
    assert "Usage:" in neither.stderr
    assert not marker.exists()

    valid_pr = _run_pr_check(gates_dir=stubs_dir, pr="260", extra_path=fake_bin)
    assert valid_pr.returncode == RC_OK
    assert marker.exists()

    _write_stubs(stubs_dir, dict.fromkeys(STUB_GATE_NAMES, RC_FAIL))
    failed_gate = _run_pr_check(gates_dir=stubs_dir, pr="260", extra_path=fake_bin)
    assert failed_gate.returncode == RC_USAGE
    assert "pr-body-gates: summary pass=0 fail=3 error=0 rc=1" in failed_gate.stdout.splitlines()


def test_t9_pr_mode_passes_pr_revisions_to_gates(tmp_path: Path) -> None:
    """PR mode passes gh baseRefOid and headRefOid to every gate as BASE_SHA and HEAD_SHA."""
    repo = tmp_path / "repo"
    base_sha, head_sha = _init_fixture_repo(repo)
    assert base_sha != head_sha

    stubs_dir = tmp_path / "stubs"
    _write_echo_stubs(stubs_dir)
    fake_bin = tmp_path / "bin"
    _write_fake_gh(
        fake_bin,
        {
            "title": "fix: pr seven",
            "body": "body seven",
            "baseRefOid": base_sha,
            "headRefOid": head_sha,
        },
    )

    res = _run_pr_check(gates_dir=stubs_dir, pr="7", extra_path=fake_bin, cwd=repo)
    assert res.returncode == RC_OK
    titles, bodies = _parse_stub_lines(res.stdout)
    bases, heads = _parse_stub_revisions(res.stdout)
    assert titles == ["fix: pr seven"] * len(STUB_GATE_NAMES)
    assert bodies == ["body seven"] * len(STUB_GATE_NAMES)
    assert bases == [base_sha] * len(STUB_GATE_NAMES)
    assert heads == [head_sha] * len(STUB_GATE_NAMES)


def test_t10_pr_mode_fetches_missing_revision(tmp_path: Path) -> None:
    """PR mode fetches a missing revision from origin before running the gates."""
    upstream = tmp_path / "upstream"
    base_sha, head_sha = _init_fixture_repo(upstream)
    bare_origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(bare_origin))
    _git(
        upstream,
        "push",
        "-q",
        str(bare_origin),
        f"{base_sha}:refs/heads/main",
        f"{head_sha}:refs/heads/pr-7",
    )

    local_repo = tmp_path / "local"
    local_repo.mkdir()
    _git(local_repo, "init", "-q", "-b", "main")
    _git(local_repo, "remote", "add", "origin", str(bare_origin))
    _git(local_repo, "fetch", "--no-tags", "-q", "origin", "refs/heads/main")
    _git(local_repo, "checkout", "-q", "-B", "main", "FETCH_HEAD")
    assert _git(local_repo, "cat-file", "-e", f"{head_sha}^{{commit}}", check=False).returncode != 0

    stubs_dir = tmp_path / "stubs"
    _write_echo_stubs(stubs_dir)
    fake_bin = tmp_path / "bin"
    _write_fake_gh(
        fake_bin,
        {
            "title": "fix: fetched head",
            "body": "fetched body",
            "baseRefOid": base_sha,
            "headRefOid": head_sha,
        },
    )

    res = _run_pr_check(gates_dir=stubs_dir, pr="7", extra_path=fake_bin, cwd=local_repo)
    assert res.returncode == RC_OK
    assert _git(local_repo, "cat-file", "-e", f"{head_sha}^{{commit}}").returncode == RC_OK
    bases, heads = _parse_stub_revisions(res.stdout)
    assert bases == [base_sha] * len(STUB_GATE_NAMES)
    assert heads == [head_sha] * len(STUB_GATE_NAMES)


def test_t11_pr_mode_unobtainable_revision_fails(tmp_path: Path) -> None:
    """Missing or non-hex PR revisions exit 2 with pr-check diagnostic and run no gate."""
    repo = tmp_path / "repo"
    base_sha, head_sha = _init_fixture_repo(repo)
    bare_origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(bare_origin))
    _git(repo, "remote", "add", "origin", str(bare_origin))

    gate_marker = tmp_path / "gate-ran.marker"
    stubs_dir = tmp_path / "stubs"
    _write_echo_stubs(stubs_dir, marker=gate_marker)
    fake_bin = tmp_path / "bin"

    for kind, bad_base, bad_head, missing_sha in (
        ("base", "a" * 40, head_sha, "a" * 40),
        ("head", base_sha, "b" * 40, "b" * 40),
        ("head", base_sha, "c" * 64, "c" * 64),
    ):
        _write_fake_gh(
            fake_bin,
            {"title": "fix: t11", "body": "b", "baseRefOid": bad_base, "headRefOid": bad_head},
        )
        res = _run_pr_check(gates_dir=stubs_dir, pr="7", extra_path=fake_bin, cwd=repo)
        assert res.returncode == RC_USAGE
        assert f"pr-check: cannot obtain {kind} revision {missing_sha} of PR 7" in res.stderr
        assert not gate_marker.exists()

    for bad_base, bad_head in (
        ("not-a-hex-sha", head_sha),
        (base_sha, "A" * 40),
        (base_sha, "a" * 39),
        ("", head_sha),
    ):
        _write_fake_gh(
            fake_bin,
            {"title": "fix: t11", "body": "b", "baseRefOid": bad_base, "headRefOid": bad_head},
        )
        res = _run_pr_check(gates_dir=stubs_dir, pr="7", extra_path=fake_bin, cwd=repo)
        assert res.returncode == RC_USAGE
        err_lines = [ln for ln in res.stderr.splitlines() if not ln.startswith("make:")]
        assert len(err_lines) == 1 and err_lines[0].startswith("pr-check:")
        assert not gate_marker.exists()
