# VK PoC Phase 1 — integer ADM, bit-exact vs CPU: PASSED

Status: **GATE PASSED** (2026-11-06). `integer_adm` computed by the Vulkan path is
**bit-for-bit identical** to the unmodified CPU `integer_adm` extractor —
built from VMAFx public master `af5ec5c9435b396ae13f0fffc9b29d040786cea8`
(CPU 1.0.0-rc.3-660, mingw static, `-Denable_cuda=false -Denable_sycl=false
-Denable_hip=false -Denable_metal=disabled -Denable_zimg=false`) — on every
frame of both gate fixtures, on both local NVIDIA GPUs.

Anchor cross-check: the same gate fixtures produce **byte-identical**
reference files from the Netflix-style CPU extractor the removed backend was
originally written against, so this result does not depend on which of the
two public CPU implementations you treat as the contract.

| fixture | GPU | result |
|---|---|---|
| A: `testdata/{ref,dis}_576x324_48f.yuv` (576x324, 48f) | RTX 5080 (sm_120, device 0x2c02) | **bit-exact, 48 frames x 11 fields** (`compare.py` exit 0) |
| A on RTX 4060 Laptop (sm_89, device 0x28e0) | RTX 4060 | **bit-exact** (cross-GPU determinism) |
| B: `testdata/{ref,dis}_640x480_48f.yuv` (640x480, 48f) | RTX 5080 | **bit-exact, 48 frames x 11 fields** |

Fixtures are the upstream libvmaf `testdata` 48-frame pairs (yuv420p8), tracked
in `testdata/` on both this fork and upstream `master`, so the inputs are
fetchable. sha256:

```
ref_576x324_48f.yuv  1a61ff158ab0dacdd3c64e4c63602a7c2ef6da10cba0d8aa7893f6ae9fb64816
dis_576x324_48f.yuv  4e9a22d7edc1fd8c1aac396366a9eaaf1f946bec834c18bcfb2713e70bce8f26
ref_640x480_48f.yuv  2430b0de1aad6d44f2680b13a1c3a32ba4ec9ec9506ca65c5fed8199b909537d
dis_640x480_48f.yuv  984cbae373b70dade18077bb8a72aeda2257bc9663ca55f924833ecfa870976d
```

Both GPUs were under an unrelated compute workload (~98-100% SM util) during the
runs — correctness is load-independent; no performance claims are made.

Gate compared, per frame: `integer_adm_num_scale0..3`,
`integer_adm_den_scale0..3` (each a float concluded from the reduced int64
accumulators), the frame totals `integer_adm_num/den` and
`VMAF_integer_feature_adm2_score` — printed as `%a` hex floats and compared as
exact strings (`tools/compare.py`).

## Layout

- `shaders/integer_adm.comp` — port of the shader from the Vulkan backend
  removed in ADR-0726, with the bit-exactness patches listed in its header (and
  below). This is the file that produced `build/integer_adm.spv` byte-for-byte.
- `src/vk_adm.c` — standalone dynlink Vulkan host (no SDK, no volk/VMA; same
  pattern as `../phase0/src/vk_probe.c`) plus the CPU-exact conclude functions.
- `src/cpu_ref.c`, `refs/cpu-adm-*.txt` — unmodified-CPU reference generator
  (public VMAFx API, `adm` feature with `debug=true`) and its outputs.
  `build.sh` shows the CPU-only meson setup line that reproduces the exact
  library the references were taken from at `af5ec5c9`.
- `tools/compare.py` — bit-exact comparator (`--ref X --test Y`, exit 0/1/2)
  with a built-in tamper test: `python -I tools/compare.py --selftest` proves
  offline that a flipped hex digit is caught and named, so an "OK" can never
  come from a vacuous comparison.
- `build/integer_adm.spv` — the compiled shader actually run for these results.
- `out/vk-adm-*.txt` — the Vulkan-side gate outputs for the three runs above.

`build.sh` shows the exact tool lines used.

## Reproducibility chain (each link verified, 2026-11-06)

1. `shaders/integer_adm.comp` -> `build/integer_adm.spv`: re-running glslang
   16.6.0 reproduces the committed SPIR-V **byte-for-byte** (sha256
   `e71b25b57d448dfc…`).
2. `src/vk_adm.c` + that `.spv` -> `out/*.txt`: rebuilding the host from the
   committed source and re-running all three configurations reproduced every
   committed output file **byte-for-byte**, headers included — the outputs in
   `out/` are not stale artifacts of some other tree.
