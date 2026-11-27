## The to_xml formatter and the bootstrap correlation resampler carry golden-stability rules (2026-11-28)

`test/issue-2889-reenable-golden-tests`, [issue #2889](https://github.com/VMAFx/vmafx/issues/2889).
`compat/python-vmaf/core/result.py` `to_xml()` emits attributes in the recorded order (score keys
first, then `frameNum`/`method`) and formats score floats with Python 2's 12-digit `str(float)`
semantics (`_format_score_attr()`), so the golden XML in `python/test/result_test.py::test_to_xml` is
byte-stable on every supported interpreter; upstream's `to_xml()` still sets `frameNum`/`method`
first and prints Python-3 repr, and upstream itself skips that test ("numerical value has changed").
`compat/python-vmaf/routine.py` `_correlation_stat()` normalizes prediction columns at three decimals
(the finest grid absorbing the observed cross-build flat-asset separation, 1.1e-12 .. 2.18e-4 with
flipping order) and returns NaN when a resampled column has fewer than two distinct values, and
`_bootstrap_correlations()` draws with an explicit legacy `np.random.RandomState(seed_resample)`
handed to `df.sample(random_state=...)` — the same draw sequence the golden expectations were
recorded with, without touching global RNG state. Upstream's harness has none of these three rules
and Netflix comments its copy of the bootstrap test out. A rebase must keep both the emission order
and the 3-decimal/NaN/RandomState contract in `compat/python-vmaf/routine.py` (coverage: the
re-enabled `test_compare_two_quality_runners_on_dataset` golden asserts plus the all-NaN CI95
assertions; the boundary margin of the 1e-3 grid is ~1.5× the observed build spread, see
[docs/research/2889-reenable-golden-tests.md](../research/2889-reenable-golden-tests.md)).
