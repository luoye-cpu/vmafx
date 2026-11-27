- **The dated exceptions of the 2027-01 window are renewed or re-rendered, none left to
  expire unrecorded (issue #2944).** The five list entries expiring before 2027-01-31 —
  `HISS-10` (ffmpeg-integration consumers job), `HISS-11` (docker publish provenance) and
  three `clang-tidy-coverage` units waiting on a tidy-baseline re-measure (`test_integer_adm_buffer_alloc.c`,
  `test_xxd_extern_types.c`, `test_xxd_hsaco_extern_types.c`) — are renewed to 2027-03-31
  with category A, `entered` 2026-11-28 and a renewal sentence in the reason naming why the
  source cannot be fixed here; the praetor render cap `PRAETOR_EXPIRY_CAP` moves from
  2027-01-04 to 2027-02-26 (90 days from the renewal date) and `.standards.yaml` is
  re-rendered, so the 18 category-A rows the cap had cut to 2027-01-04 stay live past the
  window (`scripts/ci/praetor_tidy_coverage.py` renewed with the entries it caps). Decisions
  and the by-expiry ledger: `docs/research/2944-exception-ledger-decisions.md`.
