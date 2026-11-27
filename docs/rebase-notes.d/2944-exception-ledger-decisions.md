## The 2027-01 exception-window decisions (2026-11-28)

- `.config/lint-exceptions.d/{HISS-10,HISS-11,clang-tidy-coverage}.toml`: five entries
  renewed to 2027-03-31 (entered 2026-11-28, category A, renewal sentence appended to the
  reason in the PR #3522 form). **On rebase**: if a concurrent PR also edits these three
  files, the conflict is mechanical — keep the newer `expires`/`entered` and merge the
  renewal sentences; an entry whose source violation landed (a tidy lane measured one of
  the three test files, the SLSA restructuring landed, or the praetor vendored-tree
  log-scan gate shipped) should be deleted outright instead of renewed, and then
  `scripts/ci/praetor_tidy_coverage.py --write` re-run.
- `scripts/ci/praetor_tidy_coverage.py`: `PRAETOR_EXPIRY_CAP` 2027-01-04 → 2027-02-26
  (comment documents the renewal). **On rebase**: a concurrent praetor-pin bump that moves
  this constant is a real conflict — take the later date (the cap must always be within
  praetor's 90-day refusal window of the newer renewal date) and re-render.
- `.standards.yaml`: the generated block re-rendered (16 rows to 2027-02-26, 2 rows to
  their true 2027-02-12, HISS-10/HISS-11 reason/entered updates). Never hand-edit; a
  conflict here is resolved by regenerating (`--write` on the rebased tree), not by merging.
- `scripts/ci/tests/test_praetor_tidy_coverage.py`: three cap-derived literals moved to
  `2027-02-26` (test render assertions only; the D-interval and category-cutoff fixtures
  keep their own dates). **On rebase**: if the cap moves again, move these literals with it.
- `changelog.d/changed/2944-exception-ledger-decisions.md`,
  `docs/research/2944-exception-ledger-decisions.md`,
  `docs/state.md` (journal line + Recently closed row
  `T-EXCEPTIONS-LEDGER-2027-01-WINDOW-2026-11-28`): additive; rebase conflicts are
  first-line insertions that stack.
