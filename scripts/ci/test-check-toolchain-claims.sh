#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# scripts/ci/test-check-toolchain-claims.sh — test suite for PR-body toolchain
# claim gate (scripts/ci/check-toolchain-claims.sh) and host probe (scripts/dev/toolchain-probe.sh).
#
# Follows the test pattern of scripts/ci/test-validate-pr-body.sh:
#   - Positive: claim + probe saying absent -> pass (exit 0)
#   - Negative planted: claim without probe -> fail (exit 1)
#   - Negative planted: claim with probe saying found -> fail (exit 1)
#   - Boundary: no claim -> pass (exit 0)
#   - Boundary: probe rows are exempt, prose inside fences is checked
#   - Probe stub test: fake setvars.sh exporting PATH to stub icpx -> found
#   - Probe empty prefix test: empty prefix -> absent

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
gate="${repo_root}/scripts/ci/check-toolchain-claims.sh"
probe="${repo_root}/scripts/dev/toolchain-probe.sh"
# shellcheck source=scripts/dev/toolchain-probe-format.sh
. "${repo_root}/scripts/dev/toolchain-probe-format.sh"

if [ ! -x "${gate}" ]; then
  echo "test-check-toolchain-claims: gate script missing or non-executable: ${gate}" >&2
  exit 1
fi

if [ ! -x "${probe}" ]; then
  echo "test-check-toolchain-claims: probe script missing or non-executable: ${probe}" >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

pass_count=0
fail_count=0

expect_gate_exit() {
  local name="$1" expected="$2" body_file="$3" message="${4:-}" forbidden="${5:-}"
  local out exit_code=0

  out="$(PR_BODY="$(cat "${body_file}")" "${gate}" 2>&1)" || exit_code=$?
  if [[ "${exit_code}" -eq "${expected}" && "${out}" == *"${message}"* &&
    (-z "${forbidden}" || "${out}" != *"${forbidden}"*) ]]; then
    echo "PASS: ${name} via PR_BODY (exit=${exit_code})"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: ${name} via PR_BODY (got exit=${exit_code}, expected ${expected}${message:+ and message: ${message}})"
    echo "----- captured output -----"
    printf '%s\n' "${out}"
    echo "---------------------------"
    fail_count=$((fail_count + 1))
  fi

  # Also test stdin pipe
  local out_stdin exit_code_stdin=0
  out_stdin="$(env -u PR_BODY "${gate}" <"${body_file}" 2>&1)" || exit_code_stdin=$?
  if [[ "${exit_code_stdin}" -eq "${expected}" && "${out_stdin}" == *"${message}"* &&
    (-z "${forbidden}" || "${out_stdin}" != *"${forbidden}"*) ]]; then
    echo "PASS: ${name} via stdin (exit=${exit_code_stdin})"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: ${name} via stdin (got exit=${exit_code_stdin}, expected ${expected}${message:+ and message: ${message}})"
    echo "----- captured output -----"
    printf '%s\n' "${out_stdin}"
    echo "---------------------------"
    fail_count=$((fail_count + 1))
  fi
}

# ---------- Case 1: Negative planted — claim without probe -> fail (exit 1) ----------
cat >"${work}/case1_no_probe.md" <<'EOF'
## Summary

SYCL was not compiled: this host has no `icpx`.
EOF
expect_gate_exit "planted: claim without probe -> exit 1" 1 "${work}/case1_no_probe.md"

# ---------- Case 2: Negative planted — claim with probe saying found -> fail (exit 1) ----------
cat >"${work}/case2_probe_found.md" <<'EOF'
## Summary

SYCL was not compiled: this host has no `icpx`.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx found /opt/intel/oneapi/compiler/2026.0/bin/icpx Intel(R) oneAPI DPC++/C++ Compiler 2026.0
toolchain-probe: nvcc absent searched=PATH,/opt/cuda
```
EOF
expect_gate_exit "planted: claim with probe saying found -> exit 1" 1 "${work}/case2_probe_found.md"

# ---------- Case 3: Positive — claim + probe saying absent -> pass (exit 0) ----------
cat >"${work}/case3_probe_absent.md" <<'EOF'
## Summary

SYCL was not compiled: this host has no `icpx`.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx absent searched=/opt/intel/oneapi/setvars.sh
toolchain-probe: nvcc found /opt/cuda/bin/nvcc nvcc: NVIDIA (R) Cuda compiler driver
```
EOF
expect_gate_exit "positive: claim + probe saying absent -> exit 0" 0 "${work}/case3_probe_absent.md"

# ---------- Case 4: Boundary — no claim made -> pass (exit 0) ----------
cat >"${work}/case4_clean.md" <<'EOF'
## Summary

Refactor AVX2 feature extractors for improved throughput.
All unit and regression tests pass.
EOF
expect_gate_exit "boundary: clean body with no claim -> exit 0" 0 "${work}/case4_clean.md"

# ---------- Case 5: Boundary — probe rows alone -> pass (exit 0) ----------
cat >"${work}/case5_probe_only.md" <<'EOF'
## Summary

Update build flags.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx absent searched=/opt/intel/oneapi/setvars.sh
toolchain-probe: nvcc found /opt/cuda/bin/nvcc nvcc: NVIDIA (R) Cuda compiler driver
```
EOF
expect_gate_exit "boundary: probe rows alone -> exit 0" 0 "${work}/case5_probe_only.md"

# ---------- Case 6: CUDA claim without probe vs with probe ----------
cat >"${work}/case6_cuda_no_probe.md" <<'EOF'
## Summary
CUDA was not compiled locally: no nvcc found.
EOF
expect_gate_exit "cuda claim without probe -> exit 1" 1 "${work}/case6_cuda_no_probe.md"

cat >"${work}/case6_cuda_absent.md" <<'EOF'
## Summary
CUDA was not compiled locally: no nvcc found.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc absent searched=PATH,/opt/cuda,/usr/local/cuda
```
EOF
expect_gate_exit "cuda claim with nvcc absent -> exit 0" 0 "${work}/case6_cuda_absent.md"

