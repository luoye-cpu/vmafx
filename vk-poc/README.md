# VMAFx Vulkan PoC — integer-first, bit-exact against the CPU path

Companion branch for [VMAFx/vmafx#3302](https://github.com/VMAFx/vmafx/issues/3302).
Everything lives under `vk-poc/`; the branch adds no files elsewhere and
modifies no in-tree source, model or build file.

Per the maintainer decision in that thread (2026-11-05T18:06Z): the PoC lives on
our own fork, nothing lands in-tree, ADR-0726 stays in force, and
**an integer-parity proof (one integer feature bit-exact against the CPU path)
is what decides whether an in-tree proposal + superseding ADR goes ahead.**

This branch is that proof. Two phases:

| dir | content |
|---|---|
| [`phase0/`](phase0/) | device self-check probe (`probe.comp` + `vk_probe.c`) and its two-GPU reports |
| [`phase1/`](phase1/) | **integer ADM** (`shaders/integer_adm.comp` + `src/vk_adm.c`), the CPU reference generator (`src/cpu_ref.c`), the exactness gate (`tools/compare.py`), and the committed inputs + outputs so the result is checkable without re-running |

## Result (Phase 1)

```
OK: bit-exact (48 frames x 11 fields)
```

* fixture A `576x324_48f` on **RTX 5080** (Blackwell sm_120) — bit-exact
* fixture A on **RTX 4060 Laptop** (Ada sm_89) — bit-exact (cross-GPU determinism)
* fixture B `640x480_48f` on RTX 5080 — bit-exact

Fields compared per frame: `integer_adm_num_scale0..3`,
`integer_adm_den_scale0..3`, `integer_adm_num`, `integer_adm_den`,
`VMAF_integer_feature_adm2_score` — printed as C `%a` hex-floats and compared
as exact strings. **No tolerance anywhere; equality is bit-for-bit.**

Contract anchor = the **unmodified** CPU `integer_adm` extractor of VMAFx
public master `af5ec5c9435b396ae13f0fffc9b29d040786cea8` (API `adm` feature
with `debug=true`; `phase1/build.sh` gives the CPU-only meson setup that
reproduces the exact library). Never another GPU backend, never a calibrated
places target. This branch is based on `master`, whose `core/src/feature`
integer-ADM sources and `testdata/` fixtures are identical to that commit, so
checking out this branch and running `phase1/build.sh` re-derives the CPU
reference from the same source you already have.

Both gate fixtures were also verified bit-identical against the Netflix-style
CPU extractor the removed Vulkan backend was originally written against, so the
result does not hinge on which of the two public CPU implementations you
anchor to.

`phase1/refs/` holds the CPU reference outputs and `phase1/out/` the Vulkan
outputs actually produced on 2026-11-06, so the gate can be re-checked offline:

```
python -I vk-poc/phase1/tools/compare.py \
    --ref vk-poc/phase1/refs/cpu-adm-576x324-48f.txt \
    --test vk-poc/phase1/out/vk-adm-576x324-48f.txt
# -> OK: bit-exact (48 frames x 11 fields), exit 0
```

(The two commands above run from the repository root.)

The comparator is tamper-tested — `python -I vk-poc/phase1/tools/compare.py
--selftest` proves offline that flipping one hex digit of one field makes it
exit 1 and name the frame + field — so an "OK" here cannot come from a
vacuous comparison. See [`phase1/README.md`](phase1/README.md) for the six
bit-exactness fixes this required and for the local build/run instructions.

## Provenance and licence

* The Vulkan shaders/host are ports from the Vulkan backend removed in
  ADR-0726 (our own fork history), plus PoC-only scaffolding.
* `phase1/shaders/integer_adm.comp` records, in its header, every deviation
  from the removed-backend snapshot and the CPU source line each is anchored
  to — those deviations *are* the finding.
* Vendored Vulkan-Headers (MIT, Khronos) and `glslang` 16.6.0 are **not**
  committed here; they are fetched as noted in the phase READMEs.
* This branch is experimental PoC code, not a VMAFx product release, and it
  does not propose any in-tree change.
