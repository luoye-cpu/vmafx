<!-- Copyright 2026 luoye-cpu -->
<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Run-result timeline C ABI — design notes for the #2452 writer/reader PR (2026-11-10)

Anchors: [VMAFx/vmafx#2452](https://github.com/VMAFx/vmafx/issues/2452) (RC5,
milestone 0.2.0), the schema skeleton in
[`api/run-result-timeline.schema.json`](../../api/run-result-timeline.schema.json)
(pull request [#3385](https://github.com/VMAFx/vmafx/pull/3385)), and the
maintainer's narrow-start agreement of 2026-11-07 22:01Z in the issue thread
(schema first, the C ABI writer/reader and the identity checks in later PRs
once the shape has been reviewed). This document is the research digest for
that follow-up pull request; nothing here changes a shipped surface.

## What the issue's four acceptance checks need

1. **Identity** — a fixture run through the CLI, the C API and the server
   yields identical documents (modulo elapsed time).
2. **Named refusal of an event without a time key.**
3. **Named refusal of an unknown schema version.**
4. **Byte-equal write, read, write round trip.**

Checks 2-4 live entirely in the C core. Check 1 spans three surfaces and is
the only one that needs the server; the split below keeps it in its own PR.

## The writer side: extend `vmafx_report_write`, do not add a parallel writer

`vmafx_report_write` already exists (`core/api/vmafx.toml`, header
`vmafx/report.h`, since ABI 0.1.5): it writes a context's scores to a path in
a `VmafxReportFormat` (JSON/XML/CSV/SUB), takes a printf score format, and can
emit a `<path>.provenance.json` sidecar. A second, parallel timeline writer
would duplicate that machinery (HISS-19), so the recommendation is:

- Add one `VmafxReportFormat` value for the run-result timeline document, so
  `vmafx_report_write(path, VMAFX_REPORT_RUN_RESULT_TIMELINE, ...)` emits the
  schema of [#3385](https://github.com/VMAFx/vmafx/pull/3385) with the
  context's per-frame scores in `frames`, its pooled statistics in `windows`,
  and the provenance record embedded verbatim (the record the context already
  renders canonically via `vmafx_context_provenance_json`,
  `core/src/vmafx/provenance_json.c`).
- `windows` come from the context's existing pooling path; `frames` from the
  per-frame score store. No second data path.
- **Events are the one genuinely new input.** The context does not track
  alignment decisions, drops, repeats, freezes, switches or masked areas
  today; the producers of those facts live above the library (CLI tooling,
  the server). An additive call such as
  `vmafx_context_add_run_event(context, const VmafxRunEvent* event)`
  (sized struct: type from the schema's additive vocabulary, a time-key
  struct, an optional detail JSON string) lets callers feed the events they
  own, and the report writer serialises what it was given. Whether the
  first PR carries this call or the timeline format ships with an
  events-empty contract first is a review question.
- `scoring_configuration` is a server-side surface (the `scoring` group of
  `api/openapi/vmafx-server-v1.yaml`, #2258); a CLI-produced document omits
  it (the schema makes it optional), and the server fills it from its option
  surface.

## The reader side: a new, small surface

No reader exists in the tree. Sketch (header `vmafx/run_result.h`, additive
under the 0.x ABI, since the next patch node per ADR-1897):

- `vmafx_run_result_reader_open(const char* text, size_t length,
  VmafxRunResultReader** out, VmafxError* error)` — parses and immediately
  validates the envelope: an unknown `schema_version` is refused by name
  (`VMAFX_E_RUN_RESULT_VERSION`, note names the version found and the
  versions known), a document whose `events[i]` lacks a time key is refused
  by name (`VMAFX_E_RUN_RESULT_TIME`, note names the event index).
- Accessors: document counts; per-frame time keys and scores; per-event
  type/time; the embedded provenance record (as text, digest included).
- `vmafx_run_result_reader_reserialize(reader, char** json, VmafxError*)` —
  canonical bytes of the parsed document, which makes check 4 a three-line
  test: write, read, reserialize, compare.
- Lifetime follows the repo's existing rules ([ADR-1906] semantics; see
  `core/src/AGENTS.d/` pages: init-and-teardown, memory-and-concurrency-
  safety, output-writers-and-locale).

## Canonical form: reuse, do not reinvent

`core/src/vmafx/provenance_json.c` implements the RFC 8785 subset the fork
already committed to (keys in byte order, no whitespace, plain integers,
escape only `"` `\` and the control chars, invalid UTF-8 to U+FFFD), with the
equivalence to Python `json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` proven by `core/tools/test/test_vmaf_provenance_report.py`.
The timeline writer and `reserialize` reuse that machinery — the same
subset, applied to the timeline document — so check 1's cross-surface
identity has the same footing the provenance digest already relies on.

## Reader parsing: options

(a) **Reuse the vendored cJSON** (`core/src/mcp/3rdparty/cJSON/`, MIT): move
or share it into a core-visible third-party location with its licence file
and `REUSE.toml` entries; `cJSON_ParseWithLength` is length-safe. Cost: a
vendored-tree relocation the MCP AGENTS pages must keep describing.
(b) A hand-rolled minimal parser in the provenance_json style (the writer's
inverse). Cost: a second parser to fuzz and maintain; benefit: no new
vendored surface. (c) A walker in the `read_json_model.c` style — that
parser is model-shape-specific, not a general JSON reader. Recommendation:
(a), with the relocation done as its own commit so the MCP tree's pointers
move visibly; the AGENTS.d pages of both trees get the updated paths in the
same pull request.

## Test plan

- `test_vmafx_run_result_roundtrip` (check 4): build a context on the
  576x324 fixture, write the timeline document, read it, reserialize, assert
  byte equality; repeat on a document round-tripped through a
  non-canonicalising JSON printer to prove canonicalisation on read.
- `test_vmafx_run_result_refusals` (checks 2-3): the fixtures of
  `api/testdata/run-result-timeline/` (`invalid-event-without-time.json`,
  `invalid-unknown-schema-version.json`) fed to the reader; assert the named
  error codes and notes.
- `test_vmafx_run_result_report_format` (writer): a context written with the
  new format validates against the schema (the validator of #3385 runs in
  Python; the C test asserts the shape invariants the writer owns and the
  Python-side gate re-validates on CI artifacts).
- Check 1 lands with the server PR: the same fixture through
  `vmafx_report_write` (CLI path), the C API path, and the server's Go
  writer, comparing canonical bytes modulo `elapsed_ns`.

## Open questions for review

1. Events: does `vmafx_context_add_run_event` belong in the first PR, or
   does the timeline format ship with the events-empty contract first?
2. cJSON relocation vs a minimal in-house parser (see above).
3. Does the server write its timeline documents through a Go-side canonical
   writer (the provenance proto-JSON path) rather than calling back into C?
   The identity check needs the two writers to agree byte-for-byte, and the
   provenance precedent already proves the Python-side equivalence.
4. Converters from the existing XML/JSON/CSV outputs: stay out of the first
   writer/reader PR (the issue lists them as part of the epic, not of the
   first slice).
