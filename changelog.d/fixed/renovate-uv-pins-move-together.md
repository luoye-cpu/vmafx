- **Renovate groups both uv pins, and the lock check rejects one-sided updates.**
  `test_uv_group_requires_both_updates` checks that Renovate groups the
  `tooling-tests.in` pin with the lock manifest’s `uv_version`. The refresh job
  runs `bootstrap-uv` with the old uv to prepare the reviewed uv before
  regenerating the locks (`test_stale_tooling_lock_regenerates_after_bootstrap`).
  `test_checked_in_workflow_has_the_required_shape` checks the step order.
