# Research-2889: re-enabling the scaffold-audit skips without touching golden values

- **Status**: Active
- **Workstream**: ADR-0621 (scaffold audit P3-3), ADR-0024 / ADR-1828 (Netflix golden gate), ADR-0108
- **Last updated**: 2026-11-28

<!-- markdownlint-disable MD013 -->

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
- CI ground truth: [run 45927899704](https://github.com/vidiko/vmafx/actions/runs/45927899704)
  (master `6f8d9451a`, 2026-11-27), job **Coverage Gate** (id 140273527574) shard logs.
- Prior attempts: branch `test/issue-2889-reenable-python-tests` (compared XML by node — rejected here,
  it changes the golden assertion) and open PR [#3547](https://github.com/vidiko/vmafx/pull/3547)
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

### What landed, and why the library stays unrounded

Two things landed in the tested code. `_bootstrap_correlations` hands an explicit legacy
`np.random.RandomState(seed_resample)` to `df.sample(random_state=...)`: a freshly seeded instance
draws the same stream as the seeded global singleton, so the recorded draw sequence is preserved
while the code stops mutating (and depending on) process-global RNG state — the issue's "explicit
legacy RandomState(seed)" directive, with the sequence pinned to the golden. And
`_correlation_stat` reports NaN when either resampled column has fewer than two distinct values
(groundtruth or predictions): the value the pre-warning pandas stack returned silently, where
today's stack warns (and `pytest` config `filterwarnings=error` escalates scipy's
ConstantInputWarning to an error).

**A third change — rounding predictions at three decimals — was implemented, measured, and then
removed after review.** It reproduced the recorded ties on every measured build family (probe
deltas below), but review measured that it changes the public statistic on 0..1 scorers:

| input (0..1 predictor) | master | rounded head |
| --- | --- | --- |
| predictions distinct at 1e-4 | plcc 0.988538, srocc 1.000000 | plcc 1.000000, srocc 1.000000 |
| all predictions inside one 0.001 cell | plcc 1.000000, srocc 1.000000 | plcc nan, srocc nan |

The tie behaviour the golden test needs is a property of the recording build, not of the statistic,
so it does not belong in the library. `test_compare_two_quality_runners_on_dataset` is therefore
skipped again, with a skip reason that names the measured build-noise separation; `test_to_xml`
stays re-enabled (its fix is confined to the XML formatter, which review accepted with an explicit
full-precision option).

Measured against the golden with the removed rounding in place, per prediction family (probe,
2026-11-28) — the numbers that motivated the round(3) attempt, kept as the record of why a
precision-restoring grid was tried at all:

| predictions | plcc_first | plcc_second | srocc_first | srocc_second |
| --- | --- | --- | --- | --- |
| family A = Coverage Gate build (97.428042675471147 / 97.4278247185967) | +2.605e-07 | -6.838e-09 | exact | exact |
| family B = release builds (1.1e-12 / 2.6e-13 separation) | +2.208e-07 | -6.838e-09 | exact | exact |

### Reproduction commands

```bash
# the two tests, on a checkout with a local release build wired through
# VMAFEXEC_PATH (see compat/python-vmaf/externals.py):
python3 -m pytest -p no:anyio -q \
  python/test/result_test.py::ResultFormattingTest::test_to_xml \
  python/test/routine_test.py::TestTrainOnDataset::test_compare_two_quality_runners_on_dataset
# expected on this branch: test_to_xml PASSED, the dataset runner test SKIPPED
# (reason names the 2.18e-4 flat-asset separation)

# the golden-integrity check: only skips/comments may differ under python/test/
git diff upstream/master -- python/test/result_test.py python/test/routine_test.py

# the per-family probe: feed the recorded family-A/family-B prediction vectors
# through _bootstrap_correlations and diff the four nanmeans against golden
python3 -m pytest -p no:anyio -q scripts/ci/tests/test_golden_probe_fixture.py  # not in tree;
# the probe was a local script (flat vector table -> _paired_prediction_frame ->
# nanmeans vs golden); the family vectors and expected deltas are the two tables
# above, and the skipped test's golden asserts are the reference values.
```

### CI evidence trail

- [Run 45927899704](https://github.com/vidiko/vmafx/actions/runs/45927899704) (master `6f8d9451a`,
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
  value). Equivalent outcomes on the golden data but more code for the same contract.
- **Round at three decimals (implemented in this branch's first commit, removed after review).**
  Reproduced the recorded ties on every measured build family — but review measured that rounding a
  0..1-scaled prediction column inside a public statistic collapses genuinely distinct predictions
  (1e-4-apart inputs report plcc 1.000000 instead of 0.988538) and turns a whole 0.001 cell into
  NaN. The tie is the recording build's property, so it moved into the skip reason, not the library.
- **Re-record the golden against a pinned build.** Would need an ADR-1828 exception (the fork's
  golden rows are the authority); out of scope for a skip-re-enable fix and it re-bakes one build's
  noise into the fork's recorded expectations.

## Open questions

- What a future re-enable of `test_compare_two_quality_runners_on_dataset` would take: the golden
  expectations are only reachable on a scorer build that ties the flat pair, so either the fork
  records a re-measurement against a pinned build (an ADR-1828 exception, not a silent edit) or the
  upstream Netflix/vmaf repo re-records its own copy — the library will not quantize predictions to
  fabricate the ties.
- Whether Netflix ever re-enables its own copy of the bootstrap test; if it does with different
  values, the fork's golden row becomes a deliberate fork divergence to carry in the rebase note.

## Related

- ADRs: [ADR-0621](../adr/0621-scaffold-audit-p3-cleanup.md), [ADR-0024](../adr/0024-netflix-golden-preserved.md),
  [ADR-1828](../adr/1828-port-netflix-golden-updates.md), [ADR-0108](../adr/0108-deep-dive-deliverables-rule.md)
- PRs: #3547 (prior round, closed as superseded by this work), #3539 (earlier test_to_xml attempt,
  closed as superseded)
- Issues: #2889
