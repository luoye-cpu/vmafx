- **Renovate groups both uv pins, and the lock check rejects one-sided updates.**
  `test_checked_in_config_owns_uv_pins` checks that Renovate groups the
  `tooling-tests.in` pin with the lock manifest’s `uv_version`. One-sided rejection is covered by
  `test_check_rejects_manifest_only_bump` and `test_check_rejects_tooling_input_only_bump`.
  The refresh job runs `bootstrap-uv` with the old uv to prepare the reviewed uv before
  regenerating the locks (`test_stale_tooling_lock_regenerates_after_bootstrap`).
  `test_checked_in_workflow_has_the_required_shape` checks the step order.