cat >"${work}/case6_cuda_found.md" <<'EOF'
## Summary
CUDA was not compiled locally: no nvcc found.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc found /opt/cuda/bin/nvcc nvcc: NVIDIA (R) Cuda compiler driver
```
EOF
expect_gate_exit "cuda claim with nvcc found -> exit 1" 1 "${work}/case6_cuda_found.md"

# ---------- Case 9: build-configuration "not compiled" is no host claim ----------
cat >"${work}/case9_golden.md" <<'EOF'
## Summary
Netflix golden gate: the golden build has no CUDA backend: the changed `.c` files are not compiled.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc found /opt/cuda/bin/nvcc nvcc: NVIDIA (R) Cuda compiler driver
```
EOF
expect_gate_exit "boundary: golden-build not compiled + nvcc found -> exit 0" 0 "${work}/case9_golden.md"

cat >"${work}/case9_golden_no_probe.md" <<'EOF'
## Summary
The golden build has no CUDA backend: the changed `.c` files are not compiled.
EOF
expect_gate_exit "negative: not compiled without probe -> exit 1" 1 "${work}/case9_golden_no_probe.md"

# ---------- Case 7: ROCm / HIP claim with hipcc found vs absent ----------
cat >"${work}/case7_hip_absent.md" <<'EOF'
## Summary
HIP backend was not compiled: hipcc is missing.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: hipcc absent searched=PATH,/opt/rocm
```
EOF
expect_gate_exit "hip claim with hipcc absent -> exit 0" 0 "${work}/case7_hip_absent.md"

cat >"${work}/case7_hip_found.md" <<'EOF'
## Summary
HIP backend was not compiled: hipcc is missing.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: hipcc found /opt/rocm/bin/hipcc HIP version: 7.2.0
```
EOF
expect_gate_exit "hip claim with hipcc found -> exit 1" 1 "${work}/case7_hip_found.md"

# ---------- Case 8: Vulkan glslc claim with glslc found vs absent ----------
cat >"${work}/case8_glslc_absent.md" <<'EOF'
## Summary
Vulkan was not compiled: no glslc.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: glslc absent searched=PATH
```
EOF
expect_gate_exit "glslc claim with glslc absent -> exit 0" 0 "${work}/case8_glslc_absent.md"

cat >"${work}/case8_glslc_found.md" <<'EOF'
## Summary
Vulkan was not compiled: no glslc.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: glslc found /usr/bin/glslc 2026.1
```
EOF
expect_gate_exit "glslc claim with glslc found -> exit 1" 1 "${work}/case8_glslc_found.md"

# ---------- Case 9: toolchain-probe.sh stub dir test (fake setvars.sh exporting PATH to stub icpx) ----------
stub_dir="${work}/oneAPI SDK"
mkdir -p "${stub_dir}/bin"
cat >"${stub_dir}/bin/icpx" <<'EOF'
#!/bin/sh
echo "Intel(R) oneAPI DPC++/C++ Compiler 2026.9.9 (stub)"
EOF
chmod +x "${stub_dir}/bin/icpx"

cat >"${stub_dir}/setvars.sh" <<EOF
export PATH="${stub_dir}/bin:\$PATH"
EOF

probe_stub_out="$("${probe}" --prefix "${stub_dir}" 2>&1)" || {
  echo "FAIL: probe with stub dir returned non-zero exit"
  fail_count=$((fail_count + 1))
}

if printf '%s\n' "${probe_stub_out}" | grep -qF "toolchain-probe: icpx found $(probe_encode_field "${work}")/oneAPI%20SDK/bin/icpx "; then
  echo "PASS: probe with stub setvars.sh reported icpx found"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: probe with stub setvars.sh did not report icpx found"
  echo "----- captured output -----"
  printf '%s\n' "${probe_stub_out}"
  echo "---------------------------"
  fail_count=$((fail_count + 1))
fi

# ---------- Case 10: toolchain-probe.sh empty prefix test -> absent ----------
empty_dir="${work}/empty_prefix"
mkdir -p "${empty_dir}"

probe_empty_out="$("${probe}" --prefix "${empty_dir}" 2>&1)" || {
  echo "FAIL: probe with empty prefix returned non-zero exit"
  fail_count=$((fail_count + 1))
}

if [[ "${probe_empty_out}" == *"$(probe_absent_row icpx "${empty_dir}")"* ]]; then
  echo "PASS: probe with empty prefix reported icpx absent"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: probe with empty prefix did not report icpx absent"
  echo "----- captured output -----"
  printf '%s\n' "${probe_empty_out}"
  echo "---------------------------"
  fail_count=$((fail_count + 1))
fi

# ---------- Case 11: toolchain-probe.sh usage error -> exit 2 ----------
probe_usage_exit=0
"${probe}" --invalid-arg >/dev/null 2>&1 || probe_usage_exit=$?
if [ "${probe_usage_exit}" -eq 2 ]; then
  echo "PASS: probe with invalid argument returned exit 2"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: probe with invalid argument returned exit ${probe_usage_exit} (expected 2)"
  fail_count=$((fail_count + 1))
fi

# ---------- Case 13: Planted negative — claim with header-only probe -> fail (exit 1) ----------
cat >"${work}/case13_header_only_probe.md" <<'EOF'
## Summary

SYCL was not compiled: this host has no `icpx`.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
```
EOF
expect_gate_exit "planted: claim with header-only probe -> exit 1" 1 "${work}/case13_header_only_probe.md"

# ---------- Case 14: A non-matching fence marker leaves scope open ----------
cat >"${work}/case14_inner_fence.md" <<'EOF'
## Summary

Probe output containing inner fence markers:

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx found /opt/intel/oneapi/bin/icpx 2026.0
~~~
toolchain-probe: nvcc absent searched=PATH
No nvcc on this host
```
EOF
expect_gate_exit "different fence marker keeps subsequent probe result in scope" 0 "${work}/case14_inner_fence.md"

# ---------- Case 15: A shorter fence marker leaves scope open ----------
cat >"${work}/case15_fence_length.md" <<'EOF'
## Summary

Probe block opened with 4 backticks containing 3-backtick line:

