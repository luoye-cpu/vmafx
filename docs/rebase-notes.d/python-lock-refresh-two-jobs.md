## Python lock refresh split into two jobs (2026-11-28)

- The workflow is stacked on #3660 (`ci/renovate-uv-version-group`): land it
  after that pull request, or rebase it onto the updated branch. Conflicts land
  in the push step of `.github/workflows/python-lock-refresh.yml` and in
  `guard_order_findings` of `scripts/ci/tests/test_python_lock_refresh_workflow.py`,
  which now walks the `regenerate` and `publish-locks` jobs in `needs` order with
  the added `stage` and `restore` markers.
- **On a manifest change**: the allow-list of the publish job is the checker's
  manifest outputs — not a `requirements/locks/` prefix; 14 of the 30 outputs
  live outside that directory. A new output still needs an operator DCO grant
  (`.config/lint-exceptions.d/dco-python-lock-refresh.toml`) and flows through
  `stage`/`restore` unchanged.
