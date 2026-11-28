# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Normalise one competitor wrapper's raw JSON into the external-bench schema.

Called by ``normalise_output`` in ``lib/common.sh`` as
``normalise_output.py SRC DST COMPETITOR [METRIC]``. An empty or malformed
source, a source without frames, and a missing or non-finite score, runtime or
summary field exit 1 with a named error and write nothing (ADR-3611).
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

SUMMARY_FIELDS = ("plcc", "srocc", "rmse", "params", "gflops")
ARGC_WITHOUT_METRIC = 3
ARGC_WITH_METRIC = 4
SCORE_KEYS = {"mos": ("predicted_mos", "score"), "vmaf": ("predicted_vmaf", "vmaf")}

Row = dict[str, float | int]


class NormaliseError(Exception):
    """A raw payload the schema cannot represent."""


def finite(value: object, what: str) -> float:
    """The value as a float when it is a finite int or float that is not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormaliseError(f"{what} is not numeric")
    if not math.isfinite(value):
        raise NormaliseError(f"{what} is not numeric")
    return float(value)


def load_raw(src: pathlib.Path) -> dict[str, object]:
    """Read the source file as one JSON object."""
    if src.stat().st_size == 0:
        raise NormaliseError("empty source file")
    try:
        raw = json.loads(src.read_text())
    except json.JSONDecodeError as exc:
        raise NormaliseError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise NormaliseError("source is not a JSON object")
    return raw


def frame_score(frame: dict[str, object], index: int, metric: str) -> float:
    """The frame's predicted score under the first key the metric knows."""
    keys = SCORE_KEYS["mos" if metric == "mos" else "vmaf"]
    present = [key for key in keys if key in frame]
    if not present:
        raise NormaliseError(f"frame {index} has no score")
    return finite(frame[present[0]], f"frame {index} score")


def frame_index(frame: dict[str, object], index: int) -> int:
    """The frame number the wrapper reported, else the frame's position."""
    value = frame.get("frame", frame.get("idx", index))
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise NormaliseError(f"frame {index} index is not numeric")
    try:
        return int(value)
    except ValueError as exc:
        raise NormaliseError(f"frame {index} index is not numeric") from exc


def normalise_frame(frame: object, index: int, metric: str) -> Row:
    """One output frame row, refusing a frame without a finite score and runtime."""
    if not isinstance(frame, dict):
        raise NormaliseError(f"frame {index} is not a JSON object")
    score = frame_score(frame, index, metric)
    if "runtime_ms" not in frame:
        raise NormaliseError(f"frame {index} has no runtime_ms")
    return {
        "frame_idx": frame_index(frame, index),
        "predicted_vmaf_or_mos": score,
        "runtime_ms": finite(frame["runtime_ms"], f"frame {index} runtime_ms"),
    }


def normalise_frames(raw: dict[str, object], metric: str) -> list[Row]:
    """Every frame row of the payload; an absent or empty frame list is an error."""
    frames_in = raw.get("frames", raw.get("per_frame"))
    if not frames_in:
        raise NormaliseError("no frames")
    if not isinstance(frames_in, list):
        raise NormaliseError("frames is not a JSON array")
    return [normalise_frame(frame, index, metric) for index, frame in enumerate(frames_in)]


def summary_values(raw: dict[str, object]) -> dict[str, float | int]:
    """The summary fields in schema order; params must be an int, the rest finite."""
    values: dict[str, float | int] = {}
    for field in SUMMARY_FIELDS:
        if field not in raw:
            raise NormaliseError(f"missing {field}")
        value = raw[field]
        if field != "params":
            values[field] = finite(value, field)
        elif isinstance(value, bool) or not isinstance(value, int):
            raise NormaliseError(f"{field} is not numeric")
        else:
            values[field] = value
    return values


def runtime_total(raw: dict[str, object], frames: list[Row]) -> float:
    """The reported total runtime, or the sum of the frame runtimes; both must be finite."""
    if "runtime_total_ms" not in raw:
        return finite(float(sum(frame["runtime_ms"] for frame in frames)), "runtime_total_ms")
    return finite(raw["runtime_total_ms"], "runtime_total_ms")


def normalise(raw: dict[str, object], competitor: str, metric: str) -> dict[str, object]:
    """The normalised document: frame rows plus the summary block."""
    frames = normalise_frames(raw, metric)
    values = summary_values(raw)
    summary = {
        "competitor": competitor,
        "plcc": values["plcc"],
        "srocc": values["srocc"],
        "rmse": values["rmse"],
        "runtime_total_ms": runtime_total(raw, frames),
        "params": values["params"],
        "gflops": values["gflops"],
    }
    return {"frames": frames, "summary": summary}


def main(argv: list[str]) -> int:
    """Normalise SRC into DST; exit 2 on a usage error, 1 on a refused payload."""
    if len(argv) not in (ARGC_WITHOUT_METRIC, ARGC_WITH_METRIC):
        print("usage: normalise_output.py SRC DST COMPETITOR [METRIC]", file=sys.stderr)
        return 2
    src, dst, competitor = pathlib.Path(argv[0]), pathlib.Path(argv[1]), argv[2]
    metric = argv[3] if len(argv) == ARGC_WITH_METRIC else "vmaf"
    try:
        document = normalise(load_raw(src), competitor, metric)
    except NormaliseError as exc:
        print(f"normalise_output: {competitor}: {exc}", file=sys.stderr)
        return 1
    dst.write_text(json.dumps(document, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