````text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx found /opt/intel/oneapi/bin/icpx 2026.0
```
toolchain-probe: nvcc absent searched=PATH
No nvcc on this host
````
EOF
expect_gate_exit "shorter fence marker keeps subsequent probe result in scope" 0 "${work}/case15_fence_length.md"

# ---------- Case 16: Word boundary — shipping does not match hip with hipcc found -> exit 0 ----------
cat >"${work}/case16_shipping_no_false_contradiction.md" <<'EOF'
## Summary

no nvcc found; see the shipping notes

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc absent searched=PATH,/opt/cuda
toolchain-probe: hipcc found /opt/rocm/bin/hipcc 7.2.0
```
EOF
expect_gate_exit "word boundary: shipping does not match hip with hipcc found -> exit 0" 0 "${work}/case16_shipping_no_false_contradiction.md"

# ---------- Case 17: Planted — claim naming nvcc and hipcc with probe for nvcc only -> nonzero exit naming hipcc ----------
cat >"${work}/case17_partial_probe_missing_hipcc.md" <<'EOF'
## Summary

CUDA and HIP were not compiled locally: no nvcc or hipcc found.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc absent searched=PATH,/opt/cuda
```
EOF
expect_gate_exit "planted: claim naming nvcc and hipcc with probe for nvcc only -> exit 1 naming hipcc" 1 "${work}/case17_partial_probe_missing_hipcc.md"

partial_rc=0
out_partial="$(env -u PR_BODY "${gate}" <"${work}/case17_partial_probe_missing_hipcc.md" 2>&1)" || partial_rc=$?
if [[ "${partial_rc}" -eq 1 && "${out_partial}" == *"missing probe results for claimed toolchains: hipcc"* ]]; then
  echo "PASS: partial probe failure message names missing toolchain hipcc"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: partial probe did not exit 1 naming missing toolchain hipcc (rc=${partial_rc})"
  echo "----- captured output -----"
  printf '%s\n' "${out_partial}"
  echo "---------------------------"
  fail_count=$((fail_count + 1))
fi

# ---------- Case 18: Positive — claim naming nvcc and hipcc with full probe -> exit 0 ----------
cat >"${work}/case18_full_probe_nvcc_hipcc.md" <<'EOF'
## Summary

CUDA and HIP were not compiled locally: no nvcc or hipcc found.

```text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc absent searched=PATH,/opt/cuda
toolchain-probe: hipcc absent searched=PATH,/opt/rocm
```
EOF
expect_gate_exit "positive: claim naming nvcc and hipcc with full probe -> exit 0" 0 "${work}/case18_full_probe_nvcc_hipcc.md"

expect_body() {
  local name="$1" expected="$2" body="$3"
  printf '%s' "${body}" >"${work}/inline-body.md"
  expect_gate_exit "${name}" "${expected}" "${work}/inline-body.md" "${4:-}" "${5:-}"
}

# Only strict probe lines can be exempt, even in a fence with a header.
header='toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z'
absent_nvcc='toolchain-probe: nvcc absent searched=PATH'
found_nvcc='toolchain-probe: nvcc found /opt/cuda/bin/nvcc 13.0'
fence='```'
expect_body "a probe mention does not exempt a fence" 1 "${fence}text
# run toolchain-probe
no nvcc
${fence}"
expect_body "a probe prefix does not exempt prose" 1 'toolchain-probe could not run because no nvcc'
expect_body "a real probe fence still checks prose" 1 "${fence}text
${header}
${found_nvcc}
no nvcc
${fence}"
expect_body "a probe result alone does not exempt a fence" 1 "${fence}text
toolchain-probe: icpx found /opt/intel/oneapi/bin/icpx no nvcc
${fence}"
expect_body "an ordinary fence still checks claims" 1 "${fence}text
no nvcc
${fence}"
expect_body "a blockquote still checks claims" 1 '> no nvcc'

empty_env_rc=0
PR_BODY="" "${gate}" >/dev/null 2>&1 || empty_env_rc=$?
if [ "${empty_env_rc}" -eq 0 ]; then
  echo "PASS: explicitly empty PR_BODY passes (exit=0)"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: explicitly empty PR_BODY failed (got exit=${empty_env_rc}, expected 0)"
  fail_count=$((fail_count + 1))
fi

empty_pipe_rc=0
true | env -u PR_BODY "${gate}" >/dev/null 2>&1 || empty_pipe_rc=$?
if [ "${empty_pipe_rc}" -eq 2 ]; then
  echo "PASS: empty stdin pipe fails closed (exit=2)"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: empty stdin pipe did not exit 2 (got exit=${empty_pipe_rc})"
  fail_count=$((fail_count + 1))
fi

# ---------- Round-2 isolating regression cases (F1-F5, S1-S9) ----------
expect_body "S1: CI runner scope with nvcc found is not contradicted" 0 "Hosted Ubuntu CI runner has no nvcc

${fence}text
${header}
${found_nvcc}
${fence}"
expect_body "S1b: CI runner scope without probe still fails" 1 'Hosted Ubuntu CI runner has no nvcc'
expect_body "S2: backend alias CUDA counts as absence word" 1 'No CUDA on this host'
expect_body "S3: not available phrasing counts as absence claim" 1 'nvcc is not available'
expect_body "S4: lacks phrasing counts as absence claim" 1 'host lacks nvcc'
expect_body "S4b: lacking phrasing counts as absence claim" 1 'host is lacking hipcc'
expect_body "S5: did not compile locally requires probe" 1 'did not compile CUDA locally'
expect_body "S5b: did not build locally requires probe" 1 'did not build HIP locally'
expect_body "S6: tools taken only from claim clause across semicolon" 0 "icx build passed; no nvcc on this host

${fence}text
${header}
${absent_nvcc}
toolchain-probe: icx found /opt/intel/oneapi/bin/icx 2026.0
${fence}"
expect_body "S6b: tools taken only from claim clause across sentence end" 0 "icx build passed. No nvcc on this host

${fence}text
${header}
${absent_nvcc}
toolchain-probe: icx found /opt/intel/oneapi/bin/icx 2026.0
${fence}"
expect_body "S7: hyphenated tidy-metal does not match metal" 0 'tidy-metal is missing'
expect_body "S8: unfenced probe-shaped line without header is checked as prose" 1 \
  'toolchain-probe: icpx found /opt/intel/oneapi/bin/icpx - no nvcc on this host'
