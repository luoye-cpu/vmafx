# Perf gate guide

The wall-clock perf regression gate ([ADR-0907](../adr/0907-perf-regression-gate-wall-clock.md),
status Proposed) compares a fresh multi-resolution benchmark run against a
committed baseline. **It is not wired into CI, a git hook or a Makefile
target.** Nothing in the tree calls `scripts/perf/check-regression.py` or
`scripts/perf/bench-multi-resolution.sh`; you run both by hand, and no job
reports a perf regression for you. Wiring the gate in belongs to the
benchmarks candidate, RC8
([ADR-1490](../adr/1490-rc3-rc9-candidate-map-cpu-capability.md)), because
benchmarks and tuning never happen before it.

## What the scripts do

1. `scripts/perf/bench-multi-resolution.sh` runs the `vmaf` binary over
   resolution, backend and metric combinations and writes the timings to a JSON
   file you name with `--output`.
2. `scripts/perf/check-regression.py` joins that file against the committed
   baseline (`testdata/perf_multi_resolution.json`) by (resolution, backend,
   metric) and reports every cell whose median wall time exceeds the baseline
   by more than `--tolerance-pct` (default 5). A cell it cannot compare is
   listed under "Not compared" with one of four states and makes the run
   exit 3 ([ADR-3611](../adr/3611-measurement-non-success-states.md)).

| State | Meaning |
| --- | --- |
| `missing` | The cell is in one run only |
| `failed` | A status other than `ok` (current or baseline), or no numeric current median |
| `skipped` | The baseline cell has status `skip` |
| `incomparable` | Workload field (`width`, `height`, `bitdepth`, `frames`, `fixture_source`) differs, hardware model differs or is absent, or the baseline median is not positive |

The hardware check reads `hardware.cpu_model` for CPU cells and
`hardware.gpu_model` for every other backend. Pass `--backend` to leave out
a backend the host does not have.

| Exit code | Meaning |
| --- | --- |
| 0 | Every cell compared, none regressed |
| 1 | At least one regression (masked by `--advisory`) |
| 2 | Input error: unreadable file, malformed JSON or a `runs` value that is not a list |
| 3 | At least one cell not compared, no cell for the requested backends in either input, or `--skip-if-no-baseline` found no `ok` baseline cell |

A regression outranks exit 3. `--advisory` masks only exit 1, so an advisory
run still exits 3 when a cell was not compared.

## The baseline

The committed baseline holds 40 `ok` cells (CPU and CUDA) recorded on 2026-05-29
on one workstation (AMD Ryzen 9 9950X3D, RTX 4090). Timings do not transfer
between machines: compare a run only against a baseline recorded on the same
hardware and the same build configuration, or refresh the baseline first.
The baseline also holds ten `skip` cells (`fixture_unavailable`, five CPU and
five CUDA), so a full comparison against it exits 3 until it is refreshed
with those fixtures present.

## Run it locally

```bash
# Build first
meson setup core/build -Denable_cuda=false -Denable_sycl=false --buildtype=release
ninja -C core/build

# Benchmark
VMAF_BIN=core/build/tools/vmaf \
  scripts/perf/bench-multi-resolution.sh \
  --backends cpu \
  --runs 3 \
  --output /tmp/perf_current.json

# Compare against the committed baseline
python3 scripts/perf/check-regression.py \
  --baseline testdata/perf_multi_resolution.json \
  --current /tmp/perf_current.json \
  --tolerance-pct 5.0 \
  --backend cpu
```

## Refresh the baseline

Record a new baseline on the machine you want to compare against:

```bash
VMAF_BIN=core/build/tools/vmaf \
  scripts/perf/bench-multi-resolution.sh \
  --backends cpu,cuda \
  --runs 5 \
  --output testdata/perf_multi_resolution.json
```

Check that the cells you care about have `status: ok` and that the file
carries the `hardware` block (without it every cell is `incomparable`), and
commit the file only as a deliberate baseline change with its hardware named
in the commit message
([agent hard rule 13](agent-hard-rules.md): ad-hoc benchmark output is never
committed).

## What wiring it in would take (RC8)

- A baseline recorded on the runner class the job runs on; the workstation
  baseline is several times faster than a hosted runner, so a hard 5 %
  tolerance against it flags noise.
- A job that runs the two scripts and uploads the run JSON, advisory first
  (`--advisory`, which still exits 3 for cells not compared), then blocking
  once the tolerance has been calibrated against runner variance.
- GPU cells on a self-hosted GPU runner, as a second `check-regression.py`
  invocation with `--backend cuda`.
- An ADR that moves ADR-0907 out of Proposed and records the tolerance.

## Relevant files

| File | Purpose |
| --- | --- |
| `testdata/perf_multi_resolution.json` | Committed baseline (refreshed by hand) |
| `scripts/perf/bench-multi-resolution.sh` | Benchmark harness |
| `scripts/perf/check-regression.py` | Comparison and reporting script |
| `scripts/perf/test_check_regression.py` | Unit tests for the comparison script |

## See also

- [Performance guide](perf.md): profiling and the same not-wired note.
- [ADR-0907](../adr/0907-perf-regression-gate-wall-clock.md): the gate's design.
- [ADR-1005](../adr/1005-perf-gate-advisory-threshold.md): advisory mode and
  baseline refresh (the CI step it describes does not exist yet).
- [ADR-3611](../adr/3611-measurement-non-success-states.md): states and exit
  codes for cells that were not compared.
