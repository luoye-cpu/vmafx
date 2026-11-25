# Copyright 2026 luoye-cpu
# SPDX-License-Identifier: EUPL-1.2
"""Combined validator for the run-result timeline (schema + semantic rules)."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "api" / "run-result-timeline.schema.json"
OPENAPI_PATH = ROOT / "api" / "openapi" / "vmafx-server-v1.yaml"


class TimelineContractError(ValueError):
    """One or more schema, OpenAPI or semantic-contract errors."""


def _schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def _openapi_validator(name: str) -> jsonschema.Draft4Validator:
    components = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))["components"]
    return jsonschema.Draft4Validator({**components["schemas"][name], "components": components})


def _openapi_errors(document: dict[str, Any], section: str, component: str) -> list[str]:
    value = document.get(section)
    if value is None:
        return []
    validator = _openapi_validator(component)
    errors = [
        f"{section}{error.json_path[1:]}: {error.message}" for error in validator.iter_errors(value)
    ]
    extra = set(value) - set(validator.schema.get("properties", {}))
    if extra:
        errors.append(f"{section}: unknown member(s) {sorted(extra)}")
    return errors


TIME_KEYS = ("frame_index", "timestamp_ns", "timecode")


def _common_time_key(entries: list[dict[str, Any]], label: str) -> str:
    """The first sortable coordinate every entry in the series carries."""
    for key in TIME_KEYS:
        if all(key in entry["time"] for entry in entries):
            return key
    raise TimelineContractError(
        f"{label}: entries share no sortable time-key coordinate (frame_index, timestamp_ns, timecode)"
    )


def _series_sort_keys(entries: list[dict[str, Any]], label: str) -> list[Any]:
    if not entries:
        return []
    key = _common_time_key(entries, label)
    return [entry["time"][key] for entry in entries]


def _check_order(entries: list[dict[str, Any]], label: str, *, strict: bool) -> None:
    keys = _series_sort_keys(entries, label)
    for prev, current in itertools.pairwise(keys):
        violation = current <= prev if strict else current < prev
        if violation:
            raise TimelineContractError(
                f"{label}: time keys are not {'strictly ' if strict else ''}ascending"
            )


def _check_windows(windows: list[dict[str, Any]]) -> None:
    for index, window in enumerate(windows):
        start, end = window["start"], window["end"]
        key = _common_time_key([{"time": start}, {"time": end}], f"windows[{index}]")
        if start[key] >= end[key]:
            raise TimelineContractError(f"windows[{index}]: start must strictly precede end")


def validate_timeline(document: dict[str, Any]) -> None:
    """Validate the timeline schema, its named OpenAPI sections and its order rules.

    The published JSON Schema cannot compare time keys or enforce array order;
    this function is the contract callers run when both matter. It raises
    :class:`TimelineContractError` with one or more messages.
    """
    errors = [
        f"{error.json_path}: {error.message}" for error in _schema_validator().iter_errors(document)
    ]
    errors.extend(_openapi_errors(document, "provenance", "Provenance"))
    errors.extend(_openapi_errors(document, "scoring_configuration", "ScoreOptions"))
    try:
        _check_order(document.get("frames", []), "frames", strict=True)
        _check_order(document.get("events", []), "events", strict=False)
        _check_windows(document.get("windows", []))
    except TimelineContractError as exc:
        errors.append(str(exc))
    if errors:
        raise TimelineContractError("; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: run_result_timeline_contract.py <timeline.json>")
        return 2
    validate_timeline(json.loads(Path(args[0]).read_text(encoding="utf-8")))
    print(f"{args[0]}: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
