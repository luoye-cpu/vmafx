- **Python tests can remove read-only scratch Git objects on Windows.**
  On Windows only, cleanup retries a refused deletion once for read-only directories
  and single-link regular files. The caller must own and have created the tree and
  keep it free of concurrent writers. Links, reparse points and hard-linked objects
  keep their refusal. Tree cleanup finishes the walk and reports the first error
  with bounded notes, or verifies that the root is gone.
  Any filename in a removal error comes from the exception received by the
  `onexc` hook.