expect_body "S9: trailing prose on absent row is rejected even after header" 1 "${fence}text
${header}
toolchain-probe: icpx absent searched=PATH no nvcc
${fence}"
expect_body "header missing date= with valid absent row fails" 1 "No nvcc on this host

${fence}text
toolchain-probe v1 host=test-box os=Linux
${absent_nvcc}
${fence}"
expect_body "Vulkan family: explicit glslangValidator overrides vulkan alias when absent" 0 \
  "Vulkan (glslangValidator) was not compiled locally: no glslangValidator

${fence}text
${header}
toolchain-probe: glslc found /usr/bin/glslc 2026.1
toolchain-probe: glslangValidator absent searched=PATH
${fence}"
expect_body "Vulkan family: explicit glslangValidator contradicted when found" 1 \
  "Vulkan (glslangValidator) was not compiled locally: no glslangValidator

${fence}text
${header}
toolchain-probe: glslc found /usr/bin/glslc 2026.1
toolchain-probe: glslangValidator found /usr/bin/glslangValidator 15.0
${fence}"
expect_body "per-family precedence checks hipcc even when nvcc is explicit" 1 \
  "CUDA and HIP were not compiled locally (no nvcc)

${fence}text
${header}
${absent_nvcc}
toolchain-probe: hipcc found /opt/rocm/bin/hipcc 7.2.0
${fence}"
expect_body "F1: same-line backtick span is not a fence opener" 0 "${fence}nvcc${fence}
${fence}
${header}

${absent_nvcc}
${fence}
No nvcc on this host"
expect_body "F1b: same-line backtick span carrying claim is checked" 1 "${fence}no nvcc${fence}"
expect_body "MinGW detail rows inside header block are accepted" 0 "No nvcc on this host

${fence}text
${header}
${absent_nvcc}
toolchain-probe: x86_64-w64-mingw32-gcc found /usr/bin/x86_64-w64-mingw32-gcc 13-win32
toolchain-probe: x86_64-w64-mingw32-gcc dumpversion 13-win32
toolchain-probe: x86_64-w64-mingw32-gcc thread-model win32
toolchain-probe: x86_64-w64-mingw32-gcc runtime libstdc++-6.dll=/usr/lib/gcc/x86_64-w64-mingw32/13-win32/libstdc++-6.dll
toolchain-probe: x86_64-w64-mingw32-gcc runtime libgcc_s_seh-1.dll=/usr/lib/gcc/x86_64-w64-mingw32/13-win32/libgcc_s_seh-1.dll
toolchain-probe: x86_64-w64-mingw32-gcc runtime libwinpthread-1.dll unresolved (evidence only; not every flavour ships it)
toolchain-probe: x86_64-w64-mingw32-gcc pairing ok thread-model win32, libstdc++-6.dll imports no libwinpthread
${fence}"

expect_body "bare header and absent row lack required fields" 1 'No nvcc
toolchain-probe v1
toolchain-probe: nvcc absent'
expect_body "a result before its header cannot cover a claim" 1 "No nvcc
${absent_nvcc}
${header}"
expect_body "a blank line separates raw evidence" 1 "No nvcc
${header}

${absent_nvcc}"
expect_body "a fence boundary separates evidence" 1 "No nvcc
${fence}text
${header}
${fence}
${fence}text
${absent_nvcc}
${fence}"
expect_body "a found row needs a path" 1 "CUDA was not compiled
${header}
toolchain-probe: nvcc found"
expect_body "an absent row needs searched directories" 1 "No nvcc
${header}
toolchain-probe: nvcc absent PATH"
expect_body "raw evidence may follow a header with no intervening blank line" 0 "No nvcc
${header}
${absent_nvcc}"

expect_body "Metal absence needs evidence" 1 'no metal'
expect_body "glslangValidator absence needs evidence" 1 'no glslangValidator'
expect_body "glslc missing needs evidence" 1 'glslc is missing'
expect_body "nvcc not found needs evidence" 1 'nvcc was not found'
expect_body "MinGW absence needs evidence" 1 'no x86_64-w64-mingw32-gcc'
expect_body "without a tool in a build configuration passes" 0 'The CPU-only job builds without nvcc'
expect_body "without a tool here needs evidence" 1 'The build runs without nvcc here'
expect_body "without a tool on this host needs evidence" 1 'The build runs without nvcc on this host'
expect_body "without a tool locally needs evidence" 1 'The build runs without nvcc locally'
expect_body "CUDA skipping compilation needs evidence" 1 'CUDA compilation was skipped'
expect_body "CUDA not built needs evidence" 1 'CUDA was not built'
expect_body "a document is not a toolchain compilation claim" 0 'This document is not compiled into the binary'

expect_body "a backend alias is not a probe result name" 1 "No nvcc

${fence}text
${header}
toolchain-probe: cuda absent searched=PATH
${fence}"
expect_body "explicit icpx takes precedence over the SYCL alias" 0 "SYCL was not compiled because there is no icpx

${fence}text
${header}
toolchain-probe: icpx absent searched=PATH,/opt/intel/oneapi
toolchain-probe: icx found /opt/intel/oneapi/bin/icx 2026.0
${fence}"
expect_body "backend-only SYCL needs icpx evidence" 1 "SYCL was not compiled locally
${header}
toolchain-probe: icx absent searched=PATH"
expect_body "the Linux Metal row covers a host compilation claim" 0 "Metal was not compiled locally

${fence}text
${header}
toolchain-probe: icpx absent searched=PATH
toolchain-probe: icx absent searched=PATH
${absent_nvcc}
toolchain-probe: hipcc absent searched=PATH
toolchain-probe: glslc absent searched=PATH
toolchain-probe: glslangValidator absent searched=PATH
toolchain-probe: metal absent searched=none os=Linux
toolchain-probe: x86_64-w64-mingw32-gcc absent searched=PATH
${fence}"

# ---------- Change-scope 'no <tool>' prose vs absence claims (PR 3298 review finding) ----------
tick="${fence:0:1}"
expect_body "scope pass: This PR has no SYCL changes." 0 'This PR has no SYCL changes.'
expect_body "scope pass: Touches no CUDA code." 0 'Touches no CUDA code.'
expect_body "scope pass: no nvcc flags changed" 0 'no nvcc flags changed'
expect_body "scope pass: No Vulkan files changed." 0 'No Vulkan files changed.'
expect_body "scope pass: changes no HIP code" 0 'changes no HIP code'
expect_body "scope pass: no ${tick}nvcc${tick} flags changed" 0 "no ${tick}nvcc${tick} flags changed"
expect_body "scope pass: There is no doubt SYCL is available." 0 'There is no doubt SYCL is available.'

