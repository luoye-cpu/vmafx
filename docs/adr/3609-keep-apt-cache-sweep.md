<!-- markdownlint-disable MD013 MD060 -->
# ADR-3609: Keep the apt archive cache script instead of cache-apt-pkgs-action

- **Status**: Accepted
- **Date**: 2026-11-21
- **Deciders**: lusoris
- **Tags**: `ci`, `supply-chain`

## Context

A fleet reuse audit on 2026-11-21 listed awalsh128/cache-apt-pkgs-action
v1.6.3 as a possible replacement for the vmafx apt cache sweep, noting that
its licence was unclear.

At `master` commit `02d3a00876`, `scripts/ci/apt-install-cached.sh` is 97
lines and is called 39 times from 15 workflows. Each job restores and saves
`${RUNNER_TEMP}/apt-archives` with `actions/cache` keyed on its package list
`.github/apt/<job>.txt`. Its `refresh_apt_lists` function drops the runner
Microsoft and Azure apt sources and refreshes indices with
`apt-get --error-on=any -o Acquire::Retries=3 update`, so a failed index fetch
fails the step ([ADR-3537](3537-apt-update-fail-closed.md), pull request 3418).
The script restores only downloaded `.deb` archives and installs them with
`apt-get install`, so `dpkg` runs every install script.

## Decision

Keep `scripts/ci/apt-install-cached.sh` and do not adopt
awalsh128/cache-apt-pkgs-action v1.6.3, whose `update_apt_lists_if_stale`
helper in `lib.sh` skips `apt update` on GitHub runners and ignores errors
with `|| true` under `ACT=true`, skips `dpkg` install scripts by default, and
runs unsigned prebuilt binaries without build provenance.

Reconsider this decision when the action ships signed release binaries (or
builds them from source at use time) and fails the step on an index fetch
error.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Adopt `cache-apt-pkgs-action` v1.6.3 | Replaces the local caching script with a third-party workflow action. | In `lib.sh` (`update_apt_lists_if_stale`), it runs `apt update` only when `ACT` is `true` (`nektos/act`), not on GitHub runners, and ignores refresh errors; on a cache hit it restores packages as file sets without the `dpkg` database and without pre- or post-install scripts; it executes two prebuilt binaries committed to its repository (`apt_query-x86`, 3284030 bytes, and `apt_query-arm64`, 3233069 bytes) with no signature or build provenance, whereas vmafx pins every input and requires provenance (HISS-11, hermetic supply chain). | Fails open on stale or broken apt indices, omits `dpkg` database and install scripts on cache hits, and runs unsigned binaries without provenance. |
| Adopt `cache-apt-pkgs-action` v1.6.3 with `execute_install_scripts: true` | Attempts to run package install scripts on a cache hit. | Still skips and ignores apt index refresh errors on GitHub runners (`lib.sh` `update_apt_lists_if_stale`) and still executes the unsigned `apt_query-x86` and `apt_query-arm64` binaries without build provenance; its README section "Non-file Dependencies" states that `execute_install_scripts: true` is not guaranteed to work. | Retains the index-refresh and supply-chain defects while relying on an option that upstream documents as not guaranteed. |
| **Keep `scripts/ci/apt-install-cached.sh`** (chosen) | Fails the step on apt index fetch errors after dropping runner Microsoft and Azure sources, installs cached `.deb` archives via `apt-get install` so `dpkg` runs every install script, and runs no unverified binaries (HISS-11). | Keeps a 97-line shell script (at `02d3a00876`) called 39 times across 15 workflows alongside `actions/cache`. | — |

## Consequences

- **Positive**: Workflows preserve fail-closed apt index refreshes, complete
  `dpkg` database and install-script execution, and HISS-11 supply-chain
  provenance.
- **Negative**: The duplicate of a public caching action stays in
  `scripts/ci/apt-install-cached.sh` across 15 workflows.
- **Neutral / follow-ups**: At v1.6.3, the action's `LICENSE` file is the
  13-line `Apache-2.0` notice without the licence text, so GitHub reports the
  licence as `NOASSERTION`. `Apache-2.0` is on the vmafx allow list
  (`deny.toml`, `allow = [` block), so the licence is not the blocker and
  clarifying it is no reason to revisit. Reconsider only when the action ships
  signed release binaries (or builds them from source at use time) and fails
  the step on an index fetch error.

## References

- [ADR-3537](3537-apt-update-fail-closed.md) — Fail closed and retry on APT
  package index refreshes (pull request 3418).
- `scripts/ci/apt-install-cached.sh` — Apt archive cache and fail-closed index
  refresh helper.
- Third-party action evaluated: awalsh128/cache-apt-pkgs-action v1.6.3.
