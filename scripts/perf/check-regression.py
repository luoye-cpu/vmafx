#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Wall-clock perf regression gate over the ADR-0752 multi-resolution baseline.

Reads the committed baseline at testdata/perf_multi_resolution.json and a fresh
run JSON produced by scripts/perf/bench-multi-resolution.sh, joins them by
(resolution, backend, metric), and exits non-zero when any cell's median_ms
exceeds the baseline by more than --tolerance-pct (default 5%).

A cell that cannot be compared is reported with one of four states (missing,
failed, skipped, incomparable) and makes the gate exit 3; pass ``--backend`` to
leave out backends the host does not have.

Exit codes: 0 every cell compared and none regressed, 1 regression, 2 input
error, 3 at least one cell not compared.

Reference: ADR-0907 — Wall-clock perf regression gate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeGuard

DEFAULT_BASELINE = Path("testdata/perf_multi_resolution.json")
DEFAULT_TOLERANCE_PCT = 5.0

# --advisory masks only this exit code (ADR-1005).
_EXIT_REGRESSION = 1
_EXIT_INPUT = 2
_EXIT_NOT_COMPARED = 3
_WORKLOAD_FIELDS = ("width", "height", "bitdepth", "frames", "fixture_source")


def _cell_key(cell: dict[str, Any]) -> tuple[str, str, str]:
    """Canonical join key: (resolution, backend, metric)."""
    return (str(cell["resolution"]), str(cell["backend"]), str(cell["metric"]))