expect_body "scope fail: SYCL is not available on the runner." 1 'SYCL is not available on the runner.'
expect_body "scope fail: There is no nvcc on this host." 1 'There is no nvcc on this host.'
expect_body "scope fail: no nvcc found" 1 'no nvcc found'
expect_body "scope fail: No CUDA toolkit here." 1 'No CUDA toolkit here.'
expect_body "scope fail: bare no CUDA code stays fail-closed" 1 'no CUDA code'
expect_body "scope fail: scoped SYCL + unscoped no nvcc without probe" 1 \
  'This PR has no SYCL changes and there is no nvcc on this host.'
expect_body "scope fail: scoped SYCL + built without nvcc here" 1 \
  'no SYCL changes, built without nvcc here'

expect_body "scope positive: scoped SYCL + unscoped no nvcc with nvcc absent probe" 0 \
  "This PR has no SYCL changes and there is no nvcc on this host.

${fence}text
toolchain-probe v1 host=test-box os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: nvcc absent searched=PATH,/opt/cuda,/usr/local/cuda
${fence}"

scope_32=''
for ((scope_i = 0; scope_i < 32; scope_i++)); do
  scope_32+="${scope_32:+, }no SYCL changes"
done
expect_body "scope boundary: 32 scoped matches in one clause pass" 0 "${scope_32}"
expect_body "scope boundary: 33 scoped matches exceed cap and fail closed" 1 \
  "${scope_32}, no SYCL changes"

expect_body "unscoped claim first at scope cap" 1 "no nvcc here, ${scope_32}, no SYCL changes
${header}
toolchain-probe: icpx absent searched=PATH" "missing probe results for claimed toolchains: nvcc"

# Round 3: evidence belongs to one invocation and host scope to one absence.
expect_body "available changed" 1 'Having no nvcc available changed our build plan.'
expect_body "availability before changed" 1 'Having no nvcc installed changed our build plan.'
expect_body "two change objects" 0 'This PR has no CUDA build flag changes.'
expect_body "compiler with two change objects" 0 'no nvcc build flag changes were made'
expect_body "three change objects" 0 'no nvcc build config files changed'
expect_body "four change objects" 1 'no nvcc build config source files changed'
expect_body "same tool scoped and absent" 1 'no CUDA changes; no nvcc on this host'
expect_body "same tool scoped and absent in one clause" 1 'no CUDA changes and no nvcc on this host'
expect_body "header rejects invalid date" 1 "No nvcc on this host.
${header/date=*/date=banana}
${absent_nvcc}"
expect_body "header rejects invalid OS" 1 "No nvcc on this host.
${header/os=Linux/os=banana}
${absent_nvcc}"
expect_body "header rejects invalid hostname" 1 "No nvcc on this host.
${header/test-box/test@box}
${absent_nvcc}"
expect_body "real producer header and rows" 0 "No icpx on this host.
${probe_empty_out}"

header_b="${header/test-box/host-b}"
absent_hipcc='toolchain-probe: hipcc absent searched=PATH'
found_hipcc='toolchain-probe: hipcc found /opt/rocm/bin/hipcc'
two_tools='No nvcc or hipcc on this host.'
expect_body "split blocks lack joint coverage" 1 "${two_tools}
${header}
${absent_nvcc}
${header}
${absent_hipcc}" "missing probe results for claimed toolchains: hipcc"
expect_body "coordinated unbound absences with multiple hosts" 1 "No nvcc and no hipcc on this host.
${header}
${absent_nvcc}
${header_b}
${absent_hipcc}" "name the probed host"
expect_body "closest block determines missing results" 1 "No nvcc or hipcc or icpx on this host.
${header}
${absent_nvcc}
${header}
${absent_hipcc}
toolchain-probe: icpx absent searched=PATH" "missing probe results for claimed toolchains: nvcc"
expect_body "invalid header ends previous block" 1 "${two_tools}
${header}
${absent_nvcc}
${header_b/date=*/date=banana}
${absent_hipcc}" "missing probe results for claimed toolchains: hipcc"
expect_body "single block covers both tools" 0 "${two_tools}
${header}
${absent_nvcc}
${absent_hipcc}"
expect_body "second covering block agrees" 0 "No nvcc on this host.
${header}
${found_nvcc}
${header}
${absent_nvcc}"
expect_body "every covering block contradicts" 1 "No nvcc on this host.
${header}
${found_nvcc}
${header}
${found_nvcc}" "claims 'nvcc' is missing"
expect_body "mixed result contradicts a host claim" 1 "${two_tools}
${header}
${absent_nvcc}
${found_hipcc}"

spaced_prefix="${work}/oneAPI SDK%20"$'\t\n\v\f\r'end
spaced_out="$(ONEAPI_PREFIX="${spaced_prefix}" "${probe}")"
expect_body "encoded producer prefix covers icpx" 0 "No icpx on this host.
${spaced_out}"
if [[ "${spaced_out}" == *"/oneAPI%20SDK%2520%09%0A%0B%0C%0Dend"* ]]; then
  echo "PASS: producer encodes percent and six whitespace characters"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: producer encodes percent and six whitespace characters"
  fail_count=$((fail_count + 1))
fi

expect_body "local verification of CI subject" 0 "I verified locally that the CI runner has no nvcc.
${header}
${found_nvcc}"
expect_body "missing here is a host claim" 1 "nvcc is missing here
${header}
${found_nvcc}"
expect_body "four-word window includes locally" 1 "CI notes: locally we still see no nvcc
${header}
${found_nvcc}"
expect_body "five-word window excludes locally" 0 "CI notes: locally we can still see no nvcc
${header}
${found_nvcc}"
expect_body "machine subject binds lacks" 1 "In the CI notes my machine lacks nvcc.
${header}
${found_nvcc}"
mixed_body="the CI image has no nvcc, but this host has no hipcc
${header}
${found_nvcc}
${found_hipcc}"
expect_body "mixed subject contradiction names only hipcc" 1 "${mixed_body}" "claims 'hipcc' is missing" "claims 'nvcc"

