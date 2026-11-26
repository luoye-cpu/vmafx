- **Renovate groups both uv pins, and the lock check rejects one-sided updates.**
  `test_checked_in_config_owns_uv_pins` checks that Renovate keeps both
  files visible and groups the `tooling-tests.in` pin with the lock
  manifest’s `uv_version`; `test_uv_group_requires_both_updates` checks
  the `minimumGroupSize` threshold. One-sided rejection is covered by
  `test_check_rejects_manifest_only_bump` and
  `test_check_rejects_tooling_input_only_bump` for `check`,
  `test_write_rejects_mismatch_before_running_uv` for `write`, and
  `test_mismatched_input_refused_before_uv_runs` for `bootstrap-uv`.
  The refresh job runs `bootstrap-uv` with the old uv to prepare the
  reviewed uv before regenerating the locks
  (`test_stale_tooling_lock_regenerates_after_bootstrap`).
  `test_checked_in_workflow_has_the_required_shape` checks the step order.
