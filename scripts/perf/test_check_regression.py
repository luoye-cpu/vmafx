#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Smoke tests for scripts/perf/check-regression.py (ADR-0907)."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("check_regression", _HERE / "check-regression.py")
assert _SPEC is not None
check_regression = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(check_regression)

_WORKLOAD = {
    "width": 1920,
    "height": 1080,
    "bitdepth": 8,
    "frames": 48,
    "fixture_source": "netflix",
}


def _baseline_payload() -> dict[str, Any]:
    """Small fabricated baseline covering two backends and two metrics."""
    return {
        "schema_version": "1",
        "hardware": {"cpu_model": "Test CPU", "gpu_model": "Test GPU"},
        "runs": [
            {
                "resolution": "576",
                "backend": "cpu",
                "metric": "vif",
                "median_ms": 100,
                "status": "ok",
                **_WORKLOAD,
            },
            {
                "resolution": "576",
                "backend": "cpu",
                "metric": "adm",
                "median_ms": 200,
                "status": "ok",
                **_WORKLOAD,
            },
            {
                "resolution": "576",
                "backend": "cuda",
                "metric": "vif",
                "median_ms": 50,
                "status": "ok",
                **_WORKLOAD,
            },
            {
                "resolution": "576",
                "backend": "cuda",
                "metric": "adm",
                "median_ms": 0,
                "status": "skip",
                "skip_reason": "no GPU",
                **_WORKLOAD,
            },
        ],
    }


def _all_ok_payload() -> dict[str, Any]:
    """Baseline payload with only status ok cells (no skip lanes)."""
    payload = _baseline_payload()
    payload["runs"] = [cell for cell in payload["runs"] if cell.get("status") == "ok"]
    return payload


def _not_compared_for(
    not_compared: list[dict[str, Any]], key: tuple[str, str, str]
) -> dict[str, Any]:
    return next(entry for entry in not_compared if entry["key"] == key)


def test_identical_runs_pass() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    regressions, improvements, skipped = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert regressions == []
    assert improvements == []
    # One cuda/adm cell is skip in both -> joined-but-skipped.
    assert len(skipped) == 1


def test_regression_above_tolerance_flagged() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    # Bump cpu/vif by 10% (above the 5% tolerance).
    current["runs"][0]["median_ms"] = 110
    regressions, _, _ = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert len(regressions) == 1
    assert regressions[0]["key"] == ("576", "cpu", "vif")
    assert regressions[0]["delta_pct"] == pytest.approx(10.0)


def test_regression_within_tolerance_ignored() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    # Bump cpu/vif by 3% (below the 5% tolerance).
    current["runs"][0]["median_ms"] = 103
    regressions, _, _ = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert regressions == []


def test_improvement_reported_informationally() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    # Drop cpu/vif by 20% (well past the negative tolerance).
    current["runs"][0]["median_ms"] = 80
    regressions, improvements, _ = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert regressions == []
    assert len(improvements) == 1
    assert improvements[0]["key"] == ("576", "cpu", "vif")


def test_backend_filter_restricts_comparison() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    # Make cpu/vif regress and cuda/vif also regress. Filter to cpu only.
    current["runs"][0]["median_ms"] = 200
    current["runs"][2]["median_ms"] = 200
    regressions, _, _ = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=["cpu"]
    )
    # Only the cpu regression should be visible.
    assert len(regressions) == 1
    assert [record["key"] for record in regressions] == [("576", "cpu", "vif")]


def test_missing_current_cell_is_reported_missing() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    # Drop cpu/vif from the current run entirely.
    current["runs"] = [cell for cell in current["runs"] if cell["metric"] != "vif"]
    regressions, _, skipped = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert regressions == []
    skip_keys = {record["key"] for record in skipped}
    assert ("576", "cpu", "vif") in skip_keys
    assert ("576", "cuda", "vif") in skip_keys
    for key in (("576", "cpu", "vif"), ("576", "cuda", "vif")):
        assert _not_compared_for(skipped, key)["state"] == "missing"


def test_missing_baseline_cell_reported_missing() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"].append(
        {
            "resolution": "720",
            "backend": "cpu",
            "metric": "vif",
            "median_ms": 90,
            "status": "ok",
            **_WORKLOAD,
        }
    )
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("720", "cpu", "vif"))
    assert entry["state"] == "missing"
    assert entry["reason"] == "missing in baseline"


