- **The Python lock refresh no longer bootstraps its generator from the lock it is
  about to rewrite.** On a uv bump the refresh workflow installed the pinned uv from
  the compiled `requirements/locks/tooling-tests.txt`, which still names the previous
  release until the refresh itself regenerates it, so `write` refused the mismatch
  (`uv <old> does not match reviewed generator <new>`) and the locks could not be
  refreshed (issue #3659). `requirements/locks/manifest.json` now pins the reviewed
  release's artifact digests next to `uv_version` (`uv_sha256`), and the workflow
  installs the generator with a new `check_python_dependency_locks.py uv-bootstrap`
  command that reads those pins alone. A manifest whose `uv_version` is newer than
  the compiled tooling lock regenerates the locks again.
