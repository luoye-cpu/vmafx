<!-- markdownlint-disable MD060 -->
# Tiny-model registry — schema and verification

The registry at [`model/tiny/registry.json`](../../model/tiny/registry.json) is
the metadata index for libvmaf's tiny-AI surface. Every ONNX model shipped
under `model/tiny/` is indexed there with a SHA-256 pin and license metadata.
T6-9 / [ADR-0211](../adr/0211-model-registry-sigstore.md) formalised the schema
and wired `--tiny-model-verify` to `cosign verify-blob`.

The registry holds 26 entries, all currently unsigned. Each entry omits
`sigstore_bundle` because no per-model Sigstore bundles ship. Verification is
off by default. Enabling it rejects all 26 current entries.
[#3606](https://github.com/vidiko/vmafx/issues/3606) tracks the removal of the
placeholder paths. Per-model signing and release integration remain open in
[#1246](https://github.com/vidiko/vmafx/issues/1246).
Thirteen entries are CI smoke fixtures with `smoke: true`, see
[CI-only smoke fixtures](#ci-only-smoke-fixtures).

## Registry shape

```jsonc
{
  "$schema": "./registry.schema.json",
  "schema_version": 1,
  "models": [
    {
      "id": "learned_filter_v1",          // stable id; a label, not a --tiny-model value
      "kind": "filter",                    // fr | nr | filter
      "onnx": "learned_filter_v1.onnx",
      "opset": 17,
      "sha256": "412d537…e27",
      "teacher": "none",                   // vmaf model id | none | unknown
      "quant_mode": "dynamic",             // fp32 | dynamic | static | qat
      "int8_sha256": "1cff6fe…2d3",
      "quant_accuracy_budget_plcc": 0.01,
      "license": "BSD-2-Clause-Patent",
      "license_url": "https://github.com/vidiko/vmafx/blob/master/NOTICE",
      "description": "Tiny residual filter for vmafx_pre — degraded → clean luma.",
      "notes": "Self-supervised on KoNViD-1k …"
    }
  ]
}
```

!!! note
    `--tiny-model` takes a file path. The registry `id` is a stable name used by
    the validator, docs and tooling. The CLI does not resolve it to a file.

The full JSON Schema is
[`model/tiny/registry.schema.json`](../../model/tiny/registry.schema.json). The
schema rejects unknown properties (`additionalProperties: false`).

### Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable identifier, `^[a-z0-9][a-z0-9_-]*$`, at most 64 characters. |
| `kind` | yes | `fr`, `nr` or `filter`. |
| `onnx` | yes | ONNX path relative to `model/tiny/`. |
| `sha256` | yes | Lowercase hex, 64 characters, of the exact ONNX bytes. A mismatch against the on-disk file is a hard error. |
| `teacher` | yes | VMAF model id under `model/`; `none` = trained without a VMAF teacher; `unknown` = the tree holds no record of one. |
| `opset` | non-smoke | ONNX opset of the default domain, 7 to 21, as the graph's `opset_import` declares it; the sidecar `opset` carries the same value. The validator checks both against the file and its int8 sibling. |
| `smoke` | no | `true` for CI load-path probes, which are not quality models. |
| `quant_mode` | no | `fp32` (default), `dynamic`, `static` or `qat`. Any other value than `fp32` makes the runtime load `<onnx>.int8.onnx`; see [quantization.md](quantization.md). |
| `int8_sha256` | iff `quant_mode != "fp32"` | SHA-256 of the `.int8.onnx` file. The validator checks it; the runtime redirect to the int8 file does not ([quantization.md](quantization.md)). |
| `quant_calibration_set` | `static` only | Calibration tensor blob, relative to the repo root. |
| `quant_accuracy_budget_plcc` | no | Maximum PLCC drop versus fp32; the CI `ai-quant-accuracy` job fails a quantised model beyond it. Default 0.01. |
| `license` | schema version 1 | SPDX identifier. Fork-trained models use `BSD-2-Clause-Patent`, matching libvmaf. Upstream-derived models keep the upstream license verbatim (LPIPS-Sq is `BSD-2-Clause`). |
| `license_url` | no | URL of the license text. |
| `sigstore_bundle` | no | Path relative to `model/tiny/`, ending in `.sigstore.json`. Declare only when the real bundle exists; omit for unsigned models. |
| `description` | no | One-line summary, distinct from `notes`. |
| `notes` | no | Free-text provenance and training recipe, at most 512 characters. |
| `release_url` | no | HTTPS URL of a release attachment holding the ONNX bytes, for a model not tracked in git; `scripts/ai/fetch-tiny-blobs.sh` downloads it and checks `sha256`. No shipped entry sets it, see [tiny-blob-storage.md](tiny-blob-storage.md). |

The optional `sigstore_bundle` field records an existing per-model bundle;
it is not a reservation for a future release file. The runtime verifier
(`--tiny-model-verify`) fails closed when either the field or its file is
missing. The native release workflow signs and attests `models.tar.gz`, which
contains the entire `model/` tree, but does not generate per-model bundles.
See [release archive coverage](security.md#release-archive-coverage) for the
workflow and packaging sources.

## Validate the registry

[`ai/scripts/validate_model_registry.py`](../../ai/scripts/validate_model_registry.py)
runs the JSON Schema check and the cross-file consistency check: every ONNX
exists, every `sha256` matches, every non-smoke entry has a sidecar. Declared
bundle paths must name existing files under the registry directory; absolute
paths, dot-segment traversal and symlink escapes are rejected. These checks
validate metadata and file presence, not the bundle's signature. It also
reads each graph (and its int8 sibling) with
[`ai/src/aiutils/onnx_signature.py`](../../ai/src/aiutils/onnx_signature.py),
which needs no `onnx` package, and fails when the registry `opset` or a
sidecar's `opset`, `sha256`, `input_names` / `output_names`, `input_name` /
`output_name`, feature-list length, `encoder_vocab`, `codec_block_dim` or
`codec_block_layout` disagrees with the graph. It is a CI gate; run it before
pushing:

```bash
python3 ai/scripts/validate_model_registry.py \
    --out-json runs/tiny_model_registry_validation.json
# → OK: 26 registry entries valid against registry.schema.json
```

To validate an off-tree registry, pass it and its schema explicitly:

```bash
python3 ai/scripts/validate_model_registry.py /path/to/other-registry.json \
    --schema /path/to/registry.schema.json \
    --out-json runs/offtree_registry_validation.json
```

The validator requires `jsonschema` for Draft 2020-12 validation and exits with
status 2 if it is unavailable; it never reports a partial structural check as a
pass. Install the hash-locked dependency from
[`requirements/locks/jsonschema.txt`](../../requirements/locks/jsonschema.txt).

The optional `--out-json` report records the verdict, model count, errors,
registry and schema paths, and the ADR-0661 `run_provenance` block. Use it for
release evidence and for debugging failures without CI log scrollback.

## Verify a model at runtime

`--tiny-model-verify` makes the loader check the model's Sigstore bundle before
inference. The current registry is unsigned, so the following command
intentionally fails closed because `learned_filter_v1` has no
`sigstore_bundle` field:

```bash
vmaf -r ref.y4m -d dis.y4m \
     --tiny-model model/tiny/learned_filter_v1.onnx \
     --tiny-model-verify
```

The loader runs these steps:

1. Look up the model's basename in `model/tiny/registry.json` (alongside the
   `.onnx` by default).
2. Read the entry's `sigstore_bundle` path.
3. Spawn `cosign verify-blob --bundle=<path> --certificate-identity-regexp …
   --certificate-oidc-issuer https://token.actions.githubusercontent.com <onnx>`
   with `posix_spawnp(3p)` and an explicit argv array, no shell.
4. Refuse to load on a non-zero exit, a missing `cosign`, a missing
   `sigstore_bundle` field, a missing bundle file or a missing registry entry.

The flag is off by default. Until per-model signing and release integration
lands in [#1246](https://github.com/vidiko/vmafx/issues/1246), no model can
pass `--tiny-model-verify`. A deployment that signs bundles with its own
identity must run `cosign verify-blob` outside vmaf and leave this flag off.
On POSIX hosts, `cosign` must be on `$PATH`; install a prebuilt binary from
the [Sigstore release page](https://github.com/sigstore/cosign/releases).
Windows returns `-ENOSYS` and refuses the load. The C entry point is
`vmaf_dnn_verify_signature(onnx_path, registry_path)` in
[`core/include/libvmaf/dnn.h`](../../core/include/libvmaf/dnn.h).
`onnx_path` must be non-NULL; a NULL `registry_path` selects the registry next
to the model (or `model/tiny/registry.json` for a bare filename).

### Directory jail

The registry pins model identity. The optional `VMAF_TINY_MODEL_DIR`
environment variable constrains model location. Set it on production hosts:

```bash
export VMAF_TINY_MODEL_DIR=/opt/vmaf-models
vmaf --tiny-model /opt/vmaf-models/vmaf_tiny_v2.onnx ...
```

At load time libvmaf canonicalises the jail and the requested ONNX path. The
model must resolve below the jail. Sibling-prefix escapes, symlink escapes,
missing jail directories, and jail values that point at files fail closed with
`-EACCES`. The jail and operator allowlist apply to unsigned models too.
Deployments that also require signer verification must wait for
[#1246](https://github.com/vidiko/vmafx/issues/1246): until per-model bundles
ship, no model can pass `--tiny-model-verify`, and a deployment with its own
signer runs `cosign verify-blob` outside vmaf and leaves the flag off (see
[security.md](security.md)).

## What the registry is not

- Not the inference contract. Per-model input and output names, normalisation
  and expected ranges live in the sidecar JSON next to the ONNX
  (`<basename>.json`). The registry stays small and easy to audit. Attached
  multi-output models may use sidecar `output_names[]` for stable scalar-score
  suffixes in report keys.
- Not the operator-allowlist source of truth. That is
  `core/src/dnn/op_allowlist.c`. The registry pins identity; the allowlist
  constrains content.
- Not a path allowlist. Use `VMAF_TINY_MODEL_DIR` to reject model paths outside
  a trusted directory.

## Add a model

1. Drop the `.onnx` and the matching `<basename>.json` sidecar under
   `model/tiny/`.
2. Compute the digest: `sha256sum model/tiny/<name>.onnx`.
3. Add an entry to `registry.json`. Use existing entries as templates; the
   schema enforces required fields.
4. Omit `sigstore_bundle` while the model is unsigned. Add it only when a real
   bundle for the exact ONNX bytes exists; do not declare a future file.
   Signing and release integration are tracked in
   [#1246](https://github.com/vidiko/vmafx/issues/1246).
5. Run `python3 ai/scripts/validate_model_registry.py` and fix what it reports.

The `/add-model <path>` skill scaffolds steps 1 to 4.

## CI-only smoke fixtures

Thirteen registry entries (`smoke: true`) exist to exercise the loader and
validator in CI. They are not user-facing surfaces, are exempt from the
ADR-0042 five-point model-card requirement, and are excluded from doc-coverage
checks. Do not set them to `smoke: false` without a model card and the full
ADR-0042 bar.

| Registry id | Notes |
|---|---|
| `smoke_v0` | Minimal ONNX graph used by the `test_model_loader.c` smoke test. |
| `smoke_fp16_v0` | Same graph with fp16 weights; exercises the fp16 loader path. |
| `smoke_multi_output_v0` | Multi-output fixture (`mean_score` and `peak_score`) used by `test_vmaf_use_tiny_model.c`; exercises the attached multi-output path. |
| `smoke_v0_symbolic_batch` | Symbolic-batch fixture (dynamic first dim) used by `test_vmaf_use_tiny_model.c`; exercises the batch-agnostic load path. |
| `dists_sq_placeholder_v0` | Superseded placeholder; replaced by `dists_sq` (real weights). Card: [dists_sq](models/dists_sq.md). |
| `mobilesal_placeholder_v0` | Placeholder until the full U-2-Net weights clear compliance (ADR-0257). Card: [mobilesal](models/mobilesal.md). |
| `vmaf_tiny_v1` | Superseded by `vmaf_tiny_v2`; kept for loader back-compat tests. |
| `vmaf_tiny_v1_medium` | Superseded by `vmaf_tiny_v2`; kept for loader back-compat tests. |
| `fr_regressor_v2_ensemble_v1_seed0` to `_seed4` (five entries) | Smoke placeholders for the ensemble members; the production weights are LOSO-validated and described in each seed sidecar. See [fr_regressor_v2 probabilistic](models/fr_regressor_v2_probabilistic.md). |

## `lpips_sq_v1` and its cards

The registry entry `lpips_sq_v1` (`smoke: false`) points to `lpips_sq.onnx`.
Two model-card pages describe the same model:
[lpips_sq](models/lpips_sq.md) and [lpips_sq_v1](models/lpips_sq_v1.md). The
registry id and the file name differ by the `_v1` suffix; this is a tracked
cosmetic gap (scaffold-audit ADR-0621 P3-5).
