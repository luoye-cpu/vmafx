# VK PoC Phase 0 — device probe (VMAFx #3302)

Status: **PASS on RTX 5080 (Blackwell sm_120) + RTX 4060 Laptop (Ada sm_89), Windows, driver 621.257 / loader API 1.4.357** (2026-11-06).

Maintainer decision on #3302 (lusoris, 2026-11-05T18:06Z): fork PoC welcome; an
integer-parity proof (one integer feature bit-exact against the CPU path)
decides whether an in-tree proposal + new ADR superseding ADR-0726 goes ahead.
Results are to be posted in #3302 when ready.

## Layout

- `shaders/probe.comp` — one compute shader, op selected by push constant
- `src/vk_probe.c` — host; dynamic `vulkan-1.dll` + `vkGetInstanceProcAddr`
  (no SDK, no volk; needs only the Khronos Vulkan-Headers, MIT, on the
  include path — not vendored in this branch)
- `glslang` 16.6.0 standalone binary (not vendored here)
- `build/probe.spv` — `glslang -V --target-env vulkan1.3 shaders/probe.comp -o build/probe.spv`
- build host: `gcc -O2 -ffp-contract=off -I <Vulkan-Headers> src/vk_probe.c -o build/vk_probe.exe`
  (contract off so the host reference for op2 is a true two-rounding mul+add)
- reports: `reports/probe-report-{5080,4060}.txt`

## The four checks (and which historical failure each targets)

| op | check | result | targets |
|---|---|---|---|
| 0 | `umulExtended` u32×u32→u64 full product vs host golden | PASS ×2 | foundation of the 53-bit (VIF) / 128-bit (ADM) integer emulation |
| 1 | u64 add-reduce chain with explicit carries vs host golden | PASS ×2 | the int64 reduction layer (old backend's subgroupAdd(int64) was non-deterministic on NVIDIA; this probe uses per-thread carry chains, no subgroups) |
| 2 | fp32 contraction: `a*b+c` vs `precise(a*b+c)` vs host FMA/mul+add refs | PASS ×2 | research 0053 `vulkan-1-4-nvidia-fp-contraction-regression` / ADR-0264 (driver flipped contraction between API versions). **Measured: default path contracts (FMA, 3791/4096 finite cases); `precise` (SPIR-V NoContraction) is honored — 0 violations.** The libvmaf-fast recipe is viable on NVIDIA Windows. |
| 3 | dispatch sentinel (distinct value per invocation) | PASS ×2 | ADR-0176 errata (the removed backend's gate shipped a fake "ULP=0" baseline three times; a kernel that never ran must not read as passing) |

Edge bucketing: NaN payload canonicalization and **denormal inputs (DAZ — the
NVIDIA Windows driver flushes denormal inputs to zero; host does not)** are
recorded as edge behavior (165/4096 cases on the deterministic random stream),
not failures. DAZ matters later as an input assumption for bit-exact features.

## Phase 1 plan (executed in [`../phase1/`](../phase1/))

- Feature: **integer ADM** (the removed backend's `integer_adm.comp` is pure
  int64/int32 with two isolated `precise` angle-test blocks — closest to
  the CPU integer extractor contract). VIF would need the libvmaf-fast 53-bit
  ratio rework the snapshot does not contain.
- Gate: same-fixture CPU `integer_adm2` vs VK path, bit-for-bit (contract anchor
  = CPU reference, never another GPU backend).
- Reuse from the snapshot: 4-stage pipeline shape, div LUT push, conclude_* host
  functions shared with CPU; never reuse `vif_reduce.comp` /
  `integer_adm_reduce.comp` blindly (their `subgroupAdd(int64_t)` is the
  NVIDIA lowering defect — ADR-0269, and the manual-butterfly workaround
  that closed it: ADR-0264 Phase 3c / PR #512).
- Probe deliverable #1: vendorID/deviceID printout — already in this probe
  (selects by device identity; the public ADR-0176 errata, bug (2), is
  exactly this class: a silently wrong device was being benchmarked).
