---
paths:
  - .config/lane-brief.yaml
  - scripts/ci/tests/test_lane_brief_config.py
invariant: `.config/lane-brief.yaml` stays in ADR-3553 reader limits; new line needs incident comment + replay test.
area: gates
---
<!-- markdownlint-disable MD013 MD060 -->
# Lane-brief configuration contract (ADR-3553)

Lane launcher (`agy-run.sh` in praetor repository) reads
`.config/lane-brief.yaml` from job worktree via reader `brief_stage.py`
(praetor repository) before starting job
([ADR-3553](../../../docs/adr/3553-lane-brief-review-clause.md)):

- top-level keys in `.config/lane-brief.yaml`: `required` (every brief),
  `required_by_stage` (`impl`, `fix`, `review`, `audit`, `research`),
  `credential_stub_tests`;
- list bounds in `.config/lane-brief.yaml` and
  `scripts/ci/tests/test_lane_brief_config.py`: at most 32 strings of 1 to 200
  characters per list;
- stage resolution in `brief_stage.py`: first `task:` line in first 20 brief
  lines, mapped through router task labels;
- matching and exit codes in `brief_stage.py`: plain substring match on brief
  text; `rc 1` lists each missing line; `rc 2` = invalid
  `.config/lane-brief.yaml` or unresolved brief stage (missing `task:` line or
  unknown label).

Every new required line in `.config/lane-brief.yaml` carries comment naming
prevented incident and lands with replay case in
`scripts/ci/tests/test_lane_brief_config.py` (brief without line -> `rc 1`,
brief with line -> `rc 0`). `scripts/ci/tests/test_lane_brief_config.py` checks
shape and `tooling` suite routing on every run, and replays planted briefs
against `brief_stage.py` via `LANE_BRIEF_STAGE_PY` or `LANE_TOOLS_DIR` (missing
named file -> test failure, never skip).
