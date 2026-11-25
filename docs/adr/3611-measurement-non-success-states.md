<!-- markdownlint-disable MD013 MD060 -->
# ADR-3611: Report measurement cells and wrapper runs that were not compared as non-success

- **Status**: Accepted
- **Date**: 2026-11-22
- **Deciders**: lusoris
- **Tags**: `perf`, `bench`, `testing`

## Context

Two measurement tools reported success for runs that measured nothing.

`scripts/perf/check-regression.py` (ADR-0907, ADR-1005) joins a fresh benchmark run against a committed baseline by (resolution, backend, metric). It skipped a cell, and still exited 0, when:

- the current run lacked a cell the baseline has;
- either side had a status other than `ok`;
- the baseline median was 0;
- the two runs came from different hardware or a different workload (frame size, bit depth, frame count, fixture), because the script never compared those fields.

`--advisory` (ADR-1005) then forced exit 0 for everything, so an advisory run could not tell "every cell within tolerance" from "nothing compared". A `runs` value that was not a list ended in a traceback.

`tools/external-bench/compare.py` (ADR-0656) printed `skip ...` for each wrapper run that failed and returned 0 even when every run failed. The wrappers call `normalise_output` in `tools/external-bench/lib/common.sh`, which replaced an empty output file, a frame without a score or runtime, and a missing summary field with `0.0`. A wrapper that produced no measurement therefore emitted a payload that passed the schema check in `validate_wrapper_output`.

## Decision

A measurement that was not taken, failed, or cannot be compared is an explicit non-success with a named state and its own exit code.

`scripts/perf/check-regression.py`:

- Every cell that is not compared carries one of four states: `missing` (on one side only), `failed` (status not `ok`, or current median absent or not numeric), `skipped` (baseline status `skip`), or `incomparable` (workload field differs, hardware model differs or is absent, or baseline median not positive). The hardware check uses `cpu_model` for CPU cells and `gpu_model` for every other backend.
- Exit codes: 0 when every cell was compared and none regressed, 1 on a regression, 2 on an input error (including a malformed `runs`), 3 when at least one cell was not compared or no cell for the requested backends is in either input. A regression outranks exit 3.
- `--advisory` masks only exit 1. `--skip-if-no-baseline` stops before comparing and exits 3.
- `--backend` remains the way to leave out a backend the host does not have.

`tools/external-bench`:

- `compare.py` counts failed wrapper runs per competitor (a `failed` column in the table, `n_failed` in the JSON), still writes the partial table and JSON, and returns 5 when any run failed.
- `normalise_output`, now a call into `tools/external-bench/lib/normalise_output.py` instead of an inline heredoc, exits 1 with a named error on an empty or malformed source, no frames, a frame without a score or `runtime_ms`, a score or runtime that is not a finite number, or a missing or non-finite `plcc`, `srocc`, `rmse`, `params` or `gflops` (`params` must be an integer), and writes no output file. The score key aliases, the positional frame index and the `runtime_total_ms` sum of per-frame runtimes stay, because they are derived from measured data.

This supersedes the "reported and skipped, not failed" rule of ADR-0907 and narrows the advisory mask of ADR-1005 to the regression exit code. The gate stays unwired from CI, as before.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep skip semantics and print a louder warning | No exit code change | A caller that checks only the exit code still reads success | The defect is the exit code |
| Fail the whole run on the first non-compared cell with exit 1 | One failure code | Cannot tell a slower build from a missing or mismatched measurement | Separate codes keep the cause visible |
| Drop `skip` cells from the committed baseline now | Gate on the workstation exits 0 again | Rewrites a snapshot without re-measuring; the ten cells lack a fixture, which is a real gap | The baseline refresh is a deliberate, measured change |
| Let `--advisory` mask exit 3 too | Advisory runs never fail | Brings back the ambiguity this ADR removes | Rejected |

## Consequences

- **Positive**: A zero exit from either tool now means every cell or wrapper run was measured and compared. Each non-success names its state, so the cause shows in the log without a re-run.
- **Negative**: The committed baseline `testdata/perf_multi_resolution.json` holds ten `skip` cells (`fixture_unavailable`, five CPU and five CUDA), so a full comparison against it exits 3 until the baseline is refreshed with the fixtures present. Callers that treated any non-zero exit of `compare.py` as a crash now see 5 for partial failures.
- **Neutral / follow-ups**: The gate's unit tests run in the Tooling Tests suite; running the gate itself in CI remains with the 0.2.0 benchmarking epic (#1245), because a hosted runner never matches the baseline hardware and would exit 3. A refreshed baseline must carry the `hardware` block, or every cell is `incomparable`.

## References

- ADR-0907: wall-clock perf regression gate.
- ADR-1005: advisory mode and baseline refresh.
- ADR-0656: external-bench wrapper schema.
- `scripts/perf/check-regression.py`, `scripts/perf/test_check_regression.py`.
- `tools/external-bench/compare.py`, `tools/external-bench/lib/common.sh`, `tools/external-bench/lib/normalise_output.py`, `tools/external-bench/tests/test_compare.py`.
- `docs/development/perf-gate.md`, `tools/external-bench/README.md`.
- Source: efficiency reuse-first pass order of 2026-11-22, item 2 (paraphrased).
