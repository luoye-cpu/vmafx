# Research-2748: Setting the documented hardening flags in release builds

- **Status**: Implemented (CI evidence requested in the pull request)
- **Workstream**: [docs/principles.md section 2.2](../principles.md), issue 2748
- **Last updated**: 2026-11-28

## Question

The v1.0.0-rc.3 Linux release artifacts (the native bundle's `vmaf` and
`libvmaf.so.3.0.0`, and the published images built from
`RELEASE_BUILDER_BASE`, Debian 13 with GCC 14.2.0) carry only compiler-default
protection. `docs/principles.md` section 2.2 lists hardening goals the release
build does not set: `_FORTIFY_SOURCE=3`, `-fstack-protector-strong`, explicit
PIE and RELRO + BIND_NOW (PIE was already met by toolchain defaults; RELRO was
partial, without `-z now`; no `__stack_chk_fail` import proves no stack
protector; plain `memcpy`/`snprintf` with no `__*_chk` proves no fortify).
Where is the one shared place to set the flags for every release path, which
exact flag set do the goals define, and what evidence proves the flags did not
move a score?

## Sources

- `docs/principles.md` section 2.2 ("Target hardening flags") — the goal list.
- Issue 2748 (maintainer measurements of v1.0.0-rc.3) — the baseline.
- `scripts/release/build-native-release-artifacts.sh` — bundle build.
- `docker/Dockerfile.production`, `.node`, `.controller`, `.production-gpu`,
  `.tester` — release image builds from `RELEASE_BUILDER_BASE`.
- `scripts/ci/build-oneapi-libvmaf.sh` — the shared oneAPI (icx) release
  build of `builder-oneapi2026` in Dockerfile.node and Dockerfile.production-gpu.