def test_current_status_error_is_failed() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["status"] = "error"
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert entry["state"] == "failed"


def test_current_skip_when_baseline_ok_is_failed() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["status"] = "skip"
    current["runs"][0]["skip_reason"] = "disabled"
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert entry["state"] == "failed"


def test_baseline_skip_state_is_skipped() -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cuda", "adm"))
    assert entry["state"] == "skipped"


def test_current_median_none_is_failed() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = None
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert entry["state"] == "failed"


def test_workload_width_mismatch_is_incomparable() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["width"] = 1280
    regressions, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert entry["state"] == "incomparable"
    assert regressions == []


def test_cpu_model_mismatch_incomparable_cuda_still_compared() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["hardware"] = {"cpu_model": "Other CPU", "gpu_model": "Test GPU"}
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    cpu_entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert cpu_entry["state"] == "incomparable"
    not_compared_keys = {entry["key"] for entry in not_compared}
    assert ("576", "cuda", "vif") not in not_compared_keys


def test_gpu_model_mismatch_incomparable_cpu_still_compared() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["hardware"] = {"cpu_model": "Test CPU", "gpu_model": "Other GPU"}
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cuda", "vif"))
    assert entry["state"] == "incomparable"
    assert ("576", "cpu", "vif") not in {e["key"] for e in not_compared}


def test_current_missing_hardware_is_incomparable() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current.pop("hardware", None)
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    assert all(entry["state"] == "incomparable" for entry in not_compared)
    assert len(not_compared) == len(baseline["runs"])


def test_baseline_median_zero_ok_cell_incomparable() -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    baseline["runs"][0]["median_ms"] = 0
    current["runs"][0]["median_ms"] = 50
    _, _, not_compared = check_regression.compare_runs(
        baseline, current, tolerance_pct=5.0, backends=None
    )
    entry = _not_compared_for(not_compared, ("576", "cpu", "vif"))
    assert entry["state"] == "incomparable"


def test_real_committed_baseline_loads_cleanly() -> None:
    """The committed baseline JSON must satisfy the gate's reader."""
    baseline_path = Path(__file__).resolve().parents[2] / "testdata" / "perf_multi_resolution.json"
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    indexed = check_regression._index_runs(payload)
    assert indexed, "committed baseline produced no indexable cells"


def _run_main(
    tmp_path: Path,
    baseline: dict[str, Any],
    current: dict[str, Any],
    *extra_args: str,
) -> int:
    """Serialize baseline and current payloads to tmp_path and invoke main()."""
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")
    return int(
        check_regression.main(
            ["--baseline", str(baseline_path), "--current", str(current_path), *extra_args]
        )
    )


def test_main_exits_nonzero_on_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = 200  # 100% regression
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "REGRESSIONS" in captured.out


def test_main_exits_zero_on_clean_run(tmp_path: Path) -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == 0


def test_main_exits_three_when_not_compared_no_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"] = [c for c in current["runs"] if c["metric"] != "adm"]
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == check_regression._EXIT_NOT_COMPARED
    captured = capsys.readouterr()
    assert "missing=" in captured.err


def test_main_advisory_still_exits_three_when_not_compared(tmp_path: Path) -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"] = [c for c in current["runs"] if c["metric"] != "adm"]
    exit_code = _run_main(tmp_path, baseline, current, "--advisory")
    assert exit_code == check_regression._EXIT_NOT_COMPARED


def test_main_advisory_exits_zero_regressions_all_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = 500
    exit_code = _run_main(tmp_path, baseline, current, "--advisory")
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ADVISORY" in captured.out


def test_main_exits_one_regression_and_not_compared(tmp_path: Path) -> None:
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = 200
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == 1


def test_main_exits_two_when_runs_not_list(tmp_path: Path) -> None:
    baseline = {"schema_version": "1", "runs": "not-a-list"}
    current = {"schema_version": "1", "runs": []}
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == check_regression._EXIT_INPUT


# ===== ADR-1005: --advisory and --skip-if-no-baseline tests =====


