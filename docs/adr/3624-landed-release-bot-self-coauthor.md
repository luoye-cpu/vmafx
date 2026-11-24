# ADR-3624: Strip the release bot's own co-author line on landed commits

- **Status**: Accepted
- **Date**: 2026-11-22
- **Deciders**: Operator
- **Tags**: `ci`

## Context

Master commit `800122efd8` (PR #3613, the docs render commit) is authored by
`vmafx-release-bot[bot]` and carries exactly one `Co-authored-by:` trailer naming
that same bot. `scripts/ci/check-dco.py --landed` refuses it, so the workflow
`.github/workflows/landed-commit-messages.yml` is red on master (run 45030816200).
GitHub wrote that squash commit when #3613 landed through the merge queue, the
first render pull request to do so; the render commits before it on master
(#3528 to #3611) carry no `Co-authored-by:` line. The message of `800122efd8`:

```text
chore(docs): render generated changelog and ADR index (#3613)

Signed-off-by: vmafx-release-bot[bot] <340976456+vmafx-release-bot[bot]@users.noreply.github.com>
Co-authored-by: vmafx-release-bot[bot] <340976456+vmafx-release-bot[bot]@users.noreply.github.com>
```

## Decision

In `--landed` mode, when `(commit.author_name, commit.author_email.lower())`
matches the release bot identity, strip one line whose `.strip()` equals
`RELEASE_BOT_COAUTHOR` only when that line appears exactly once; zero or more
than one occurrence keeps the message unchanged for attribution. No path or
exception lookup applies. A second self line and a bot or coding-tool co-author
on a release-bot commit (a line matching `BANNED_COAUTHOR_TOKENS`, `[bot]`
included) stay refused; a human co-author line passes, as on any other commit.
The release-bot line on a commit by another author stays refused unless a
Renovate exemption applies: the ADR-3555 path grant or the ADR-3560 Python lock
refresh.

## Alternatives considered

| Alternative | Benefit | Cost |
| --- | --- | --- |
| Strip the self line for the release-bot author only (chosen) | Accepts the landed render commit without widening other identities | One more trailer shape in `landed_exempt_message` |
| Change squash landing so it never adds the self line | No DCO change | Which landing step adds the line is not established; only `800122efd8` shows it, and its committer is GitHub |
| Add render paths to the `dco-landed-release-bot-coauthor` exception file | Reuses path grants | Path grants expire and the render commit touches many generated paths |

## Consequences

**Positive:** `.github/workflows/landed-commit-messages.yml` accepts any landed
commit authored by `vmafx-release-bot[bot]` with one exact self
`Co-authored-by:` line, such as docs render commit `800122efd8`, without
operator intervention.

**Negative:** Landed mode recognises one more stripped `Co-authored-by:` trailer
shape.

**Neutral:** Renovate and Python lock refresh landed behaviour is unchanged.

## References

- [ADR-3389](3389-landed-commit-message-gate.md): landed commit message gate.
- [ADR-3555](3555-intel-neo-lock-file.md): Renovate landed co-author exemption.
- [ADR-3560](3560-python-lock-refresh.md): Python lock refresh landed exemption.
- `scripts/ci/check-dco.py`: `landed_exempt_message`, `RELEASE_BOT_COAUTHOR`.
