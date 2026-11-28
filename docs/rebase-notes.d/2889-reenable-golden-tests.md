## The to_xml formatter and the bootstrap correlation resampler carry golden-stability rules (2026-11-28)

`test/issue-2889-reenable-golden-tests`, [issue #2889](https://github.com/vidiko/vmafx/issues/2889).
`compat/python-vmaf/core/result.py` `to_xml()` emits attributes in the recorded order (score keys
first, then `frameNum`/`method`) and formats score floats by default with Python 2's 12-digit
`str(float)` semantics (`_format_score_attr()`, `full_precision=True` restores shortest-repr), so
the golden XML in `python/test/result_test.py::test_to_xml` — re-enabled on this branch — is
byte-stable on every supported interpreter; upstream's `to_xml()` still sets `frameNum`/`method`
first and prints Python-3 repr, and upstream itself skips that test ("numerical value has changed").
`compat/python-vmaf/routine.py` `_correlation_stat()` stays **unrounded** (a review finding: rounding
the prediction column inside a public statistic collapses genuinely distinct predictions on 0..1
scorers) and returns NaN when a resampled column has fewer than two distinct values, and
`_bootstrap_correlations()` draws with an explicit legacy `np.random.RandomState(seed_resample)`
handed to `df.sample(random_state=...)` — the same draw sequence the golden expectations were
recorded with, without touching global RNG state. `test_compare_two_quality_runners_on_dataset`
stays skipped: its golden nanmeans need the dataset's two flat assets tied, which only the
recording build produced (live builds separate the pair by 1.1e-12 to 2.18e-4 with the ordering
flipping — the skip reason names it; upstream's harness has none of these rules and Netflix
comments its copy of the bootstrap test out). A rebase must keep the emission order, the
12-digit-default + `full_precision` contract in `result.py`, and the unrounded/NaN/RandomState
contract in `routine.py` (coverage: the re-enabled `test_to_xml` golden asserts; the skip reason
and the measured family tables in
[docs/research/2889-reenable-golden-tests.md](../research/2889-reenable-golden-tests.md)).
