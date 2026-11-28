<!-- markdownlint-disable MD013 MD060 -->
# ADR-3625: Heavy CI job link profiles

- **Status**: Accepted
- **Date**: 2026-11-23
- **Deciders**: lusoris
- **Tags**: `ci`, `build`

## Context

Workflow run `45085984484` on `master` at `66f13333e4` spent most wall time in
`ninja` link steps for test executables that later jobs never ran:

| Job | Total (s) | Compile (s) | Link (s) | Test links |
| --- | ---: | ---: | ---: | ---: |
| Netflix CPU Golden | 754 | 63 | 690 | 307 |
| Tiny AI | 1288 | 115 | 1173 | 308 |
| MCP Smoke | 768 | 65 | 700 | 311 |

The `tools/vmaf` CLI finished linking in 27 s, 56 s, and 39 s respectively.

## Decision

- Compile every translation unit in each heavy job; all compile-time diagnostics
  remain on the critical path.
- Link only the targets that job consumes, plus test executables that no
  `libvmaf-build-matrix` row on the same GitHub event would link under the same
  effective Meson options and `ubuntu-26.04` runner OS label. Effective
  defaults merge project `default_options` from `core/meson.build` with
  `core/meson_options.txt`. Unknown Meson setup flags or malformed setup lines
  make a row unequal.
- When a matching matrix row is active (`c_core` and the row tier rule from
  ADR-2169), omit links for non-consumed test binaries; the matrix row builds
  them instead.
- Record the selection on each build step as a `::notice::` line from
  `scripts/ci/heavy_link_targets.py`. Each build step stops with exit 2 if the
  target list read into `targets` is empty before `ninja` runs.

## Alternatives considered

| Option | Why not |
| --- | --- |
| (a) Link only consumed targets in every heavy job | Loses test-link coverage when no matrix row matches the job options. |
| (c) Keep `meson compile -C <dir>` | Leaves 690–1173 s of unused test links per job on the measured run. |
| **Selected profile helper** | Keeps full compile coverage and reuses matrix rows when options match. |

## Consequences

- **Positive**: Heavy jobs spend link time on `tools/vmaf`, consumed tests, and
  shared libraries the pack step needs, not on hundreds of unconsumed test
  binaries.
- **Accepted risk**: Link-time (LTO) diagnostics for a test omitted here come
  only from the matching matrix row, which may use `ccache gcc-14` or
  `ccache clang-22` while heavy jobs use the runner default `cc`. Compiler
  identity is not part of the equality check.
- **Drift**: When a matrix row's effective options diverge from a heavy job,
  `scripts/ci/tests/test_heavy_link_targets.py` fails until `PROFILES` twins or
  the workflow setup lines are realigned.

## References

- Operator decision `vmafx-ci-build-reuse-link-share-r2`, 2026-11-23 (CI build
  reuse, option b).
- [ADR-1140](1140-ci-impact-planner.md) — impact planner outputs.
- [ADR-2169](2169-ci-fewer-runs.md) — tier gating for matrix legs.
