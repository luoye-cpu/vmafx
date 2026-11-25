# Copyright 2026 luoye-cpu
# SPDX-License-Identifier: EUPL-1.2
"""Acceptance fixtures for api/run-result-timeline.schema.json (issue #2452).

The schema is the skeleton the RC5 run-result timeline grows from; these
fixtures pin the shape the issue's acceptance names:

- the boundary document - every section present, all series empty - validates;
- the positive document, which carries entries in every section, validates;
- the sections the schema leaves open follow the OpenAPI contracts they name;
- a planted event without a time key is refused by that one error alone, and
  the known version validates once the key is restored;
- an unknown schema version is refused by that one error alone, and version 1
  validates once restored.

The byte-equal write-read-write round trip needs the writer/reader C ABI and
lands with it in a later PR of the same issue.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "api" / "run-result-timeline.schema.json"
FIXTURE_DIR = ROOT / "api" / "testdata" / "run-result-timeline"
OPENAPI_PATH = ROOT / "api" / "openapi" / "vmafx-server-v1.yaml"
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from run_result_timeline_contract import (  # noqa: E402
    TimelineContractError,
    validate_timeline,
)


def openapi_validator(name: str) -> jsonschema.Draft4Validator:
    """A validator for one OpenAPI 3.0 component; its $refs resolve in the same document."""
    components = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))["components"]
    return jsonschema.Draft4Validator({**components["schemas"][name], "components": components})


class RunResultTimelineSchemaTests(unittest.TestCase):
    """The shape rules the #2452 skeleton must hold."""

    @classmethod
    def setUpClass(cls) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator: Any = jsonschema.Draft202012Validator(schema)

    def load(self, name: str) -> dict:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    def series_keys(self, entries: list[dict], label: str) -> list:
        for key in ("frame_index", "timestamp_ns", "timecode"):
            if all(key in entry["time"] for entry in entries):
                return [entry["time"][key] for entry in entries]
        fail = f"{label}: no common sortable time key"
        raise AssertionError(fail)

    def test_valid_minimal_document(self) -> None:
        """Boundary: every section present, all series empty."""
        document = self.load("valid-minimal.json")
        self.validator.validate(document)
        validate_timeline(document)

    def test_valid_full_document(self) -> None:
        """Positive: provenance, scoring configuration, frames, windows, events."""
        document = self.load("valid-full.json")
        self.validator.validate(document)
        validate_timeline(document)

    def test_full_document_sections_follow_the_openapi_contract(self) -> None:
        """The sections the schema leaves open follow the contract they name."""
        document = self.load("valid-full.json")
        for section, component in (
            ("provenance", "Provenance"),
            ("scoring_configuration", "ScoreOptions"),
        ):
            validator = openapi_validator(component)
            with self.subTest(section=section):
                errors = [
                    f"{e.json_path}: {e.message}" for e in validator.iter_errors(document[section])
                ]
                self.assertEqual(errors, [])
                self.assertEqual(
                    set(document[section]) - set(validator.schema["properties"]), set()
                )

    def test_full_document_keeps_the_rules_the_schema_cannot_state(self) -> None:
        """Frames ascend strictly, events ascend by their common coordinate; windows order."""
        document = self.load("valid-full.json")
        frames = self.series_keys(document["frames"], "frames")
        self.assertEqual(frames, sorted(set(frames)))
        events = self.series_keys(document["events"], "events")
        self.assertEqual(events, sorted(events))
        for window in document["windows"]:
            with self.subTest(window=window["statistic"]):
                self.assertLess(window["start"]["frame_index"], window["end"]["frame_index"])

    def test_event_without_time_key_refused_by_name(self) -> None:
        """A planted event without a time key is refused, naming the event."""
        document = self.load("invalid-event-without-time.json")
        errors = [
            (e.json_path, e.validator, e.message) for e in self.validator.iter_errors(document)
        ]
        self.assertEqual(errors, [("$.events[0]", "required", "'time' is a required property")])
        document["events"][0]["time"] = {"frame_index": 0}
        self.validator.validate(document)

    def test_unknown_schema_version_refused_by_name(self) -> None:
        """An unknown schema version is refused, naming the member."""
        document = self.load("invalid-unknown-schema-version.json")
        errors = [(e.json_path, e.validator) for e in self.validator.iter_errors(document)]
        self.assertEqual(errors, [("$.schema_version", "const")])
        document["schema_version"] = 1
        self.validator.validate(document)

    def test_frame_without_scores_is_refused(self) -> None:
        """A frame must carry its score map; the old optional form is gone."""
        document = self.load("valid-full.json")
        document["frames"][0].pop("scores")
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("scores", str(caught.exception))

    def test_empty_scores_map_is_refused(self) -> None:
        """A frame's score map must not be empty: it would not be a score."""
        document = self.load("valid-full.json")
        document["frames"][0]["scores"] = {}
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("scores", str(caught.exception))

    def test_invalid_scoring_option_is_refused_by_the_combined_validator(self) -> None:
        """A timeline-only validator cannot see ScoreOptions; the combined one must."""
        document = self.load("valid-full.json")
        document["scoring_configuration"] = {"pixel_format": "bogus"}
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("pixel_format", str(caught.exception))

    def test_duplicate_and_out_of_order_frames_are_refused(self) -> None:
        """Duplicate and descending frame coordinates are refused, not just sorted away."""
        document = self.load("valid-full.json")
        document["frames"][1]["time"]["frame_index"] = 0
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("frames", str(caught.exception))

    def test_timestamp_only_and_timecode_only_series_validate(self) -> None:
        """Ordering works for every coordinate kind the schema allows."""
        timestamp_document = self.load("valid-full.json")
        for index, timestamp in enumerate((0, 41708333)):
            timestamp_document["frames"][index]["time"] = {"timestamp_ns": timestamp}
        validate_timeline(timestamp_document)

        timecode_document = self.load("valid-full.json")
        for index, timecode in enumerate(("00:00:00:00", "00:00:00:01")):
            timecode_document["frames"][index]["time"] = {"timecode": timecode}
        validate_timeline(timecode_document)

    def test_out_of_order_timecode_series_is_refused(self) -> None:
        """Timecode ordering is checked by its HH:MM:SS[:FF] fields."""
        document = self.load("valid-full.json")
        document["frames"][1]["time"] = {"timecode": "00:00:00:00"}
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("frames", str(caught.exception))

    def test_window_without_common_coordinate_is_refused(self) -> None:
        """A window cannot compare a frame index with a timecode."""
        document = self.load("valid-full.json")
        document["windows"][0]["end"] = {"timecode": "00:00:00:02"}
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("windows[0]", str(caught.exception))

    def test_window_start_after_end_is_refused(self) -> None:
        """A window whose start is not strictly before its end is refused."""
        document = self.load("valid-full.json")
        document["windows"][0]["start"] = {"frame_index": 2}
        with self.assertRaises(TimelineContractError) as caught:
            validate_timeline(document)
        self.assertIn("windows[0]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
