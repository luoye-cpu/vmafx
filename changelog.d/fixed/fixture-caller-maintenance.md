- Tests: caller fixtures in `scripts/ci/test_git_fixture_isolation.py` now
  disable git's automatic maintenance before their baseline commit and fail
  closed when that commit starts background work. Previously, a baseline commit
  in the `exercise` or linked-worktree hook caller repository could spawn a
  detached `git maintenance run --auto` whose lock or repack raced the caller
  snapshot. `commit_caller_baseline()` now sets `maintenance.auto=false` and
  `gc.auto=0` before committing and traces that commit with `GIT_TRACE=1`.