# Host binding, independent predicates, and format agreement.
header_a="${header/test-box/build-a}"
header_b="${header/test-box/build-b}"
blocks="${header_a}
${found_nvcc}
${header_b}
${absent_nvcc}"
only_a="${header_a}
${absent_nvcc}"
expect_body "unprobed named host" 1 "No nvcc on build-b.
${only_a}" "no toolchain-probe block for host 'build-b'"
expect_body "both named hosts need evidence" 1 "No nvcc on build-a and on build-b.
${only_a}" "no toolchain-probe block for host 'build-b'"
expect_body "named host added" 0 "No nvcc on build-b.
${only_a}
${header_b}
${absent_nvcc}"
expect_body "host is an unnamed qualifier" 1 "No nvcc on this host.
${only_a}
${header/test-box/host}
${absent_nvcc}" "name the probed host"
expect_body "explicit alphabetic host" 0 "No nvcc on host buildbox.
${blocks/build-b/buildbox}"
expect_body "quoted alphabetic host" 1 "No nvcc for ${tick}buildbox${tick}.
${only_a}" "no toolchain-probe block for host 'buildbox'"
expect_body "OS qualifier uses single host" 0 "No nvcc on Linux.
${only_a}"
expect_body "unprobed host subject" 1 "build-b has no nvcc.
${only_a}" "no toolchain-probe block for host 'build-b'"
expect_body "tool subject with hyphens" 0 "x86_64-w64-mingw32-gcc is missing.
${header_a}
toolchain-probe: x86_64-w64-mingw32-gcc absent searched=PATH"
expect_body "named host contradiction" 1 "No nvcc on BUILD-A.
${blocks}" "host=build-a, header line 2"
expect_body "named host agrees" 0 "No nvcc on build-a.
${blocks/"${found_nvcc}"/"${absent_nvcc}"}"
expect_body "named second host agrees" 0 "No nvcc on build-b.
${blocks}"
expect_body "named host missing results" 1 "No hipcc on build-b.
${header_a}
${absent_hipcc}
${header_b}" "missing probe results for claimed toolchains: hipcc (host=build-b, header line 4)"
expect_body "unbound host ambiguity" 1 "No nvcc on this host.
${blocks}" "name the probed host"
expect_body "hostname prefix cannot bind" 1 "No nvcc on build-a.lab.
${blocks}" "no toolchain-probe block for host 'build-a.lab'"
expect_body "literal dotted hostname" 0 "No nvcc on BUILD.B.
${blocks/build-b/build.b}"
expect_body "dot cannot bind another hostname" 1 "No nvcc on buildXb.
${blocks/build-b/build.b}" "name the probed host"
expect_body "named host checks all matching blocks" 1 "No nvcc on build-a.
${header_a}
${absent_nvcc}
${header_a}
${found_nvcc}" "host=build-a, header line 4"

icx_found='toolchain-probe: icx found /bin/icx'
icpx_absent='toolchain-probe: icpx absent searched=PATH'
subject_icx='I built with icx and this host lacks SYCL.'
expect_body "compiler outside absence object" 0 "${subject_icx}
${header}
${icpx_absent}
${icx_found}"
expect_body "backend object contradicted" 1 "${subject_icx}
${header}
toolchain-probe: icpx found /bin/icpx
${icx_found}" "claims 'icpx' is missing"
expect_body "explicit compiler object" 1 "This host has no SYCL compiler icx.
${header}
${icx_found}" "claims 'icx' is missing"
expect_body "parenthesized compiler object" 0 "No SYCL (icx).
${header}
toolchain-probe: icx absent searched=PATH"
expect_body "compiler outside status subject" 0 "I built with icx and SYCL is missing.
${header}
${icpx_absent}
${icx_found}"
expect_body "compiler outside compilation subject" 0 "I built with icx and SYCL was not compiled locally.
${header}
${icpx_absent}
${icx_found}"
sycl_ci='No SYCL on this host, but the CI runner lacks icx.'
expect_body "segment alias needs icpx" 1 "${sycl_ci}
${header}
${icx_found}" "missing probe results for claimed toolchains: icpx"
expect_body "segment alias covered" 0 "${sycl_ci}
${header}
${icx_found}
${icpx_absent}"
expect_body "compilation alias cannot borrow next compiler" 1 "Vulkan was not compiled locally: no glslangValidator.
${header}
toolchain-probe: glslc found /bin/glslc
toolchain-probe: glslangValidator absent searched=PATH" "claims 'glslc' is missing"
joins=(but while whereas although)
for ((i = 0; i < ${#joins[@]}; i++)); do
  expect_body "contrast boundary ${joins[i]}" 0 "CI has icx, ${joins[i]} no SYCL here.
${header}
${icpx_absent}"
done
expect_body "same clause segment alias" 1 "No SYCL here and the runner lacks icx.
${header}
${icx_found}" "missing probe results for claimed toolchains: icpx"
expect_body "explicit compiler in next subject" 1 "No SYCL here and icx is missing in the CI image.
${header}
${icx_found}" "missing probe results for claimed toolchains: icpx"
expect_body "compound compilation contradiction" 1 "CUDA was not compiled locally, but the CI image has no hipcc.
${header}
${found_nvcc}
${absent_hipcc}" "claims 'nvcc' is missing"
expect_body "compiler in host qualifier" 0 "No SYCL on this host (icx).
${header}
toolchain-probe: icx absent searched=PATH"
expect_body "qualifier before causal subject" 0 "No nvcc here because the CI runner lacks hipcc.
${header}
${absent_nvcc}
${found_hipcc}"
expect_body "compile then absence without contrast" 1 "CUDA was not compiled locally and the CI image has no hipcc.
${header}
${found_nvcc}
${absent_hipcc}" "claims 'nvcc' is missing"
expect_body "runner on this host absent" 0 "The runner on this host has no nvcc.
${header}
${absent_nvcc}"
expect_body "runner on this host contradicted" 1 "The runner on this host has no nvcc.
${header}
${found_nvcc}" "claims 'nvcc' is missing"
expect_body "CI and local absences agree" 0 "The CI image has no nvcc, but this host has no hipcc.
${header}
${found_nvcc}
${absent_hipcc}"
expect_body "Linux rejects generic metal absence" 1 "No metal locally.
${header}
toolchain-probe: metal absent searched=xcrun,PATH" "missing probe results for claimed toolchains: metal"
expect_body "Linux ignores metal found row" 1 "No metal locally.
${header}
toolchain-probe: metal found /tmp/metal no nvcc" "missing probe results for claimed toolchains: metal" "claimed toolchains: nvcc"
expect_body "Linux rejects mismatched metal OS" 1 "No metal locally.
${header}
toolchain-probe: metal absent searched=none os=FreeBSD" "missing probe results for claimed toolchains: metal"
expect_body "Linux accepts matching metal OS" 0 "No metal locally.
${header}
toolchain-probe: metal absent searched=none os=Linux"
expect_body "Darwin accepts generic metal absence" 0 "No metal locally.
${header/os=Linux/os=Darwin}
toolchain-probe: metal absent searched=xcrun,PATH"
expect_body "encoded found path with free version text" 1 "No icpx locally.
${probe_stub_out}" "claims 'icpx' is missing"
expect_body "invalid escape in found path" 1 "No icpx locally.
${header}
toolchain-probe: icpx found /bad%2G/path 13.0" "missing probe results for claimed toolchains: icpx"
expect_body "Darwin rejects Linux metal row" 1 "No metal locally.
${header/os=Linux/os=Darwin}
toolchain-probe: metal absent searched=none os=Linux" "missing probe results for claimed toolchains: metal"

mkdir "${work}/stub-uname"
cat >"${work}/stub-uname/uname" <<'EOF'
#!/bin/sh
case "$1" in
  -n) echo 'build@box' ;;
  -s) echo Linux ;;
  *) exit 2 ;;
