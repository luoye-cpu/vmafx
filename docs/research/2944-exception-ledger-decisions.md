# Research-2944: renew-or-retire decisions for the 2027-01 exception window

- **Status**: Active
- **Workstream**: ADR-1142 (declared lint exceptions),
  ADR-1512/standards registry (`.standards.yaml`),
  ADR-0108 deliverables
- **Last updated**: 2026-11-28

## Question

Issue #2944: the repository's declared exception list holds 69 dated exceptions
that expire between 2026-12-31 and 2027-01-05. An expired exception fails like
a missing one, so each needs a decision — fix at the source so the entry can
be deleted, or renew with a reason, a category and a new expiry — before the
window opens. Which entries are they on the tree this change builds against,
which are fixable here, and which get renewed?

## Sources

- Issue #2944 (opened 2026-10-23).
- `.config/lint-exceptions.d/*.toml` (the declared list; format and rules
  `scripts/ci/lint_exceptions.py`, MAX_DAYS 400).
- `scripts/ci/praetor_tidy_coverage.py` (renders the `exceptions:` block of
  `.standards.yaml`; `PRAETOR_EXPIRY_CAP` cuts every category-A row to the
  cap; "renew the cap with the entries").
- `scripts/ci/check-tidy-coverage.py`, `scripts/ci/check-adr-status-drift.py` +
  `scripts/ci/adr-status-exceptions.json`, `osv-scanner.toml` (`ignoreUntil`),
  `.trivyignore.yaml` (`expired_at`), `.github/ci-tier.json` (`expires`) — the
  other dated registries.
- The renewal house pattern of PR #3522
  (`vmaf-test-float-identity-via-float-bits.toml`).
- `git archaeology`: the exception list's history between 2026-10-05 and
  2026-11-28.

## Findings

### The ledger at the working base (`upstream/master` `d61cd1f5d`)

Five dated registries carry expiries. The sweep at the base (field regex
`expires|expired_at|ignoreUntil` over TOML/YAML/JSON):

| registry | entries | in window 2026-12-31..2027-01-05 | expiring before 2027-01-31 |
| --- | ---: | ---: | ---: |
| `.config/lint-exceptions.d/*.toml` (the declared list) | 117 | 3 | 5 |
| `.standards.yaml` generated praetor block (render of the list) | 30 | 18 | 18 |
| `scripts/ci/adr-status-exceptions.json` | 4 | 0 | 0 (earliest 2027-02-12) |
| `osv-scanner.toml` (`ignoreUntil`) | 1 | 0 | 0 (2027-02-13) |
| `.trivyignore.yaml` (`expired_at`) | 2 | 0 | 0 (2027-02-10) |
| `.github/ci-tier.json` (`expires`) | 9+ | 0 | 0 (2027-12-31) |

The 18 rendered rows sit on `PRAETOR_EXPIRY_CAP = 2027-01-04`: praetor refuses
a category-A exception more than 90 days out, so the renderer cuts every
category-A row to the cap, and the whole rendered layer expires with it on
2027-01-04 — including 13 rows whose source entries in the list expire later
(2027-02-12, 2027-02-27, 2027-03-31). The cap is itself a dated exception the
issue's completion criterion covers.

### The entries the Done-when names (sorted by expiry, then rule)

Every entry expiring before 2027-01-31 without a recorded decision, with the
decision this change records:

