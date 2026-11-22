<!-- markdownlint-disable MD013 MD025 MD060 -->
# ADR-3610: Admit fork pull requests to the merge queue at the reviewed head

- **Status**: Accepted; supersedes in part [ADR-3556](3556-merge-queue-merge-group.md) (Decision bullet 1, queue admission)
- **Date**: 2026-11-22
- **Deciders**: Project maintainer
- **Tags**: ci, fork-local

## Context

[ADR-3556](3556-merge-queue-merge-group.md) wired `Required Checks Aggregator` and its feeding workflows onto `merge_group` for the `master` merge queue while restricting queue admission to pull requests whose head branch lives in `vidiko/vmafx`. Under that interim rule, `Train.guard` in `scripts/dev/merge_train_guard.py` refused every cross-repository pull request (`merge_train_guard.py:196`), so a fork contribution could enter the queue only if a maintainer re-pushed it onto an in-repository branch or held it outside the queue.

Direct queue admission for a fork pull request is safe only when the queued commit is pinned to the exact SHA a listed maintainer reviewed and approved, with `CLEAN` merge state, passing required checks, and a valid squash commit message, and when no pending auto-merge request can enqueue a later unreviewed push on that pull request.

## Decision

Admit fork pull requests directly to the `master` merge queue only at the exact head a listed maintainer approved, keeping same-repository merge-train rules unchanged:

- Fork pull requests enter the queue only after `python3 scripts/dev/merge_train_guard.py admit-fork <n> --reviewed-head <sha> --state-dir <dir>` returns `0`, and only through `gh pr merge <n> --match-head-commit <sha>` (or `expectedHeadOid` on the GraphQL `enqueuePullRequest` mutation) pinned to that same head.
- Do not arm GitHub auto-merge (`--auto`) on fork pull requests: a pending enqueue could land a later head pushed after the admission verdict without a fresh review and verdict.
- Keep `PAUSED`, `hold.txt`, and the release pull request (#1213) holds in force on `admit-fork`.
- Keep `Train.guard` and every merge-train action (`inspect`, `cycle`, `promote`, `rebase`, `arm`, `merge`) refusing cross-repository pull requests (`cross-repository PRs require an explicit owner`); `admit-fork` is read-only, refuses `--apply`, never calls `gh pr merge`, never pushes, and never writes state.
- Declare eligible maintainer GitHub logins in `policy.json` under `fork_reviewers`. Among decisive reviews (`APPROVED`, `CHANGES_REQUESTED`, `DISMISSED`; `COMMENTED` and `PENDING` are ignored), take the latest review by `submittedAt` for each login in `fork_reviewers`: at least one must be `APPROVED` with `commit.oid == <sha>`, and none may be `CHANGES_REQUESTED`. Dismissed approvals and approvals from logins outside `fork_reviewers` never count.
- Run `python3 scripts/dev/merge_train_guard.py queue-audit --state-dir <dir>` after enqueueing to re-check every cross-repository entry in `mergeQueue(branch: "master")` against its live `headRefOid` and the `fork_reviewers` approval rule; the operator removes any failing entry from the queue at once.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Option a: admit fork pull requests at the reviewed head after `merge_train_guard.py admit-fork` returns `0`, enqueue with `--match-head-commit` and no auto-merge, and re-check with `queue-audit` | Preserves contributor pull requests, attribution, and review threads; binds admission to a maintainer-approved commit SHA, `CLEAN` merge state, green required checks, and squash message validation | Requires explicit operator enqueue without `--auto` plus post-enqueue `queue-audit` | Chosen; admits fork pull requests directly while keeping exact-head review binding and existing `merge_group` barriers |
| Option b: maintainers re-push fork changes to an in-repository branch or open replacement pull requests | Keeps every `merge_group` ref restricted to in-repository branches | Closes or supersedes contributor work, splits review history, and adds manual branch duplication | Rejected: closes or supersedes contributor work |
| Option c: hold every fork pull request outside the merge queue | Requires zero gateway or documentation changes | Blocks external contributors indefinitely once `master` requires the merge queue | Rejected as a standing rule; it was the interim until this change landed |

## Consequences

- **Positive**: External fork pull requests can enter the `master` merge queue directly without replacement branches, gated by exact-head maintainer approval in `fork_reviewers`, `CLEAN` merge state, passing required checks, and squash commit message validation (`scripts/dev/tests/test_merge_train_guard.py`).
- **Security boundary**: A merge group can now carry a fork diff. The provenance guard in `.github/actions/runner-probe/action.yml` (`scripts/ci/merge_group_provenance.py`) writes `same_repo=false` for such a group and hardware probes do not run, so the second barrier of [ADR-3556](3556-merge-queue-merge-group.md) stays in place unchanged; the effect on hardware-gated contexts is recorded from the first live fork group. GitHub documents removal from the queue on failing checks, timeout, user removal and unresolvable branch protection failure (docs page "Merging a pull request with a merge queue", section "Understanding why your pull request was removed from the merge queue"); removal on a head push is not stated there, which is why `queue-audit` exists as a second check.
- **Negative**: Fork pull requests cannot use `--auto` arming; any new push to the fork branch invalidates the reviewed head and requires a fresh maintainer approval on the new commit before `admit-fork` can succeed again.
- **Neutral / follow-ups**: In-repository pull requests continue to use `Train.guard` and the existing merge-train actions unchanged ([ADR-1244](1244-merge-train-ownership-and-validation.md)).

## References

- [ADR-3556: Trigger required checks and aggregator on merge-queue merge_group events](3556-merge-queue-merge-group.md).
- [ADR-1244: Guard merge-train ownership and exact-head validation](1244-merge-train-ownership-and-validation.md).
- [Local merge-train control](../development/merge-train.md).
- [CI overview: Merge queue](../development/ci.md#merge-queue).
- Source: maintainer decision (paraphrased; vmafx-queue-fork-pr-admission, 2026-11-22, fork queue admission): admit a fork pull request only pinned to the exact reviewed head with a recorded review on that head and all required checks green; keep the provenance guard and every hold.