3. anchor build -> `refs/*.txt`: rebuilding the CPU library from
   `af5ec5c9` and re-running `cpu_ref` gives files byte-identical to the
   committed references.
4. `refs` vs `out` -> `compare.py` exit 0 (all three configurations), and
   `compare.py --selftest` exit 0 proves the comparator itself is not vacuous.

So the whole gate can be re-derived from committed sources, and the committed
evidence can be checked against them without any GPU at all (step 4) or with one
(steps 1-3).

## Output contract

Both tools write the same diffable format; payload values are
`printf("%a", (double)value)` so every bit of the float/double is recoverable:

```
# tool=cpu-ref libvmaf=v1.0.0-rc.3-660-gaf5ec5c94 fixture=576x324_48f frames=48 w=576 h=324
# fields: frame s0num s0den s1num s1den s2num s2den s3num s3den num den adm2
frame=3 s0num=0x1.ec5cd8p+4 s0den=0x1.f064p+4 ... adm2=0x1.f770df...p-1 | s0num=30.7726669 ...
```

One line per frame; the decimal mirror after `|` is for humans and is ignored
by the comparator. `sNnum`/`sNden` are the per-scale float `num_scale`/
`den_scale`; `num`/`den` are the frame totals after the CPU `numden_limit`
clamp; `adm2 = den == 0 ? 1 : num/den` — all with the CPU's own widths and
rounding points.

Build/run (mingw64 gcc 16.2, glslang 16.6 standalone; see `build.sh`):

```
glslang -V --target-env vulkan1.3 shaders/integer_adm.comp -o build/integer_adm.spv
gcc -O2 -ffp-contract=off -I <Vulkan-Headers> src/vk_adm.c -o build/vk_adm.exe
build/vk_adm.exe --ref R.yuv --dis D.yuv --w 576 --h 324 --frames 48 \
    --device-id 0x2c02 --spv build/integer_adm.spv --out out/vk-adm-576x324-48f.txt
python3 -I tools/compare.py --ref refs/cpu-adm-576x324-48f.txt \
    --test out/vk-adm-576x324-48f.txt
```

`-ffp-contract=off` on the host is load-bearing: it makes the host reference
math a true two-rounding mul+add, matching the CPU's `-O2` default.

## The six bit-exactness fixes vs the removed-backend snapshot

Each was found by binary-searching intermediate values against the CPU source
(VMAFx master `core/src/feature/integer_adm_kernels.h` +
`integer_adm.c` + `adm_angle_flag.h`), not by tolerance tuning. The first
three were visible as wrong values; the last three only as 1-2 float ULP
after the accumulators already matched. Line numbers below are at
`af5ec5c9`.

1. **int64 subgroup reduction** — the snapshot used `subgroupAdd(int64_t)`,
   the NVIDIA lowering defect the fork itself documents in ADR-0269
   ("driver bug in NVIDIA's int64 emulation of `subgroupAdd(int64_t)`",
   ~1e14-scale sign flips) and ADR-0264. Replaced with a shared[256] serial
   sum by thread 0 (addition is associative; order-free).
2. **scale-0 int16 truncation points** — CPU keeps scale-0 dwt tmp, bands,
   `csf_a`, `csf_f` in `int16_t` (`integer_adm.h:86-89`; stores
   `kernels.h:1351-1356`, `:1377-1383`, `:486-490`, `:316-318`); the shader
   now wraps all of them (`wrap16`). **One deliberate exception:** the
   scale-0 CM threshold own-term. The shader keeps the Netflix-style
   `int16_t` narrowing of that tap, because that is what the removed
   backend was built against; VMAFx master removed the narrowing
   (ADR-1402, `kernels.h:816` — the wrap makes an oversized tap negative,
   which then *adds* contrast instead of masking it). The two CPU
   references are bit-identical on both gate fixtures, so the gate does
   not distinguish them, but an in-tree port must follow VMAFx and drop
   the wrap.
3. **scale-0 diagonal rounding constant** — CPU `adm_csf_shiftsadd = {16384,
   16384, 65535}` (`kernels.h:439`, applied `:487`): the diagonal band's
   rounding add is 65535, **not** `1 << 16`. The snapshot used
   `1 << (shift-1)`.
