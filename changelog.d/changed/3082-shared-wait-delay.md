- Subprocess call sites now share `internal/executil.SubprocessWaitDelay`
  (two seconds) and `ShortSubprocessWaitDelay` (one second) instead of
  repeating inline `time.Second` values.