def _index_runs(payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Build a (resolution, backend, metric) -> cell index from a baseline/run JSON."""
    runs = payload.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError("malformed perf JSON: 'runs' is not a list")
    return {_cell_key(cell): cell for cell in runs}


def _filter_backends(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    *,
    backends: Sequence[str] | None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Optionally restrict to one or more backends (CPU-only gate by default in CI)."""
    if not backends:
        return cells
    wanted = set(backends)
    return {k: v for k, v in cells.items() if k[1] in wanted}


def _selected_cells(
    payload: dict[str, Any],
    *,
    backends: Sequence[str] | None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index and optionally filter payload runs to the requested backends."""
    return _filter_backends(_index_runs(payload), backends=backends)


def _hardware(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the payload hardware dict, or empty when absent or malformed."""
    hw = payload.get("hardware")
    return hw if isinstance(hw, dict) else {}


def _is_number(value: Any) -> TypeGuard[int | float]:
    """True for a finite int or float; bool and NaN do not count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _median_for_report(cell: dict[str, Any] | None) -> float | int | None:
    if cell is None:
        return None
    value = cell.get("median_ms")
    return value if _is_number(value) else None


def _not_compared(
    key: tuple[str, str, str],
    state: str,
    reason: str,
    base_cell: dict[str, Any] | None,
    cur_cell: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a not-compared report entry."""
    return {
        "key": key,
        "state": state,
        "reason": reason,
        "baseline_ms": _median_for_report(base_cell),
        "current_ms": _median_for_report(cur_cell),
    }


def _workload_incomparable_reason(
    base_cell: dict[str, Any], cur_cell: dict[str, Any]
) -> str | None:
    for field in _WORKLOAD_FIELDS:
        if base_cell.get(field) != cur_cell.get(field):
            return (
                f"workload {field} baseline={base_cell.get(field)} "
                f"current={cur_cell.get(field)}"
            )
    return None


def _hardware_incomparable_reason(
    key: tuple[str, str, str],
    base_hw: dict[str, Any],
    cur_hw: dict[str, Any],
) -> str | None:
    field = "cpu_model" if key[1] == "cpu" else "gpu_model"
    base_val = base_hw.get(field)
    cur_val = cur_hw.get(field)
    base_ok = isinstance(base_val, str) and bool(base_val)
    cur_ok = isinstance(cur_val, str) and bool(cur_val)
    if not base_ok or not cur_ok or base_val != cur_val:
        return f"hardware {field} baseline={base_val} current={cur_val}"
    return None


def _status_reason(base_cell: dict[str, Any], cur_cell: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(state, reason)`` when a status or the current median rules the cell out."""
    base_status = base_cell.get("status")
    if base_status == "skip":
        return "skipped", f"baseline status skip ({base_cell.get('skip_reason', '')})"
    if base_status != "ok":
        return "failed", f"baseline status {base_status}"
    cur_status = cur_cell.get("status")
    if cur_status != "ok":
        return "failed", f"current status {cur_status}"
    if not _is_number(cur_cell.get("median_ms")):
        return "failed", "current median_ms missing or not numeric"
    return None


def _parity_reason(
    key: tuple[str, str, str],
    base_cell: dict[str, Any],
    cur_cell: dict[str, Any],
    base_hw: dict[str, Any],
    cur_hw: dict[str, Any],
) -> tuple[str, str] | None:
    """Return ``(state, reason)`` when workload, hardware or baseline median rule the cell out."""
    reason = _workload_incomparable_reason(base_cell, cur_cell)
    if reason is None:
        reason = _hardware_incomparable_reason(key, base_hw, cur_hw)
    if reason is not None:
        return "incomparable", reason
    base_ms = base_cell.get("median_ms")
    if not _is_number(base_ms) or base_ms <= 0:
        return "incomparable", "baseline median_ms <= 0 or not numeric"
    return None


def _compare_cell(
    key: tuple[str, str, str],
    base_cell: dict[str, Any],
    cur_cell: dict[str, Any] | None,
    base_hw: dict[str, Any],
    cur_hw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Compare one cell; return ``(record, None)`` or ``({}, not_compared_entry)``."""
    if cur_cell is None:
        return {}, _not_compared(key, "missing", "missing in current run", base_cell, None)
    verdict = _status_reason(base_cell, cur_cell)
    if verdict is None:
        verdict = _parity_reason(key, base_cell, cur_cell, base_hw, cur_hw)
    if verdict is not None:
        state, reason = verdict
        return {}, _not_compared(key, state, reason, base_cell, cur_cell)
    base_ms = float(base_cell["median_ms"])
    cur_ms = float(cur_cell["median_ms"])
    return {
        "key": key,
        "baseline_ms": base_ms,
        "current_ms": cur_ms,
        "delta_pct": (cur_ms - base_ms) / base_ms * 100.0,
    }, None


def compare_runs(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    tolerance_pct: float,
    backends: Sequence[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare two perf-bench payloads.

    Returns (regressions, improvements, not_compared). regressions are cells whose
    current median_ms exceeds baseline by > tolerance_pct. The third list holds
    every cell that was not compared, each with a ``state``.
    """
    base = _selected_cells(baseline, backends=backends)
    cur = _selected_cells(current, backends=backends)
    base_hw = _hardware(baseline)
    cur_hw = _hardware(current)

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    not_compared: list[dict[str, Any]] = []

    for key, base_cell in sorted(base.items()):
        record, entry = _compare_cell(key, base_cell, cur.get(key), base_hw, cur_hw)
        if entry is not None:
            not_compared.append(entry)
            continue
        delta_pct = record["delta_pct"]
        if delta_pct > tolerance_pct:
            regressions.append(record)
        elif delta_pct < -tolerance_pct:
            improvements.append(record)

    for key in sorted(cur.keys()):
        if key not in base:
            not_compared.append(
                _not_compared(key, "missing", "missing in baseline", None, cur[key])
            )

    return regressions, improvements, not_compared


def _format_row(record: dict[str, Any]) -> str:
    res, backend, metric = record["key"]
    return (
        f"  {res:>4}p {backend:>4} {metric:>8} : "
        f"{record['baseline_ms']:>7.1f} ms -> {record['current_ms']:>7.1f} ms "
        f"({record['delta_pct']:+6.2f}%)"
    )


def _baseline_has_ok_cells(
    payload: dict[str, Any],
    *,
    backends: Sequence[str] | None,
) -> bool:
    """Return True when the baseline contains at least one ok cell for the given backends."""
    runs = payload.get("runs", [])
    if not isinstance(runs, list):
        return False
    for cell in runs:
        if cell.get("status") != "ok":
            continue
        if backends and cell.get("backend") not in set(backends):
            continue
        return True
    return False


def render_report(
    regressions: list[dict[str, Any]],
    improvements: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    tolerance_pct: float,
    advisory: bool = False,
) -> str:
    """Build a human-readable report (stable layout for CI logs)."""
    mode_tag = (
        " [ADVISORY: regressions do not fail; cells not compared still exit 3]" if advisory else ""
    )
    lines = [f"== Perf regression gate (tolerance: +/- {tolerance_pct:.1f}%){mode_tag} =="]
    if regressions:
        lines.append("")
        lines.append(f"REGRESSIONS ({len(regressions)}):")
        for record in regressions:
            lines.append(_format_row(record))
    if improvements:
        lines.append("")
        lines.append(f"Improvements (informational, {len(improvements)}):")
        for record in improvements:
            lines.append(_format_row(record))
    if skipped:
        lines.append("")
        lines.append(f"Not compared ({len(skipped)}):")
        for record in skipped:
            res, backend, metric = record["key"]
            state = record["state"]
            lines.append(f"  {res:>4}p {backend:>4} {metric:>8} : {state}: {record['reason']}")
    if not regressions and not improvements and not skipped:
        lines.append("All cells within tolerance.")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"path to committed baseline JSON (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="path to current run JSON produced by bench-multi-resolution.sh",
    )
    parser.add_argument(
        "--tolerance-pct",
        type=float,
        default=DEFAULT_TOLERANCE_PCT,
        help=f"max allowed wall-clock regression in percent (default: {DEFAULT_TOLERANCE_PCT})",
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=None,
        help="restrict comparison to one or more backends; repeatable (default: all)",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        default=False,
        help=(
            "print the full regression report but mask only the regression exit "
            "code (ADR-1005); cells not compared still exit 3."
        ),
    )
    parser.add_argument(
        "--skip-if-no-baseline",
        action="store_true",
        default=False,
        dest="skip_if_no_baseline",
        help=(
            "stop before comparing and exit 3 when the baseline has no ok cell for "
            "the requested backends."
        ),
    )
    args = parser.parse_args(argv)
    if args.tolerance_pct <= 0:
        parser.error("--tolerance-pct must be > 0")
    return args


def _not_compared_counts(not_compared: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"missing": 0, "failed": 0, "skipped": 0, "incomparable": 0}
    for entry in not_compared:
        state = entry.get("state")
        if state in counts:
            counts[str(state)] += 1
    return counts


def _precheck(
    args: argparse.Namespace,
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> int | None:
    """Validate inputs and short-circuit before compare_runs; return an exit code or None."""
    base_cells = _selected_cells(baseline, backends=args.backend)
    cur_cells = _selected_cells(current, backends=args.backend)
    if args.skip_if_no_baseline and not _baseline_has_ok_cells(baseline, backends=args.backend):
        sys.stderr.write(
            "perf-regression-gate: baseline has no ok cells for the requested "
            "backend(s); not compared (exit 3)\n"
        )
        return _EXIT_NOT_COMPARED
    if not base_cells and not cur_cells:
        sys.stderr.write(
            "perf-regression-gate: no cell for the requested backend(s) in either "
            "input; nothing compared (exit 3)\n"
        )
        return _EXIT_NOT_COMPARED
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        current = json.loads(args.current.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"perf-regression-gate: failed to read input JSON: {exc}\n")
        return _EXIT_INPUT

    try:
        precheck = _precheck(args, baseline, current)
        if precheck is not None:
            return precheck

        regressions, improvements, not_compared = compare_runs(
            baseline,
            current,
            tolerance_pct=args.tolerance_pct,
            backends=args.backend,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"perf-regression-gate: malformed input JSON: {exc}\n")
        return _EXIT_INPUT

    print(
        render_report(
            regressions,
            improvements,
            not_compared,
            tolerance_pct=args.tolerance_pct,
            advisory=args.advisory,
        )
    )
    if args.advisory and regressions:
        print(
            f"\nperf-regression-gate: {len(regressions)} regression(s) found "
            "but --advisory mode is active; exiting 0 only when every cell was "
            "compared. See ADR-1005 for the promotion plan."
        )
    if regressions and not args.advisory:
        return _EXIT_REGRESSION
    if not_compared:
        counts = _not_compared_counts(not_compared)
        sys.stderr.write(
            f"perf-regression-gate: {len(not_compared)} cell(s) not compared "
            f"(missing={counts['missing']} failed={counts['failed']} "
            f"skipped={counts['skipped']} incomparable={counts['incomparable']}); "
            "exit 3\n"
        )
        return _EXIT_NOT_COMPARED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
