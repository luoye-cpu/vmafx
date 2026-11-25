The Python lock writer now moves every exact pin of a
`requirements/locks/tooling-tests.txt` package in a Python hook's
`additional_dependencies` to the lock version, so a lock refresh cannot leave
`.pre-commit-config.yaml` stale. A non-exact pin of a lock-tied package is
refused instead of guessed, the write mode publishes a changed config through
the lock-refresh workflow, and the Renovate skip rule, the writer list and the
checked-in pins are kept equal by tests (issue #3656).
