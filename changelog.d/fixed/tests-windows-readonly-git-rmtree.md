- **Python tests can remove read-only scratch Git objects on Windows.**
  On Windows only, cleanup retries a refused deletion once for read-only directories
  and single-link regular files. The caller must own and have created the tree and
  keep it free of concurrent writers. Links, reparse points and hard-linked objects
  keep their refusal. After the walk, `remove_tree` raises the first unrecovered
  error received by the `onexc` hook, preserving its filename and adding bounded
  notes. If the walk reports no error but the root remains, `remove_tree` raises
  `ENOTEMPTY` with the root as its filename.
  Before deleting anything, cache removal refuses trees with more than 65,536
  entries and roots that are symlinks or junctions.