| # | expires | rule / file | category | decision | one-line basis |
| --- | --- | --- | --- | --- | --- |
| 1 | 2027-01-04 | `HISS-10` / `.github/workflows/ffmpeg-integration.yml` | A | **RENEW** → 2027-03-31 | The 19 gcc warnings live in Netflix's pinned libvmaf reference tree, which the fork does not patch; the vendored-tree log-scan gate (praetor issue 825) that would excuse them is still out. |
| 2 | 2027-01-04 | `HISS-11` / `.github/workflows/docker-publish-operator-node.yml` | A | **RENEW** → 2027-03-31 | The SLSA Level 3 restructuring (build + attest in one reusable workflow) is the open tracked project `T-SLSA-RELEASE-PROVENANCE-LEVEL-2-2026-10-07`; a release-path change a ledger PR must not make. |
| 3 | 2027-01-04 | `clang-tidy-coverage` / `core/test/test_integer_adm_buffer_alloc.c` | A | **RENEW** → 2027-03-31 | The file is in no lane's `measured_sources` on master (verified against every `scripts/ci/tidy-baseline-*.json`); the fix is a tidy-baseline re-measure on the lane compile databases, which this PR does not run. |
| 4 | 2027-01-14 | `clang-tidy-coverage` / `core/test/test_xxd_extern_types.c` | A | **RENEW** → 2027-03-31 | Same gate: in no lane's `measured_sources` on master (verified); re-measure needs the lanes, not an edit. |
| 5 | 2027-01-14 | `clang-tidy-coverage` / `core/test/test_xxd_hsaco_extern_types.c` | A | **RENEW** → 2027-03-31 | Same gate: in no lane's `measured_sources` on master (verified); re-measure needs the lanes, not an edit. |
| 6-23 | 2027-01-04 (rendered) | 18 category-A rows (16 riding the cap, 2 at their own 2027-02-12) of the `.standards.yaml` block (bpf, hip inline header, 6 Pelorus mirrors, `test_colorspace`, `test_sycl_cuda_serial_upload_lifetime`, `test_integer_adm_buffer_alloc`, the two `test_xxd` files, `getopt.c`, `test_vmaf_windows_utf8_argv.cpp`, `c_api_pooled_mean.c`, HISS-10, HISS-11) | A | **RENEW the cap** 2027-01-04 → 2027-02-26 and re-render | The rendered expiry is `min(source, PRAETOR_EXPIRY_CAP)`; without a cap renewal every category-A row dies on 2027-01-04 even where the list entry was renewed. 90 days from the renewal date (2026-11-28) is 2027-02-26, inside praetor's refusal window from day one of CI. |

Decision distribution at the base: **0 fix-at-source, 0 delete, 5 renew + 1
cap renewal covering 18 rendered rows.** Two rows that the cap previously cut
to 2027-01-04 (`test_colorspace.c`, `test_sycl_cuda_serial_upload_lifetime.c`,
both renewed to 2027-02-12 by earlier PRs) now render at their true dates,
2027-02-12, no longer capped.

### Why the issue's 69 does not match any single snapshot

The list moves; the issue was filed 2026-10-23 against master `2280fd149`.
Measured two-layer counts of rows whose enforced date falls in the window
(the list's own `expires` + the rendered block's cap-cut dates):

| state | date | list rows in window | rendered rows in window |
| --- | --- | ---: | ---: |
| `860900145` (praetor 3a766f2d) | 2026-10-08 | 44 | 75 |
| `8e4f512e9` | 2026-10-14 | 20 | 60 (65 distinct union) |
| `5454b4d33` (DX3 batch 1) | 2026-10-16 | 15 | 60 (every rendered row in window) |
| `15e917580`..`6d248c3e2` | 2026-10-16..18 | 10 | 57 |
| `2280fd149` (filing) | 2026-10-23 | 10 (9 rust-lane rows at 2026-12-31 + HISS-11) | 33 |

The filing-time count brackets 69 (65-80 across the days it was drafted) and
moves as the re-review batches land: the 9 rust-lane entries at 2026-12-31
were deleted when ADR-3380's tidy rust lane measured them, the DX3 batches
renewed the SPDX/black/ruff/ffmpeg-fallback cohorts out of the window, and
the praetor re-render of 2026-10-19 (`5d8a75f6f`) cut the rendered layer. The
issue's number is a point-in-time headline; its completion criterion — no
exception expires before 2027-01-31 without a recorded decision — is what
this change satisfies, and the table above is the "list the entries by expiry
date and rule" deliverable the issue asks for, on the tree the change builds
against.

### Renewal mechanics (house pattern, PR #3522)