def test_advisory_flag_exits_zero_despite_regressions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--advisory must exit 0 even when regressions are found."""
    baseline = _all_ok_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = 500  # severe regression
    exit_code = _run_main(tmp_path, baseline, current, "--advisory")
    assert exit_code == 0
    captured = capsys.readouterr()
    # Report is still printed
    assert "REGRESSIONS" in captured.out
    # Advisory tag is present
    assert "ADVISORY" in captured.out


def test_advisory_mode_tag_absent_when_not_set() -> None:
    """render_report must not include the ADVISORY tag when advisory=False."""
    report = check_regression.render_report([], [], [], tolerance_pct=5.0, advisory=False)
    assert "ADVISORY" not in report


def test_advisory_mode_tag_present_when_set() -> None:
    """render_report must include the ADVISORY tag when advisory=True."""
    report = check_regression.render_report([], [], [], tolerance_pct=5.0, advisory=True)
    assert "ADVISORY" in report


def test_skip_if_no_baseline_exits_not_compared(tmp_path: Path) -> None:
    """--skip-if-no-baseline exits 3 when the baseline has no ok cells."""
    # Baseline with no ok cells for any backend
    baseline: dict[str, Any] = {
        "schema_version": "1",
        "runs": [
            {
                "resolution": "576",
                "backend": "cpu",
                "metric": "vif",
                "median_ms": None,
                "status": "skip",
                "skip_reason": "fixture_unavailable",
            }
        ],
    }
    current = deepcopy(baseline)
    exit_code = _run_main(tmp_path, baseline, current, "--skip-if-no-baseline")
    assert exit_code == check_regression._EXIT_NOT_COMPARED


def test_skip_if_no_baseline_proceeds_when_baseline_has_ok_cells(
    tmp_path: Path,
) -> None:
    """--skip-if-no-baseline must NOT skip when the baseline has ok cells."""
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    current["runs"][0]["median_ms"] = 500  # regression
    exit_code = _run_main(tmp_path, baseline, current, "--skip-if-no-baseline")
    # Baseline has ok cells -> comparison runs -> regression found -> exit 1
    assert exit_code == 1


def test_skip_if_no_baseline_rejects_malformed_baseline_runs(tmp_path: Path) -> None:
    """A malformed baseline `runs` is an input error even with --skip-if-no-baseline."""
    baseline: dict[str, Any] = {"schema_version": "1", "runs": "not-a-list"}
    current = _baseline_payload()
    exit_code = _run_main(tmp_path, baseline, current, "--skip-if-no-baseline")
    assert exit_code == check_regression._EXIT_INPUT


def test_skip_if_no_baseline_rejects_malformed_current_runs(tmp_path: Path) -> None:
    """A malformed current `runs` is an input error even with --skip-if-no-baseline."""
    baseline = _baseline_payload()
    current: dict[str, Any] = {"schema_version": "1", "runs": "not-a-list"}
    exit_code = _run_main(tmp_path, baseline, current, "--skip-if-no-baseline")
    assert exit_code == check_regression._EXIT_INPUT


def test_backend_without_cells_is_not_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --backend that matches no cell compares nothing and must not exit 0."""
    baseline = _baseline_payload()
    current = deepcopy(baseline)
    exit_code = _run_main(tmp_path, baseline, current, "--backend", "vulkan")
    assert exit_code == check_regression._EXIT_NOT_COMPARED
    captured = capsys.readouterr()
    assert "All cells within tolerance." not in captured.out
    assert "nothing compared" in captured.err


def test_empty_runs_are_not_compared(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two inputs with empty `runs` compare nothing and must not exit 0."""
    baseline: dict[str, Any] = {"schema_version": "1", "runs": []}
    current: dict[str, Any] = {"schema_version": "1", "runs": []}
    exit_code = _run_main(tmp_path, baseline, current)
    assert exit_code == check_regression._EXIT_NOT_COMPARED
    captured = capsys.readouterr()
    assert "All cells within tolerance." not in captured.out
    assert "nothing compared" in captured.err


def test_baseline_has_ok_cells_respects_backend_filter() -> None:
    """_baseline_has_ok_cells must filter by backend when backends is given."""
    baseline = _baseline_payload()
    # cpu has ok cells; cuda has one skip cell
    assert check_regression._baseline_has_ok_cells(baseline, backends=["cpu"]) is True
    assert check_regression._baseline_has_ok_cells(baseline, backends=["cuda"]) is True
    # Requesting a backend not present at all
    assert check_regression._baseline_has_ok_cells(baseline, backends=["sycl"]) is False
