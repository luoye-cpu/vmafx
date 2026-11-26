---
paths:
  - scripts/ci/check_python_dependency_locks.py
  - scripts/ci/tests/test_python_dependency_locks.py
  - scripts/ci/check_python_floors.py
  - scripts/ci/tests/test_python_floors.py
  - requirements/locks/*
invariant: Every lock registered in `manifest.json`; installs hash-pinned, exact-path bound; scanners fail closed.
area: release
---
<!-- markdownlint-disable MD013 MD060 -->
# Hash-locked Python dependency policy (ADR-1305)

`check_python_dependency_locks.py` enforces cryptographic pinning and hermetic
install policy across repository:

- Generate all lock files via `requirements/locks/manifest.json` using reviewed `uv_version` (manifest field; no copy here).
- Manifest outputs and inputs require local repository-relative paths on both POSIX and Windows (rejecting directory traversal `..`, drive/UNC or POSIX absolute paths, remote URLs, and surrounding whitespace); reject duplicate inputs.
- Manifest `compile_args` forbid output overrides (`-o`, `--output-file`).
- Register every `*-lock.txt` and `requirements/locks/*.txt` in `manifest.json`.
- Require exact match between requirement target and manifest output or explicit `install_aliases` entry bound to specific repo-relative consumer path and context. Consumer bindings require local repository-relative paths on both POSIX and Windows. Identity uses exact separator-normalized equality, so nested suffix lookalike never inherits authority. Aliases reject directory traversals (`..`), Windows drive/UNC paths, duplicate JSON keys, and unreferenced/dead aliases fail closed. No authority from basename or suffix matches.
- Executable pip parsing preserves global pre-subcommand flags (`--trusted-host`, etc.), splits and validates joined short forms (`-qrfoo`, `-rmalicious.txt`, `-cconstraints.txt`), and rejects unhashed or secondary requirement/constraint flags.
- Nox AST scanner restricts receiver authority to `@nox.session` parameters, tracks and rejects plain or annotated session/method aliases (`installer = session.install; installer(...)`, `alias: object = session`, `installer: object = session.install`) and literal `getattr(session, "install")` aliases, and inspects literal shell runner invocations (`session.run("sh", "-c", ...)`) fail-closed.
- Workstation-portable Nox development locks (compiled with `--universal`, no `--python-platform`), and pin each Nox session to explicit Python version matching lock resolution.
- Git discovery and consumer tracking fail closed with `ContractError` on any process or filesystem error.
- Workflow steps that consume repo-local requirements locks, packages, helper scripts, or local actions execute strictly after `actions/checkout` in that job; `check_python_dependency_locks.py` fail-closed scanner (`scan_workflow_checkout_ordering`) enforces this ordering across all workflows in `.github/workflows/*.yml`. validator enforces fail-closed semantics across both PyYAML and fallback parsing paths: accept only exact `actions/checkout` owner and action pinned to full 40-character hex SHA (rejecting spoofed owners, altered action names, or unpinned refs); rejects foreign `with.repository` checkouts; rejects conditional (`if:`) and `continue-on-error` checkouts as insufficient; rejects checkouts with `with.path` targeting subdirectory; preserves folded YAML run blocks (`>`) where flags such as `-r` and local paths split across physical lines; detects joined pip options (`-rrequirements/...`, `-eai`) and local source targets (`--no-build-isolation ai`); fails closed on malformed YAML with PyYAML installed; and, without PyYAML, accepts simple quoted or unquoted block keys for `jobs`, job ids, and `steps` while rejecting unsupported inline/flow-style mappings rather than treating unparsed workflow as empty.
- Non-editable local install = `.whl` only with `--no-deps`; plain directory and sdist (`.tar.gz`/`.zip`) refused (Scorecard Pinned-Dependencies: plain directory install unpinned without `-e` or wheel).
- uv pin visibility + grouping: `test_checked_in_config_owns_uv_pins`. `minimumGroupSize` threshold (`test_uv_group_requires_both_updates`) postpones branch creation. Single-pin mismatch rejection: `check` (`test_check_rejects_manifest_only_bump`, `test_check_rejects_tooling_input_only_bump`), `write` (`test_write_rejects_mismatch_before_running_uv`), `bootstrap-uv` (`test_mismatched_input_refused_before_uv_runs`).
- `write` regenerates tracked locks. `bootstrap-uv --output FILE` compiles temporary generator lock on installed/reviewed uv version mismatch (`test_stale_tooling_lock_regenerates_after_bootstrap`). Refresh workflow installs temporary lock immediately after generation (`test_checked_in_workflow_has_the_required_shape`). Both modes permit network access. `check` runs offline in pre-commit and `make lint`.
- Regression coverage in `scripts/ci/tests/test_python_dependency_locks.py`.
- Declared `requires-python` floors in tracked `pyproject.toml` files move only with `scripts/ci/check_python_floors.py` green (`make python-floors-check`; ADR-3335).