esac
EOF
chmod +x "${work}/stub-uname/uname"
bad_host_rc=0
bad_host="$(
  unset TOOLCHAIN_PROBE_HOST
  PATH="${work}/stub-uname:${PATH}" "${probe}" --prefix "${empty_dir}" 2>&1
)" || bad_host_rc=$?
if [[ ${bad_host_rc} == 2 && "${bad_host}" == "toolchain-probe: hostname 'build@box' has characters outside [A-Za-z0-9._-]; set TOOLCHAIN_PROBE_HOST" ]]; then
  echo "PASS: invalid producer hostname"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: invalid producer hostname (rc=${bad_host_rc})"
  fail_count=$((fail_count + 1))
fi
invalid_override_rc=0
invalid_override="$(TOOLCHAIN_PROBE_HOST='bad host' "${probe}" --prefix "${empty_dir}" 2>&1)" || invalid_override_rc=$?
if [[ ${invalid_override_rc} != 2 || "${invalid_override}" != *"hostname 'bad host' has characters outside"* ]]; then
  echo "FAIL: invalid hostname override (rc=${invalid_override_rc})"
  fail_count=$((fail_count + 1))
fi
override_out="$(PATH="${work}/stub-uname:${PATH}" TOOLCHAIN_PROBE_HOST=build-box "${probe}" --prefix "${empty_dir}")"
expect_body "producer hostname override" 0 "No icpx on build-box.
${header_a}
toolchain-probe: icpx found /bin/icpx
${override_out}"

# Exercise the producer on an empty compiler PATH. Ignore vendor globs only
# inside this subshell so installed off-PATH SDKs cannot mask absent rows.
search_bin="${work}/search-bin"
mkdir "${search_bin}"
utilities=(bash date dirname tr)
for ((i = 0; i < ${#utilities[@]}; i++)); do
  utility="${utilities[i]}"
  ln -s "$(command -v "${utility}")" "${search_bin}/${utility}"
done
cp "${work}/stub-uname/uname" "${search_bin}/uname"
search_prefix="${work}/vendor SDK%20"$'\t\n\v\f\r'end
search_out="$(
  PATH="${search_bin}"
  GLOBIGNORE='*'
  TOOLCHAIN_PROBE_HOST=build-box
  ONEAPI_PREFIX="${search_prefix}" CUDA_PATH="${search_prefix}" ROCM_PATH="${search_prefix}"
  VULKAN_SDK="${search_prefix}" MINGW_CROSS_PREFIX="${search_prefix}"
  # shellcheck source=scripts/dev/toolchain-probe.sh
  . "${probe}"
)"
encoded_prefix="$(probe_encode_field "${work}")/vendor%20SDK%2520%09%0A%0B%0C%0Dend"
search_tools=(icpx icx nvcc hipcc glslc glslangValidator x86_64-w64-mingw32-gcc)
for ((i = 0; i < ${#search_tools[@]}; i++)); do
  name="${search_tools[i]}"
  case "${name}" in
    icpx | icx) searched="${encoded_prefix}" ;;
    nvcc) searched="PATH,${encoded_prefix},/opt/cuda,/usr/local/cuda" ;;
    hipcc) searched="PATH,${encoded_prefix},/opt/rocm" ;;
    glslc | glslangValidator) searched="PATH,${encoded_prefix}/bin" ;;
    *) searched="${encoded_prefix}/bin" ;;
  esac
  if [[ "${search_out}"$'\n' != *"toolchain-probe: ${name} absent searched=${searched}"$'\n'* ]]; then
    echo "FAIL: producer searched field for ${name}"
    fail_count=$((fail_count + 1))
  fi
  expect_body "producer searched field for ${name}" 0 "No ${name}.
${search_out}"
done

# Relocate only the built-in vendor root in this fixture. Candidate 51
# is executable, so an unbounded scan would report it found.
vendor_root="${work}/intel"
fixture_probe="${work}/toolchain-probe.sh"
sed 's@/opt/intel/@./intel/@g' "${probe}" >"${fixture_probe}"
cp "${repo_root}/scripts/dev/toolchain-probe-format.sh" "${work}/"
for ((i = 1; i <= 51; i++)); do
  printf -v version '%02d' "${i}"
  candidate="${vendor_root}/oneapi/compiler/${version}/bin/icpx"
  mkdir -p "${candidate%/*}"
  printf '#!/bin/sh\necho fixture\n' >"${candidate}"
