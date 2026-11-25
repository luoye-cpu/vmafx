<!-- markdownlint-disable MD013 MD060 -->
# ADR-3627: Check the render in the merge group

- **Status**: Accepted
- **Date**: 2026-11-23
- **Deciders**: Project maintainer
- **Tags**: `ci`, `docs`, `release`

## Context

Render pull request 3632 generated the changelog on base `a9d4887dc3`. Pull
request 3575 landed first as `7990dc01b2` and added a changelog fragment. The
merge queue built the group for 3632 on `7990dc01b2`
(`gh-readonly-queue/master/pr-3632-7990dc01b2`), and the render landed on
`master` as `7d30d685c9` without that fragment. The Docs job checked the
render only on the `master` push, where it failed on the render commit's own
tree. The render opener still waited for the six-hour threshold when a later
landing left a render owed.

## Decision

On `merge_group`, the Docs job fetches full history and passes the event base
SHA through the environment to `check_render_on_master.py --group-base`.
The check finds the last first-parent commit that touches a rendered path in
`<base-sha>..HEAD` and runs `make docs-render-check` on that commit's own tree.
The base must be a full commit ID that is an ancestor of the group head,
otherwise the check fails. A group without a render passes. An inexact render
fails with its commit ID and instructions to re-render after the preceding
changes land. The age bound applies to the master push check. The render
opener treats an inexact last render as due immediately when the master tip
needs a render.

## Alternatives considered

| Option | Pros | Cons | Why discarded |
| --- | --- | --- | --- |
| Check only on master pushes | Keeps the merge-group checkout shallow. | A render checked at its PR head can land with missing fragments. | The queue must catch the stale render before it reaches master. |
| Check the merge-group head directly | One check at the candidate tip. | A valid render followed by a fragment-only landing would fail. | Fragment-only landings can owe a later render under ADR-3295. |

## Consequences

- The queue rejects a render that missed fragments from earlier in the group.
- Fragment-only groups can still land before their render. A fragment landing
  after an exact render in the group starts the normal wait on master.
- The Docs job fetches full history for merge groups and checks a detached
  tree when a render is present. The opener also checks the last render's tree
  when a repair would otherwise wait for the threshold.

## References

- [ADR-3295](3295-render-only-pull-request.md), [ADR-3373](3373-scheduled-render-pull-request.md), and [ADR-3556](3556-merge-queue-merge-group.md).
- [Release guide](../development/release.md#rendered-at-landing-not-in-the-pull-request).
- `scripts/ci/tests/test_check_render_on_master.py` covers an inexact queued render, exact renders, a fragment-only group, and immediate repair of an inexact render on master.
- Maintainer request (paraphrased, 2026-11-23, `vmafx-lesson-render-merge-group-20261123`). Check queued render commits in their group and open a repair immediately when the last render is inexact.
