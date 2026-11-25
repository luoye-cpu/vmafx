- **Python tests can remove scratch `.git` directories on Windows.**
  `remove_file()` and `remove_tree()` keep the first refusal with retry and later
  errors as notes and retry once with a non-following chmod only for a read-only,
  non-link, non-reparse directory or single-link regular file, with tree cleanup
  checking both the resolved parent before chmod (it must be inside the resolved
  root, though an ancestor swap after the check goes undetected) and the root
  after deletion (`path_absent()` treats only `FileNotFoundError` as absence and
  counts a dangling link as present).
