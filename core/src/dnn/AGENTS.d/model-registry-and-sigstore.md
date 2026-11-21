---
paths:
  - model/tiny/registry.json
  - model/tiny/registry.schema.json
  - model/tiny/*.json
  - core/src/dnn/model_loader.c
  - ai/scripts/validate_model_registry.py
  - ai/src/aiutils/onnx_signature.py
invariant: Unsigned entries omit optional bundles; declared paths must exist; runtime verification stays opt-in.
---
<!-- markdownlint-disable MD013 -->
# Model Registry Schema and Sigstore Verification

- **Registry schema is the metadata contract** (T6-9 / [ADR-0211](../../../../docs/adr/0211-model-registry-sigstore.md)).
  Every entry in [`model/tiny/registry.json`](../../../../model/tiny/registry.json)
  must satisfy [`registry.schema.json`](../../../../model/tiny/registry.schema.json):
  required identity, graph, digest and teacher fields; the shipped registry
  also requires per-model `license` metadata (guarded by
  `python/test/model_registry_schema_test.py`). `sigstore_bundle` is optional:
  no field means unsigned, and a declared `.sigstore.json` path must exist.
  Currently all 26 registry entries are unsigned. The offline validator checks
  any declared path; `--tiny-model-verify` in `model_loader.c` parses the
  registry inline (no JSON dep), invokes `cosign` via `posix_spawnp(3p)` and
  fails closed for a missing entry, bundle field or bundle file. Verification
  remains opt-in; `system(3)` is and stays banned.

- **Per-model signing follow-up** (T6-9 / ADR-0211): declare a bundle only when
  the real per-model artifact exists. The native release archive carries
  archive-level provenance, but that does not create a per-model Sigstore
  bundle. Signing and release wiring follow up in [#1246](https://github.com/vidiko/vmafx/issues/1246) and [#3606](https://github.com/vidiko/vmafx/issues/3606). Registry
  metadata remains separate from `quant_mode` / `int8_sha256` fields from
  [ADR-0173](../../../../docs/adr/0173-ptq-int8-audit-impl.md) /
  [ADR-0174](../../../../docs/adr/0174-first-model-quantisation.md).

- **Metadata matches graph ([ADR-1546](../../../../docs/adr/1546-tiny-model-metadata-against-graphs.md))** —
  `ai/scripts/validate_model_registry.py` (required check "Tiny-Model
  Registry Validate") reads every registered graph and its int8 sibling
  with `ai/src/aiutils/onnx_signature.py` (no `onnx` package); fails
  when registry `opset` or sidecar's `opset`, `sha256`,
  `input_names` / `output_names`, `input_name` / `output_name`,
  feature-list length, `encoder_vocab` (width + 2), `codec_block_dim` or
  `codec_block_layout` disagrees with graph. re-export updates
  sidecar and registry row with graph; exporters record opset
  file imports (torch raises requested 17 to 18). runtime does
  not check registry digests: only `--tiny-model-verify` reads
  registry, for bundle path.