- `scripts/ci/build-wheel-libvmafx.py` — the Linux wheel build.
- commit `d61949113` (#3543, "build: share sanitizer and coverage meson
  arguments") — the approved precedent for sharing build arguments.
- glibc `include/features.h` (tags glibc-2.28 and newer) — fortify level 3
  support.

## Method and fingerprint

Read-only audit of the release paths at upstream/master `d61cd1f5d`,
implemented on `build/issue-2748-release-hardening-flags`. Every `meson setup`
that produces a published Linux artifact was enumerated
(`rg -n "meson setup" scripts/release docker dev .github/workflows Makefile`)
and classified release vs development. The glibc fortify claims were checked
against the glibc sources, not assumed.

## Decision 1 — the flag set is the principles.md goal list

The issue says "the four flags"; the principles goal list names four build
flags plus one audit goal:

| Goal | Flag | Where it lands |
| --- | --- | --- |
| Fortify | `-D_FORTIFY_SOURCE=3` | `c_args`/`cpp_args` |
| Stack protector | `-fstack-protector-strong` | `c_args`/`cpp_args` |
| PIE | `-fPIE` compile, `-pie` link | `c_args`/`cpp_args` + `c_link_args`/`cpp_link_args` |
| RELRO + BIND_NOW | `-Wl,-z,relro,-z,now` | `c_link_args`/`cpp_link_args` |
| valgrind memcheck | audit workflow, not a flag | out of this change (named in the PR body) |

PIE is set explicitly rather than left to toolchain defaults so the property
no longer depends on the base image's gcc configuration.

## Decision 2 — one Meson native file inside `core/`

Candidates considered:

1. **Makefile variables, #3543-style** — rejected: the precedent shares
   arguments among Makefile targets only; the release paths are a shell
   script, five Dockerfiles, a shared Dockerfile script and a Python wheel
   builder, none of which read the Makefile.
2. **`build-config.env` knob** — rejected: it is the single source for
   *versions and base images*, loaded by `load-build-config.sh` into
   `$GITHUB_ENV`; Docker builds cannot source it at build time and the
   selective contexts (Dockerfile.node/controller/tester copy only `core/`
   plus a few files) do not carry it.
3. **A conditional in `core/meson.build`** (auto-harden `--buildtype=release`)
   — rejected: it would silently harden every development lane that builds
   `--buildtype=release` (golden gates, twin matrices, ffmpeg integration),
   changing their generated code as a side effect of landing the release
   change.
4. **`core/native/release-hardening.ini`, passed with `--native-file`**
   (chosen): the flags live in one Meson native file that every release
   path references by name. `core/` is the Meson project root, so a file
   under `core/` rides along in every build context that already copies
   `core/` (Dockerfile.node and Dockerfile.controller deliberately keep
   their contexts closed over configure-time inputs); the bundle script and
   the wheel script resolve it from the repository root. Development and CI
   test builds do not pass the file and stay byte-identical.

The location `core/native/` (not `core/build-aux/`) avoids the
`.dockerignore` `core/build-*/` exclusion, which exists to keep build trees
out of the context.

### nvcc and b_pie

`b_pie = true` was rejected: Meson's `CudaCompiler` inherits the base
`get_pie_args()`, which raises `EnvironmentException` ("Language cuda does not
support position-independent executable"), so the GPU release legs
(Dockerfile.production-gpu `builder-cuda13`, the tester CUDA leg) would fail
at configure time. The explicit `-fPIE`/`-pie` pair never reaches nvcc
(Meson configures CUDA through its own `cuda_args`/`cuda_link_args`). For
shared libraries GCC emits `-shared` without `-pie` (its LINK_SPEC nests
`-pie` inside `%{!shared:}`), and the clang driver (icx, hipcc) likewise
suppresses `-pie` for shared outputs, so the link flag is safe to pass
globally; Meson adds `-fPIC` after `-fPIE` for shared objects, keeping the
chain PIC.

### The fortify level is the builder's glibc's

`-D_FORTIFY_SOURCE=3` is set everywhere; what the headers do with it depends
on the builder (verified against glibc sources, glibc-2.28 tag):

- Debian 13 builders (glibc 2.41, GCC 14.2.0): level 3 applies.
- The bundle builder (quay.io/pypa manylinux_2_28, AlmaLinux 8, glibc 2.28
  headers): glibc 2.28's `features.h` handles `_FORTIFY_SOURCE > 1` with the
  `#elif _FORTIFY_SOURCE > 1` branch, so 3 is silently capped at
  `__USE_FORTIFY_LEVEL 2` — no warning, no build failure, and the fortified
  `_chk` entry points are still called. The bundle therefore carries
  level-2 fortify; the flag matches the documented goal and the checker's
  `__*_chk` evidence holds on both builders.

Release `-O3` satisfies fortify's optimization requirement on every path.

## Decision 3 — the image leftover run path

Meson links `build/tools/vmaf` with the build-tree RUNPATH
`$ORIGIN/../src`. The bundle has rewritten it on the staged copy since
T-RELEASE-NATIVE-RUNPATH-BUILD-TREE-2026-09-27, but the published images
copy the build-tree binary to `/usr/local/bin/vmaf`, where `$ORIGIN/../src`
resolves to `/usr/local/src` — a directory that does not exist. Resolution
there already goes through the runtime images' `LD_LIBRARY_PATH=/usr/local/lib`
(production, node, controller, the oneAPI image) or `ldconfig` (node's Debian
stages), so `patchelf --remove-rpath` on the staged copy is
behavior-identical and clears the leftover. `patchelf` joins the builder
stages' apt lists; `build-oneapi-libvmaf.sh` fails closed when it is missing.
The tester image is different by design: it keeps `/opt/vmafx/build/tools/vmaf`
with the build tree present (`LD_LIBRARY_PATH` names
`/opt/vmafx/build/src`), so its run path resolves and needs no rewrite.

## Decision 4 — the checker

`scripts/release/check_release_hardening.py` (readelf subprocess, in the
style of `scripts/release/check_glibc_floor.py`) reads each staged artifact
and fails on: ELF type EXEC; an executable without the PIE flag (a shared
object declares a SONAME and is exempt); missing PT_GNU_RELRO; RELRO without
BIND_NOW; no `__stack_chk_fail` import; no `__*_chk` import; a DT_RPATH; or
any RPATH/RUNPATH entry that is not `$ORIGIN`-relative or that contains
`..` (the Meson build-tree `$ORIGIN/../src` and absolute build-directory
entries are rejected). It is wired as a supply-chain release step beside the
glibc-floor check, for both bundle architectures.

`scripts/ci/tests/test_check_release_hardening.py` proves the accept/reject
matrix with canned readelf output (runs on every host) and — when the host
has `cc` and `readelf`, as the Linux runners do — compiles two tiny programs,
one with the profile's flags and one without, and requires the checker to
accept the first and refuse both negatives (no-PIE executable, unhardened
shared library). It skips where those tools are absent, like
`test_check_install_layout.py`'s tool-gated tests.

## Golden gate and exact-twin rerun plan (CI evidence)

The flags change the generated code of every feature extractor, so the score
parity gates must be re-run on a hardened build before merge; these run in
CI on the Linux runners and cannot be produced locally on this Windows
checkout. The PR body requests:

1. **Netflix golden gate** — the merge train's golden-data job builds the
   CPU core and compares the pooled `vmaf` mean against the preserved
   Netflix pins (ADR-0024/ADR-1828). Requested evidence: green run of the
   golden gate job on this branch; the pooled mean and the per-twin deltas
   quoted in the PR body.
2. **Exact-twin comparison** — `scripts/ci/exact_twin_matrix.py` /
   `twin-drift-check.sh` against the unhardened master build. Requested
   evidence: zero drift on every twin pair (the twins compare extractor
   outputs, which the stack protector and fortified calls must not move).

## Per-frame cost measurement plan (CI evidence)

The stack protector writes and checks a canary in functions with
address-taken locals, and fortified calls carry the extra object-size
argument — both sit in extractor hot loops, so the PR must quantify the
per-frame cost rather than assert it:

- Measure `tools/vmaf` CPU time per frame on the Netflix golden CPU pair,
  hardened build vs `upstream/master` build (same runner, same machine,
  `--threads 1`, three repetitions, report median), via the rc1-tester
  timing surface the tester image already carries
  (`VMAF_*_timing` pooled metrics of the golden pair job).
- Expected bound from upstream experience with the same flags: within the
  run-to-run noise band (low single-digit percent). If the median regresses
  beyond the noise band, the PR takes that to the maintainer before merge
  rather than landing silently.
- The PR body records the numbers from CI; this digest records the plan and
  the bound.

## Alternatives

- **Harden in `core/meson.build` unconditionally** — simplest wiring, but it
  changes development builds' generated code (golden/twin comparisons, lint
  lanes) and couples the release decision to the project build.
- **Set flags per path** (one edit per Dockerfile/script) — no shared place;
  the next release path forgets one flag. The checker catches it at artifact
  level, but the drift begins at the source.
- **Distro hardening wrapper (`dpkg-buildflags`)** — the issue's own
  contrast: Debian's set is `-fstack-protector-strong
  -fstack-clash-protection -fcf-protection -D_FORTIFY_SOURCE=2
  -Wl,-z,relro` with `-Wl,-z,now` only at hardening=+all, and the release
  build does not go through dpkg; the documented goals (`=3`, BIND_NOW)
  differ, so the wrapper would set the wrong set.

## Findings (wiring audit)

Every release path now names the profile:

| Path | Change |
| --- | --- |
| `scripts/release/build-native-release-artifacts.sh` | `--native-file` in `build_release()` |
| `docker/Dockerfile.production` | builder `meson setup`; `patchelf --remove-rpath` on the staged CLI |
| `docker/Dockerfile.node` | `vmaf-builder` setup; staged CLI rpath cleared |
| `docker/Dockerfile.controller` | `vmaf-builder` setup; staged CLI rpath cleared |
| `docker/Dockerfile.production-gpu` | `builder-cuda13` and `builder-rocm10` setups; staged CLI rpath cleared |
| `docker/Dockerfile.tester` | cpu, sycl (icx), cuda, hip setups (keeps its build-tree layout) |
| `scripts/ci/build-oneapi-libvmaf.sh` | oneAPI setup; staged CLI rpath cleared; patchelf fail-closed |
| `scripts/ci/build-wheel-libvmafx.py` | Linux-only `--native-file` (Windows/macOS wheels out of scope) |
| `.github/workflows/supply-chain.yml` | checker step on the staged artifacts (x86_64 and arm64) |

Out of scope, as the issue's "Not covered" section says: Windows (MSVC has
its own flag surface), macOS (no glibc fortify) and the arm64 *bundle
verification* (the arm64 bundle builds through the same script and checker —
the release job runs both architectures — but the maintainer's measurements
cover x86_64 only). valgrind memcheck stays a target, not a flag.
