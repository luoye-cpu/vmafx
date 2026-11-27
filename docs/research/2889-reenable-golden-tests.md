# Research-2889: re-enabling the two scaffold-audit skips without touching golden values

- **Status**: Active
- **Workstream**: ADR-0621 (scaffold audit P3-3), ADR-0024 / ADR-1828 (Netflix golden gate), ADR-0108
- **Last updated**: 2026-11-28

## Question

`python/test/result_test.py::test_to_xml` and
`python/test/routine_test.py::test_compare_two_quality_runners_on_dataset` were skipped unconditionally
(scaffold-audit ADR-0621 P3-3, tracking issue #2889). The skips blame (1) XML attribute order that
"differs across Python versions" and (2) "bootstrap resampling values drifting with numpy RNG API
changes". Can both tests be re-enabled with the fork's golden values byte-identical (AGENTS.md rule 1,
ADR-1828) by fixing the tested code, and what actually drifts?

## Sources

- `python/test/result_test.py`, `python/test/routine_test.py` on `upstream/master` (`d61cd1f5d`) and on
  [Netflix/vmaf master](https://github.com/Netflix/vmaf) (fetched 2026-11-28).
- `compat/python-vmaf/core/result.py` `to_xml`, `compat/python-vmaf/routine.py`
  `_bootstrap_correlations` / `_correlation_stat`.
- pandas 3.1.1 `pandas/core/common.py::random_state` and `pandas/core/sample.py` (installed under
  `python/requirements.txt` floors: numpy ≥ 2.5.3, pandas ≥ 3.1.0).
- CI ground truth: [run 45927899704](https://github.com/VMAFx/vmafx/actions/runs/45927899704)
  (master `6f8d9451a`, 2026-11-27), job **Coverage Gate** (id 140273527574) shard logs.
- Prior attempts: branch `test/issue-2889-reenable-python-tests` (compared XML by node — rejected here,
  it changes the golden assertion) and open PR [#3547](https://github.com/VMAFx/vmafx/pull/3547)
  (round-9 collapse, validated only on the tester-image build; its fork CI runs never completed).
- Local probes on this machine (Python 3.12.10, numpy 2.5.5, pandas 3.1.1, scipy 1.17.1): scripts
  measuring the per-resample correlations of the golden dataset under each candidate code path.

## Findings

### Netflix no longer runs either test

Netflix master skips `test_to_xml` (`@unittest.skip("numerical value has changed.")`, expected string
emptied) and comments the whole bootstrap comparison test out. The fork's golden values are therefore
the only fixed point: the golden XML string matches the fork's `ssim_result_for_test.txt` fixture, and
the four nanmean expectations (plcc 0.8655928449687122 / 0.9875440797696373, srocc
0.8642507701111302 / 1.0, all CI95 NaN) are the fork's authority. Neither expectation moved.

### test_to_xml: two version-bound deltas in the tested code, not one

Baseline on master code (Python 3.12.10): frames emit `frameNum="0" SSIM_...=...` (golden: score keys
first, `frameNum` last) and the aggregate emits `method="mean" SSIM_...=...` (golden: score keys first,
`method` last) — **and** the aggregate float text differs: code prints Python-3 shortest-repr
(`0.9358023333333333`), the golden string has Python-2 `str(float)` 12-significant-digit text
(`0.935802333333`). The old skip note ("scores are correct") missed the second delta.

Mechanics: `ElementTree` serializes attributes in insertion order (dict order); `xml.dom.minidom`
sorted attributes alphabetically until Python 3.8 (bpo-34160), when it switched to preserving document
order. The golden string was recorded in the pre-3.8 world, where the alphabetical sort put vmaf-style
keys (`SSIM_*`, uppercase) before `frameNum`/`method` (lowercase) regardless of insertion order. On
3.8+ the insertion order (`frameNum` first, `method` first) started leaking into the output.

Fix: `Result.to_xml()` now emits score keys first, then `frameNum`/`method` — for vmaf-style keys that
is exactly the recorded (alphabetical) order — and a new `_format_score_attr()` renders floats with
`%.12g` plus the Python-2 integral-`.0` rule (`str(100.0)` stays `100.0`), keeping non-float values on
`str()`. The output is byte-stable across the supported Python floor (3.12; no pre-3.8 minidom left to
worry about, and the emission order coincides with the sort order anyway). `from_xml` is
order-independent (it keys off attribute names), so the round-trip test
`ResultAggregatingTest::test_from_xml_from_json_and_aggregation` is unaffected; the package-internal
`compat/python-vmaf/tests/test_result.py` XML tests assert only round-trip equality, not text.

Measured red→green: baseline `assertEqual` fails (frame/aggregate lines differ as above); after the
fix the full golden string matches byte-for-byte with the expectation untouched (2026-11-28, local).

### Bootstrap test: the RNG theory was wrong; the drift is the scorer's flat-asset separation

The skip blamed `np.random.seed` vs `default_rng`. Measured reality:

1. **The draw sequences never drifted.** `pandas.core.common.random_state(None)` returns the global
   `np.random` module, so `np.random.seed(seed_resample)` + `df.sample()` draws a legacy `RandomState`
   stream — stable by numpy's compatibility policy, and `choice(n, size, replace=True, p=None)`
   delegates to `randint` in every numpy release. With `seed_resample=0` the 10 index sequences are
   fixed on every build/numpy/pandas combination measured:
   `[0,3,1,0] [3,3,3,3] [1,3,1,2] [0,3,2,0] [0,0,2,1] [2,3,3,2] [0,1,1,1] [1,0,1,0] [3,0,3,1] [2,3,3,0]`.
2. **The PSNR side already reproduces golden exactly.** `second_prediction` is capped PSNR
   (60.0 / 11.321920999999998 / 60.0 / 29.045953) — build-independent. Master's code yields
   nanmean plcc_second = 0.9875440797696373 and srocc_second = 1.0 to all 16 digits.
3. **The VMAF side carries the drift.** The golden expectations are only reachable when the two flat
   assets (asset 2 = flat_0-vs-flat_0, asset 3 = flat_0-vs-flat_10) are **tied** in the correlation
   input. Resample 5 = `[2,3,3,2]` then has a constant prediction column → NaN → srocc_first's golden
   nanmean 0.8642507701111302 is a mean of 8 rank-based values, and those rank values are exactly the
   tie patterns (0.9428090415820635, 0.5773502691896257, 0.8164965809277261, 1.0, ...). Verified by
   simulation: a df with the flat pair bitwise-equal reproduces srocc_first to all 16 digits and
   plcc_first within 2.9e-7 under the *original* code.
4. **Live builds separate the pair by build noise.** Measured `first_prediction` for assets 2/3:
   97.4280426426119 vs 97.4280426426108 (1.1e-12, tester image, PR #3547 probe); 97.42804264260998 vs
   97.42804264261024 (2.6e-13, local release gcc 16.2 build); and **97.428042675471147 vs
   97.4278247185967 (2.18e-4) on the fork's Coverage Gate debug build** — proven by the shard log of
   run 45927899704: `TestTrainOnDataset::test_test_on_dataset PASSED`, whose golden pins those two
   values at places=4 (two ±5e-5 windows that cannot overlap). The ordering even flips between builds.
   The separation magnitude is itself build noise (three builds, three different separations), not
   signal: both flat assets sit on one model plateau.
5. **pandas 3 made the constant-column resample an error, not a NaN.** pandas' spearman now delegates
   to `scipy.stats.spearmanr`, which raises `ConstantInputWarning` on an all-equal input; resample 1
   (`[3,3,3,3]`, one asset drawn four times, P=4·(1/4)⁴ per draw set but fixed by seed 0) is such a
   resample, and `python/tox.ini`'s `filterwarnings = error` escalates it. Older pandas stacks returned
   NaN silently — which is what the golden's all-NaN CI95 assertions encode (`np.percentile` over any
   NaN-containing array is NaN).

Consequences per build on master's code: noise-separated builds give spurious ±1 correlations and rank
flips (nanmean plcc_first 0.6583 vs golden 0.8656); the CI's separated build gives the physically
correct but non-golden 0.8805/0.9529; everything fails at the ConstantInputWarning first.

### The fix, and why thousandth resolution

`_correlation_stat` now normalizes the prediction column at **three decimals** — the finest grid that
absorbs the largest observed cross-build separation (2.18e-4) — and reports NaN when either column has
fewer than two distinct values (groundtruth or predictions). Differences at the grid and above keep
ranking, so the collapse only eats the noise class. `_bootstrap_correlations` hands an explicit legacy
`np.random.RandomState(seed_resample)` to `df.sample(random_state=...)`: a freshly seeded instance
draws the same stream as the seeded global singleton, so the recorded draw sequence is preserved while
the code stops mutating (and depending on) process-global RNG state — the issue's "显式 legacy
RandomState(seed)" directive, with the sequence pinned to the golden.

Measured against the golden, per prediction family (probe, 2026-11-28):

| predictions | plcc_first | plcc_second | srocc_first | srocc_second |
| --- | --- | --- | --- | --- |
| family A = Coverage Gate build (97.428042675471147 / 97.4278247185967) | +2.605e-07 | -6.838e-09 | exact | exact |
| family B = release builds (1.1e-12 / 2.6e-13 separation) | +2.208e-07 | -6.838e-09 | exact | exact |

All four deltas sit far inside the `places=4` tolerance (±5e-5). End-to-end: both target tests pass
locally with the golden values untouched (2026-11-28), and the CI-side family is proven by the shard
log plus the family-A simulation of this exact code path.

The boundary margin: the golden pair maps into one 1e-3 cell ([97.4275, 97.4285)) with ≥ 3.2e-4 of
margin from the cell edge, about 1.5× the observed build spread. A build that pushed the plateau pair
across a cell edge would break the tie again; that risk is recorded in the rebase note and in the
`_correlation_stat` comment.

### CI evidence trail

- [Run 45927899704](https://github.com/VMAFx/vmafx/actions/runs/45927899704) (master `6f8d9451a`,
  2026-11-27), job Coverage Gate (140273527574) — job success, shard logs show
  `test_test_on_dataset PASSED` and `test_compare_two_quality_runners_on_dataset SKIPPED`.
- PR #3547's heads carry only bot check runs (CodeRabbit/Sourcery skipped, fork runs
  `action_required`) — the round-9 approach was never validated by the fork's CI. This digest closes
  that gap: round(9) cannot pass the Coverage Gate (the family-A separation is 2.18e-4, far above
  1e-9; the round-9 code would leave the pair distinct and compute the non-golden 0.8805/0.9529).

## Alternatives explored

- **Compare XML by node instead of by string** (prior branch `test/issue-2889-reenable-python-tests`).
  Rejected: it rewrites the golden assertion, which the issue forbids.
- **Round predictions at 9 decimals** (PR #3547). Reproduces golden on release builds (1e-12-class
  separation) but not on the Coverage Gate build (2.18e-4): the pair stays distinct and the computed
  nanmeans miss golden by 1.5e-2 / 8.9e-2. Rejected after measuring the CI build's prediction family.
- **Round at the 6-decimal reporting grid.** Same failure — 2.18e-4 is far above 1e-6, the pair stays
  distinct (probe: plcc_first +1.5e-2 FAIL). Also breaks the second side once applied coarsely enough
  to matter.
- **Leave `_correlation_stat` alone and only fix the warning.** Fails everywhere: spurious ±1 on
  noise-separated builds, 1.5e-2/8.9e-2 off golden on the CI build.
- **Make the scorer emit bitwise-equal flat-asset scores** (clamp features on near-constant content).
  Rejected: it changes score semantics globally to satisfy one golden pair — the Netflix golden gate
  forbids score-moving changes, and the integer-model pins already record a 3.3e-8 separation.
- **Mark the test slow / skip on CI.** Rejected: a disguised skip; the issue forbids empty skips.
- **Boundary-free clustering instead of grid rounding** (single-linkage at 1e-3, cluster mean as tie
  value). Equivalent outcomes on the golden data but more code for the same contract; grid rounding
  keeps the normalization legible (`round(3)` in one line) and the margin analysis above bounds the
  edge risk.

## Open questions

- Whether the Coverage Gate's debug build and future toolchain bumps keep the flat-asset plateau
  inside one 1e-3 cell. The margin is ~1.5× the largest observed spread; a wider spread would need a
  recorded golden re-measurement (an ADR-1828 exception, not a silent edit).
- Whether Netflix ever re-enables its own copy of the bootstrap test; if it does with different
  values, the fork's golden row becomes a deliberate fork divergence to carry in the rebase note.

## Related

- ADRs: [ADR-0621](../adr/0621-scaffold-audit-p3-cleanup.md), [ADR-0024](../adr/0024-netflix-golden-preserved.md),
  [ADR-1828](../adr/1828-port-netflix-golden-updates.md), [ADR-0108](../adr/0108-deep-dive-deliverables-rule.md)
- PRs: #3547 (prior round, open), #3539 (test_to_xml attempt)
- Issues: #2889
