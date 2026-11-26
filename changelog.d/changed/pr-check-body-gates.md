- **`make pr-check` runs all three `rule-enforcement.yml` PR-body shell gates.**
  `scripts/ci/pr-body-gates.sh` runs `deliverables-check.sh`,
  `state-md-touch-check.sh` and `ffmpeg-patches-surface-check.sh` without early
  stop so a PR body that passes `make pr-check` locally cannot fail the
  `docs/state.md` or `ffmpeg-patches` surface gates in CI. `PR=<num>` fetches
  the title, body and revisions in one `gh pr view` call; `BODY=<file>` accepts
  `TITLE=<text>` and defaults to the `HEAD` commit subject when `TITLE` is
  empty. `make pr-check PR=<num>` now checks the PR's own base and head
  revisions, fetching missing commits from `origin` and failing if they cannot
  be obtained. In PR mode, the FFmpeg surface gate's tree checks also read the
  PR head tree rather than the local checkout, reading raw blobs so archive
  attributes in the PR cannot hide or rewrite inputs; the FFmpeg surface gate
  reads path lists NUL-delimited so patch and doc names with non-ASCII
  characters are checked. `TITLE` and `BODY` are passed to the gates literally
  without `make` or shell expansion, and `PR` must be a positive decimal
  number.
