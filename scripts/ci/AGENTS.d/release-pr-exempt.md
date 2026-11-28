---
paths:
  - scripts/ci/release-pr-exempt.sh
  - .github/actions/release-pr-exempt/action.yml
  - scripts/ci/tests/test-release-pr-exempt.sh
invariant: Exempt = `release-please--` head ref AND bot author; always exits 0; only four authoring gates consult it.
area: release
---
<!-- markdownlint-disable MD013 MD060 -->
# release-pr-exempt.sh invariants (ADR-1151)

- Predicate = `release-please--` head ref **AND** bot author. Never relax
  it to head-ref-only: four gates it disarms = required contexts, so
  head-ref-only test would let anyone skip them by naming branch
  `release-please--anything`.
- Always exits 0, communicates through `exempt=true|false`. Gate
  consuming it must use step-level `if:` so job still **reports**.
  Skipping whole job makes check *absent*; since ADR-3293 absent owed
  check fails aggregator.
- Only four authoring-discipline gates may consult it: Deliverables
  Checklist, Doc-Substance Gate, `docs/state.md` Gate, FFmpeg-Patches Surface
  Sync. local pre-push mirror of Deliverables Checklist
  (`scripts/git-hooks/pre-push-pr-body-lint.sh`) consults it too, so pushing
  release-branch changelog cut is not blocked by gate CI would skip.
  `Release Script Contract` and `ADR Collision Guard` stay armed on
  release PRs. Former = gate proving cut ran; also runs
  `tests/test-release-pr-exempt.sh`, so exemption's own test can never
  be skipped by exemption.
- New gate added to aggregator's `required` array must be checked against
  release PR's shape (`.release-please-manifest.json` + coordinated
  version-marker diff, and rendered-changelog body) before promoted.
- The six gates in `rule-enforcement.yml` consume the script through the
  composite action `.github/actions/release-pr-exempt` (step id `release_pr`,
  output `exempt`) — audit C2-12, #3078. A new gate keeps the step shape
  (`- name: Detect a machine-generated release PR` + `id: release_pr` +
  `uses:`) rather than re-inlining the script call; `check_composite_actions.py`
  lints the action, and the step-level `if:` rule above still applies.
