- **Python tests can remove scratch `.git` directories on Windows.**
  `remove_tree()` retries deletion of read-only Git objects and `remove_file()`
  handles single Git object files. Both helpers propagate a permission error on
  symlink deletion without changing permissions or retrying. The
  `ReadOnlyTreeRemoval` tests cover both helpers. The `SourceCacheReset` tests
  check that the FFmpeg patch-stack cache is reinitialized only after its old
  root is gone.
