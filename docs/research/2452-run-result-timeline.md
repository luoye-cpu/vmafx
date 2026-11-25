# Research: run-result timeline schema (#2452)

## Question

What shape should one versioned run-result document take so that a scoring
run's provenance, per-frame scores, per-window statistics and events share
one addressable timeline, and how should that shape evolve?

## Sources

- [VMAFx/vmafx#2452](https://github.com/VMAFx/vmafx/issues/2452) - the RC5
  issue and its acceptance criteria.
- `api/run-result-timeline.schema.json` - the skeleton proposed by PR #3385.
- `api/openapi/vmafx-server-v1.yaml` - the `Provenance` and `ScoreOptions`
  components named by the skeleton.
- `core/src/output.cpp` (`json_write_frame_metric`) - the CLI's current
  frame-score JSON, including its `null` spelling for non-finite values.
- `docs/hardware-reports/report.schema.json` - the closed-object versioning
  precedent.
- [ADR-3311](../adr/3311-stream-metadata-field-list.md) - the append-only
  field-list precedent.

## Findings

1. The issue asks for one envelope before alignment, fields, masks and
   bits-per-pixel grow their own reports. The common address is a time key
   carrying a frame index, timestamp or timecode, with one required key per
   entry.
2. The repository has two versioning precedents. The hardware report closes
   its objects and bumps `schema_version` for every change; the stream
   metadata list stays append-only under version 1. A timeline whose objects
   are closed cannot honestly claim the append-only model for its own
   members, so the skeleton follows the closed model and keeps only the
   sections owned by another contract open.
3. Three sections have a single field-contract source elsewhere:
   `provenance` (OpenAPI `Provenance`), `scoring_configuration` (OpenAPI
   `ScoreOptions`) and an event's `detail` (writer-defined). Copying their
   fields would create drift; referencing them keeps one source of truth.
4. `scores` is not a closed object with schema-owned members. Its keys are
   feature names supplied by the mounted models, so a new feature is a new
   map key, not a new schema member. It needs `minProperties: 1` and the
   `null` value spelling, not a version bump.
5. JSON Schema cannot compare time keys, enforce frame order, reject
   duplicate frames or check a window's start/end order. The schema and the
   test therefore need a combined validator that adds those semantic rules
   and the two OpenAPI components.
6. A frame score without a score map is not a score. The first skeleton made
   `scores` optional; the acceptance fixture should require it and refuse an
   empty map.

## Decision impact

ADR-3538 records the closed-object model, the three pass-through sections,
the `scores` dynamic-map exception and the rule that every schema-owned member
(required or optional) and every event type takes a new version. This research
is the digest behind that ADR; the schema, the combined validator
(`scripts/ci/run_result_timeline_contract.py`), the fixtures and the human
reference in `docs/api/run-result-timeline.md` carry the result.
