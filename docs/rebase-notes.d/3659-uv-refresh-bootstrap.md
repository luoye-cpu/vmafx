## The lock refresh generator bootstrap (2026-11-28)

Rebases against master must keep three invariants this change pins, and
coordinate with our open PR #3671 (issue #3656), which touches several of the
same files.

Invariants (each enforced by a named test):

- The refresh workflow's install step must run
  `check_python_dependency_locks.py uv-bootstrap` before the write step, and no
  `run:` step of `.github/workflows/python-lock-refresh.yml` may read
  `requirements/locks/tooling-tests.txt` — reinstalling uv from the compiled
  tooling lock is what re-creates the bootstrap deadlock
  (`test_bootstrap_installs_the_reviewed_generator_from_the_manifest` and the
  `compiled tooling lock used in a run step` shape finding in
  `scripts/ci/tests/test_python_lock_refresh_workflow.py`).
- `requirements/locks/manifest.json` must keep the non-empty, unique,
  lowercase-hex `uv_sha256` list directly next to `uv_version`;
  `parse_manifest` refuses anything else, and `manifest_uv_version_only` in
  `scripts/ci/check-dco.py` ignores exactly those two keys when it authorizes a
  refresh commit's manifest diff
  (`test_positive_squash_with_uv_version_and_its_digests_changed_passes`,
  `test_squash_changing_only_the_uv_digests_is_refused`).
- `write` keeps refusing a generator whose version differs from the manifest —
  the fix moves where the generator is installed from, not the review gate
  (`test_write_still_refuses_a_generator_at_the_stale_lock_version`).

Coordination with PR #3671: it edits the publish step of the same workflow
(adds `.pre-commit-config.yaml` as a publish output), adds the hook-pin sync to
`check_python_dependency_locks.py` (`sync_config_hook_pins`, called after the
lock regeneration loop inside `write`), extends `python_lock_landed_exemption`
in `check-dco.py` with `precommit_hook_pins_only`, edits the ADR-3560 body
under an ADR-Body-Edit trailer, and grows the same two test files. The two
changes compose — bootstrap installs the manifest generator, `write`
regenerates the locks, the hook-pin sync reads the fresh tooling lock — but a
rebase that drops either half leaves the other referencing stale anchors: keep
#3671's `sync_config_hook_pins` call after the regeneration loop, and keep the
`uv_sha256` pop in `manifest_uv_version_only` alongside #3671's
`.pre-commit-config.yaml` grant handling.

The refreshed uv digests live in the manifest, so the next uv bump must move
`uv_version` and `uv_sha256` together (the digest list of the new release is
the uv block of the freshly compiled `tooling-tests.txt`); a manifest that
bumps only `uv_version` still parses and still passes the DCO exemption, but
the bootstrap install fails closed at the hash check.