Each renewed entry: `expires = 2027-03-31`, `entered = 2026-11-28`, category
unchanged (A), and a renewal sentence appended to the reason in the #3522
form — `Renewed 2026-11-28 (issue #2944) from category A (entered <old>,
expiry was <old>): <why it still cannot be fixed here>.` — keeping any `Ends
when ...` clause last. 2027-03-31 is 123 days out, inside `MAX_DAYS = 400`;
after `CATEGORY_CUTOFF` (2026-11-28) a category is required, and all five
carry A. The three clang-tidy-coverage renewals do not change
`check-tidy-coverage.py`'s verdict (the entries stay live and the files stay
unmeasured); the HISS-10/HISS-11 renewals re-render through the renewed cap
into `.standards.yaml` (16 rows at 2027-02-26, 2 at 2027-02-12).

## Reproduce

From the repository root, against the working tree of this change:

```bash
python3 scripts/ci/lint_exceptions.py check                 # the declared list: 117 entries, 0 findings
python3 scripts/ci/praetor_tidy_coverage.py --check         # the rendered block matches the list + renewed cap
python3 scripts/ci/check-tidy-coverage.py                   # every tracked unit is read or excepted
python3 scripts/ci/tests/test_praetor_tidy_coverage.py      # 14 tests, cap-date literals moved with the cap
python3 scripts/ci/tests/test_check_tidy_coverage.py        # 14 tests
python3 - <<'PY'
import re, pathlib
root = pathlib.Path(".")
pat = re.compile(r'(?:expires|expired_at|ignoreUntil)\s*[=:]\s*"?(2026-12-31|2027-01-0[0-5])')
hits = [str(p) for p in root.rglob("*") if p.is_file() and p.suffix in {".toml", ".yaml", ".json"}
        and ".git" not in p.parts and pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
print("enforced dates left in the window:", hits or "none outside test fixtures")
PY
```

The sweep after this change reports no registry row at all (the issue's
window dates survive only as string literals inside
`scripts/ci/tests/test_lint_exceptions.py`'s synthetic fixtures, which
enforce nothing).

## Alternatives explored

- **Fix at source by re-measuring the tidy baseline for entries 3-5.**
  Refused: `measured_sources` is the recorded output of a clang-tidy lane run
  over a compile database; no lane on master has measured these files
  (verified), and faking the baseline or hand-adding entries would lie to the
  gate the baselines exist to constrain. Renewal is the honest record.
- **Fix HISS-10 by patching the vendored Netflix tree.** Refused by the
  exception's own terms: the tree is a read-only reference at an upstream
  parity pin.
- **Renew everything to 2027-03-31 including the cap.** Refused: praetor
  refuses a category-A exception more than 90 days out, so a cap of
  2027-03-31 would fail the audit from the first CI run until 2026-12-31. The
  cap goes to 2026-11-28 + 90 days = 2027-02-26; the list entries may hold
  the longer date because the render, not the list, is what praetor reads.
- **Leave the cap at 2027-01-04 and renew only the list.** Refused: the 18
  rendered rows would still expire on 2027-01-04 and fail praetor's audit
  like missing exceptions — the exact failure mode the issue exists to
  prevent.

## Open questions

- The exact tree the issue's "69" was counted on is not recoverable from git
  (see the table above); if the maintainer has the snapshot, the ledger table
  can be reconciled against it in a follow-up.
- `test_lint_exceptions.py`'s `RepositoryListTests` hardcode
  `PATH=/usr/bin:/bin:/usr/local/bin` and fail on Windows with `git not found
  on PATH` (pre-existing on pristine master, two tests; POSIX-only fixture,
  not touched here).

## Related

- ADRs: [ADR-1142](../adr/1142-whole-codebase-standards.md),
  [ADR-2197](../adr/2197-render-generated-docs-at-landing.md)
- Issues: #2944
- PRs: #3522 (renewal house pattern), #3414, #3452, #3500 (entries touched by
  earlier renewals)
