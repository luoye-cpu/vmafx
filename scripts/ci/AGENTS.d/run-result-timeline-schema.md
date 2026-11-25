---
paths:
  - api/run-result-timeline.schema.json
  - api/testdata/run-result-timeline/*.json
  - scripts/ci/run_result_timeline_contract.py
  - scripts/ci/tests/test_run_result_timeline_schema.py
invariant: Timeline schema grows additively (HISS-14); remove, rename, retype, new required or optional member, or new event type = new schema_version.
area: tests
---
<!-- markdownlint-disable MD013 -->
# Run-result timeline schema contract (ADR-3538)

- `api/run-result-timeline.schema.json` version 1 only grows. Removed, renamed or retyped member, new required or optional member, new event type, narrower enum or changed `schema_version` contract -> new schema version, never edit of version 1.
- `provenance`, `scoring_configuration` and an event's `detail` are open pass-throughs; `scores` is a dynamic feature-name map. A new field there or a new feature name is not a timeline membership change. A new schema-owned member (required or optional) is.
- `scripts/ci/run_result_timeline_contract.py` is the combined validator: JSON Schema plus OpenAPI `Provenance` / `ScoreOptions` plus ordering (frames strict, events non-decreasing, windows start before end). Change its rules and its test in the same PR.
- Rebase conflict that drops or retypes schema member = contract break, not cleanup: keep schema side, reopen discussion (`docs/rebase-notes.d/run-result-timeline-schema.md`).
- Fixtures under `api/testdata/run-result-timeline/` change with schema in same PR. `valid-*` validate, `invalid-*` fail; `test_run_result_timeline_schema.py` checks both sets.