done
chmod +x "${candidate}"
budget_out="$(
  cd "${work}"
  PATH="${search_bin}"
  GLOBIGNORE='/opt/*:/usr/local/*'
  TOOLCHAIN_PROBE_HOST=build-box ONEAPI_PREFIX='' ONEAPI_ROOT=''
  CUDA_PATH="${empty_dir}" ROCM_PATH="${empty_dir}" VULKAN_SDK="${empty_dir}" MINGW_CROSS_PREFIX="${empty_dir}"
  # shellcheck source=scripts/dev/toolchain-probe.sh
  . "${fixture_probe}"
)"
if [[ "${budget_out}" != *"toolchain-probe: icpx absent searched=PATH,./intel/oneapi*,budget-exhausted"$'\n'* ]]; then
  echo "FAIL: oneAPI fallback budget marker"
  fail_count=$((fail_count + 1))
fi
expect_body "oneAPI fallback budget" 0 "No icpx on build-box.
${budget_out}"

# A real runtime path can contain semicolons and a space. Only a recognized
# detail row is exempt from the prose clause cap. Pair it with an invalid row.
printf -v separators '%064d' 0
separators="${separators//0/;}"
runtime="${work}/runtime SDK/${separators}"
mingw="${work}/mingw/bin/x86_64-w64-mingw32"
mkdir -p "${runtime}" "${work}/mingw/bin"
touch "${runtime}/"{libstdc++-6.dll,libgcc_s_seh-1.dll,libwinpthread-1.dll}
cat >"${mingw}-gcc" <<'EOF'
#!/bin/sh
case "$1" in
  -print-file-name=*) printf '%s/%s\n' "$RUNTIME_FIXTURE" "${1#*=}" ;;
  -v) echo 'Thread model: win32' ;;
  *) echo 13 ;;
esac
EOF
printf '#!/bin/sh\nexit 0\n' >"${mingw}-objdump"
chmod +x "${mingw}-gcc" "${mingw}-objdump"
mingw_out="$(MINGW_CROSS_PREFIX="${work}/mingw" RUNTIME_FIXTURE="${runtime}" "${probe}" --prefix "${empty_dir}")"
detail="toolchain-probe: x86_64-w64-mingw32-gcc runtime libstdc++-6.dll=$(probe_encode_field "${work}")/runtime%20SDK/${separators}/libstdc++-6.dll"
if [[ "${mingw_out}" != *"${detail}"* ]]; then
  echo "FAIL: producer encoded runtime detail"
  fail_count=$((fail_count + 1))
fi
expect_body "MinGW detail exempt from prose" 0 "${mingw_out}"
expect_body "detail-looking prose checked" 1 "${header}
${detail} no nvcc" "clauses per line"
expect_body "clause cap plus one" 1 "a${separators//;/;a}" "clauses per line"
expect_body "clause cap inclusive" 0 "a${separators:1}"
printf -v padding '%04096d' 0
padding="${padding//0/$'\n'}"
expect_body "body cap plus one" 1 "${padding}end" "4096 lines"
expect_body "body cap inclusive" 0 "${padding:1}end"

syntax_rc=0
grep -En '((declare|local)[[:space:]]+-A|mapfile|readarray|,,}|\^\^}|declare[[:space:]]+-n|\|&|\[[[:space:]]*-[0-9]+[[:space:]]*\])' "${gate}" "${probe}" "${repo_root}/scripts/dev/toolchain-probe-format.sh" || syntax_rc=$?
if [ "${syntax_rc}" -eq 1 ]; then
  echo "PASS: Bash 3.2 checker syntax contract"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: Bash 3.2 checker syntax contract (grep rc=${syntax_rc})"
  fail_count=$((fail_count + 1))
fi

probe_os="$(uname -s)"
if [[ "${probe_os}" != Darwin ]]; then
  if [[ "${probe_empty_out}" == *"toolchain-probe: metal absent searched=none os=${probe_os}"* ]]; then
    echo "PASS: non-Darwin probe emits the Metal absence row"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: non-Darwin probe is missing the Metal absence row"
    fail_count=$((fail_count + 1))
  fi
fi

# ---------- Case 12: deliverables-check.sh integration test ----------
deliv_script="${repo_root}/scripts/ci/deliverables-check.sh"
deliv_exit=0
# The body fails other deliverables checks too, so the exit status alone does
# not prove the wiring: require the claims gate's own finding in the output.
deliv_out="$(PR_BODY="$(cat "${work}/case1_no_probe.md")" BASE_SHA="HEAD~1" HEAD_SHA="HEAD" \
  bash "${deliv_script}" 2>&1)" || deliv_exit=$?
if [ "${deliv_exit}" -eq 1 ] &&
  [[ "${deliv_out}" == *"check-toolchain-claims: line "* ]]; then
  echo "PASS: deliverables-check.sh propagates toolchain claim gate failure (exit=1)"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: deliverables-check.sh did not propagate failure (exit=${deliv_exit})"
  fail_count=$((fail_count + 1))
fi

# A stub is needed because the caller always supplies the child's PR_BODY.
mkdir "${work}/child-exit"
cp "${deliv_script}" "${repo_root}/scripts/ci/"{pr-body-input,pr-diff-base}.sh "${work}/child-exit/"
cat >"${work}/child-exit/check-toolchain-claims.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo 'fixture: toolchain claims child exit 2' >&2
exit 2
EOF
deliv_exit=0
deliv_out="$(PR_BODY='A valid description.' bash "${work}/child-exit/deliverables-check.sh" 2>&1)" ||
  deliv_exit=$?
if [[ "${deliv_exit}" -eq 2 && "${deliv_out}" == *"fixture: toolchain claims child exit 2"* ]]; then
  echo "PASS: deliverables-check.sh propagates the child usage error (exit=2)"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: deliverables-check.sh did not propagate the child usage error (exit=${deliv_exit})"
  fail_count=$((fail_count + 1))
fi

echo ""
echo "test-check-toolchain-claims: ${pass_count} passed, ${fail_count} failed"

if [ "${fail_count}" -ne 0 ]; then
  exit 1
fi

exit 0
