# Research digest: the lock refresh generator bootstrap (issue #3659)

## Question

The refresh workflow (ADR-3560) regenerates every manifest lock on a
`renovate/**` branch with `check_python_dependency_locks.py write`. Its
bootstrap step installed the pinned uv from the compiled
`requirements/locks/tooling-tests.txt` — but on a uv bump that lock still
names the previous release until the very same job regenerates it, so the
writer refused (`uv <old> does not match reviewed generator <new>`,
`scripts/ci/check_python_dependency_locks.py` around the
`uv_version` comparison in `write`). The refresh could not produce the lock
it needed to run: a bootstrap deadlock. Which reviewed source should the
generator install from?

## Measurements

- On the last real uv bump (#3042, "regenerate Python locks with uv
  0.13.6"), Renovate moved the `tooling-tests.in` pin to 0.13.6 and the
  maintainer had to push a follow-up commit that moved `uv_version` and
  rewrote every lock by hand on a workstation — exactly the work the refresh
  workflow exists to automate.
- The `uv==0.13.6` block of the compiled `tooling-tests.txt` carries 19 sha256
  artifact digests; the manifest carries none. The install-scan policy
  (`scan_install_commands`) accepts `pip install --require-hashes -r <path>`
  only when `<path>` is a manifest lock output or a bound alias, so a
  runtime-generated requirements file under `$RUNNER_TEMP` cannot be installed
  from a workflow `run:` step without loosening the scanner.
- Python scripts are not scanned consumer surfaces (`tracked_consumer_paths`
  covers `.sh`, `.ps1`, workflow YAML, container recipes, `Makefile`,
  `noxfile.py`), and the checker already runs networked subprocesses in
  `write` mode through `scripts/lib/safe_subprocess`.
- The checker's workflow parser has an offline fallback for missing PyYAML;
  with `yaml` hidden, `_load_workflow_jobs` parses all 55 workflow files of
  master, so dropping the tooling-lock install (which supplied PyYAML) keeps
  `check` usable in the refresh job.
- The DCO landed exemption (ADR-3560,
  `manifest_uv_version_only` in `scripts/ci/check-dco.py`) authorizes a
  manifest diff that changes only `uv_version`; a digest pin added next to it
  has to move with the release or the refresh commit loses the co-author line.

## Decision

Approach A: the manifest itself pins the generator. `uv_sha256` sits next to
`uv_version` and holds the reviewed release's artifact digests; a new
`check_python_dependency_locks.py uv-bootstrap` command writes the pinned
requirement to a temp file and installs it with
`pip install --require-hashes`, so the generator install is reviewed data
(the manifest), executed by reviewed code (the checker), and independent of
every compiled lock. The workflow's install step keeps only
`package-build.txt` and the new command; no run step reads the compiled
tooling lock any more, which `test_python_lock_refresh_workflow.py` now
refuses to see.

## Alternatives considered

| Option | Why not |
|---|---|
| Approach B: a dedicated small generator lock (`uv-generator.in`/`.txt`) moved in the same Renovate group, refresh installs uv from it | Renovate ignores `requirements/locks/*.txt` (`ignorePaths`), so the compiled generator lock is stale in exactly the bump window the issue describes — the deadlock returns; making it a manifest output adds a refresh output, making it hand-curated adds an unverified surface. |
| Emit the manifest pin as a requirements file and `pip install -r` it in the workflow step | The install scanner only accepts `-r` targets that are manifest lock outputs/aliases; a `$RUNNER_TEMP` file needs a scanner carve-out keyed on a runtime file name — fragile string matching of shell plumbing. |
| Keep installing `tooling-tests.txt` and upgrade uv from the manifest afterwards | The bootstrap still depends on the stale compiled lock installing cleanly, keeps the double uv download, and contradicts the reviewed-source rule the workflow's own header states. |
| Install the uv standalone binary tarball from GitHub releases with a single digest | Moves the generator from the PyPI wheel set the locks hash to a second distribution channel, changes PATH handling, and pins one artifact instead of the reviewed set pip may resolve per platform. |

## Evidence

- `UvBootstrapTests` in `scripts/ci/tests/test_python_dependency_locks.py`
  builds the bump state — manifest `uv_version` 0.14.0, `tooling-tests.in`
  pinning 0.14.0, compiled `tooling-tests.txt` still stamping 0.13.6 — and
  proves the bootstrap installs `uv==0.14.0` with the manifest digests, that
  `write` with that generator regenerates the stale lock (fresh stamp), and
  that a generator left at the stale version still refuses (fail closed).
- `test_bootstrap_installs_the_reviewed_generator_from_the_manifest` in
  `scripts/ci/tests/test_python_lock_refresh_workflow.py` pins the workflow
  shape: `uv-bootstrap` runs before `write`, and any run step touching
  `requirements/locks/tooling-tests.txt` is reported as a shape finding.
- `test_positive_squash_with_uv_version_and_its_digests_changed_passes` and
  `test_squash_changing_only_the_uv_digests_is_refused` in
  `scripts/ci/tests/test_check_dco_python_lock_refresh.py` pin the extended
  landed exemption.