4. **angle test in double** — CPU's `(float)ot_dp / 4096.0` promotes to a
   double division (double literal `4096.0`), so lhs/rhs of the cos^2(1deg)
   test are double products (`adm_angle_flag.h:85-91`, the frozen
   expression shared by CPU/CUDA/HIP; call sites `kernels.h:312-314` and
   `:407-409`). The snapshot's `precise float` test flips `angle_flag` on
   boundary pixels. Requires `shaderFloat64` (both local GPUs expose it).
   VMAFx also ships `adm_angle_flag_i64()` — a bit-identical fp64-free
   reformulation (`adm_angle_flag.h:219-262`, derivation in
   `docs/research/2030`) — which a shader could port to drop the
   `shaderFloat64` requirement for drivers without full fp64.
5. **`1u << 31` assigned to int32_t wraps to -2147483648**
   (`kernels.h:520-528`; documented deliberately-preserved arithmetic,
   ADR-0155 / Netflix issue #955, comment `:507-519`) — so for scales 1..3
   the CPU's real expressions are `csf_f = (143165577*|csf_a| - 2^31) >> 32`
   (`:589-591`) and the CM threshold own-term
   `(I4_ONE_BY_15*|csf_a| - 2^31) >> 32` (`:847-848`) — **minus**, not
   plus. The removed Vulkan snapshot used `+ 2^31`, shifting nearly every
   threshold value by 1 and the CM numerator by ~1e-5 relative. Worth
   stating plainly, since it bounds the claim: the CUDA, HIP and SYCL
   twins already encode the minus correctly (`adm_cm.cu:268` uses
   `INT32_MIN`; `integer_adm_sycl.cpp:129` uses `-(int64_t{1} << 31)`);
   this defect was specific to the removed **Vulkan** shader. We have not
   re-run the old Vulkan gate, so it remains an *inference* (not a
   measurement) that this off-by-1e-5 numerator bias is what kept the
   removed backend's ADM gate from ever reaching the places=4 its CUDA /
   SYCL siblings reported (ADR-0178: 1/48 mismatch on `adm_scale2`).
6. **conclude details** (host): `numden_limit` uses the base frame `w*h`
   (`integer_adm.c:906-909`; the snapshot host used the last scale's half
   dims); s>0 CM uses CPU's `(float)(accum / final_shift)` float division
   (`kernels.h:1228-1235`), not double; `powf_add` uses CPU's double
   product `(float)(area * (double)nw)` (`:604`); the per-band sums are
   left-associated `((h + v) + d)` exactly as CPU writes them
   (`:1084`, `:1243`).

Also verified (and irrelevant to the gate but worth recording): the CPU's
band_h/band_v naming is swapped relative to the shader's slice order
(shader slice 1 = hi-of-lo = CPU `band_v`); all per-scale concluded sums are
symmetric in h/v (equal rfactors/shifts), so the naming difference cannot
affect `num`/`den`/`adm2`.

## CPU-side context

- The JSON reference (`vmaf.exe --feature adm=debug=true -n --json`) keeps only
  6 decimals — sufficient for sanity, not for a bit-exact gate; hence the `%a`
  contract format.
- ISA invariance of the anchor was measured, not assumed: on the anchor build,
  `cpu_ref --cpumask 0 / 8 / 24` (default AVX2+AVX512 / AVX2-only / scalar)
  produced bit-identical reference files on fixture A, and match the committed
  `refs/` byte-for-byte. (`--cpumask` semantics come from VMAFx
  `libvmaf.h:205-217`: the mask lists instruction sets to *disable*.)

## What this proves (for #3302)

The libvmaf-fast recipe premise holds on NVIDIA Windows for a full integer
feature: with (a) no int64 subgroup ops, (b) exact replication of every C
integer-width truncation, and (c) exact replication of the C floating-point
expression order (including double promotion and `int32_t` wrap quirks), a
Vulkan compute path reproduces the CPU `integer_adm` extractor bit-for-bit —
per-scale accumulators (int64, dumped by both sides) and concluded float
scores. 1-ULP tolerance was never used; equality is exact.

## Not done in this PoC

- No in-tree integration and no CI rows: per the #3302 decision this stays on
  the fork until the parity evidence is reviewed.
- AIM (`integer_aim`) is not implemented (it is a separate feature; `adm2` does
  not depend on it).
- No performance numbers: the runs happened while the GPUs were busy with an
  unrelated workload. A quiet-window measurement is the natural next step.
- Only NVIDIA Windows hardware was tested; the AMD/Intel lanes would need a
  real device each to make a cross-vendor claim.
