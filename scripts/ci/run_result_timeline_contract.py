# Copyright 2026 luoye-cpu
# SPDX-License-Identifier: EUPL-1.2
"""Combined validator for the run-result timeline (schema + semantic rules)."""

from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml  # type: ignore[import-untyped]

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
_TIMECODE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:[:;](\d{2}))?$")


def _time_value(key: str, value: Any) -> Any:
    """A comparable value for one time-key kind."""
    if key != "timecode":
        return value
    match = _TIMECODE_RE.fullmatch(value)
    if match is None:
        raise TimelineContractError(f"timecode is not HH:MM:SS[:FF]: {value!r}")
    fields = [int(part) if part is not None else 0 for part in match.groups()]
    return tuple(fields)


def _shared_time_keys(entries: list[dict[str, Any]]) -> list[str]:
    """The time-key kinds every entry in the series carries."""
    if not entries:
        return []
    shared = set(entries[0]["time"])
    for entry in entries[1:]:
        shared.intersection_update(entry["time"])
    return [key for key in TIME_KEYS if key in shared]


def _check_order(entries: list[dict[str, Any]], label: str, *, strict: bool) -> None:
    shared = _shared_time_keys(entries)
    if entries and not shared:
        raise TimelineContractError(
            f"{label}: entries share no sortable time-key coordinate "
            "(frame_index, timestamp_ns, timecode)"
        )
    for key in shared:
        values = [_time_value(key, entry["time"][key]) for entry in entries]
        for prev, current in itertools.pairwise(values):
            violation = current <= prev if strict else current < prev
            if violation:
                raise TimelineContractError(
                    f"{label}: {key} keys are not {'strictly ' if strict else ''}ascending"
                )


def _check_windows(windows: list[dict[str, Any]]) -> None:
    for index, window in enumerate(windows):
        start, end = window["start"], window["end"]
        shared = [key for key in TIME_KEYS if key in start and key in end]
        if not shared:
            raise TimelineContractError(
                f"windows[{index}]: start and end share no sortable time-key coordinate "
                "(frame_index, timestamp_ns, timecode)"
            )
        for key in shared:
            if _time_value(key, start[key]) >= _time_value(key, end[key]):
                raise TimelineContractError(
                    f"windows[{index}]: {key} start must strictly precede end"
                )


def validate_timeline(document: dict[str, Any]) -> None:
    """Validate the timeline schema, its named OpenAPI sections and its order rules.

    The published JSON Schema cannot compare time keys or enforce array order;
    this function is the contract callers run when both matter. It raises
    :class:`TimelineContractError` with one or more messages.
    """
    schema_errors = [
        f"{error.json_path}: {error.message}" for error in _schema_validator().iter_errors(document)
    ]
    errors = list(schema_errors)
    if not schema_errors:
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
    try:
        validate_timeline(json.loads(Path(args[0]).read_text(encoding="utf-8")))
    except TimelineContractError as exc:
        print(f"{args[0]}: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args[0]}: {exc}", file=sys.stderr)
        return 1
    print(f"{args[0]}: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
