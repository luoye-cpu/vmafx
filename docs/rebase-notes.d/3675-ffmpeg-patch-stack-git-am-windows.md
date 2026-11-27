## FFmpeg patch stack runs on Windows: LF fixture bytes, POSIX receipts, shareable cache paths (2026-11-28)

`fix/ffmpeg-patch-stack-git-am-windows`, issue #3675,
[research digest](../research/3675-ffmpeg-patch-stack-git-am-windows.md).

- `scripts/ci/test_ffmpeg_patch_stack.py`: `RealReplay.write_lf()` writes fixture
  text with `newline="\n"`; every fixture write that feeds a committed blob, the
  `git am --3way` patch file, or a file the bash helper sources goes through it
  (`RealReplay.setUp`'s `sample` pair and patch file, `pin_series`'s
  `build-config.env`, `tag_conflicting_release`'s `sample`,
  `test_latest_release_rebases_and_updates_all_mirrors`'s `other`,
  `SharedSeriesReplay.setUp`'s `shared-fix`,
  `test_new_ffmpeg_release_waits_for_a_series_release`'s `other`).
  `scripts/ci/ffmpeg_shared_series_fixture.py` writes `SHA256SUMS` as bytes.
  **On rebase**: keep the LF discipline when adding fixture files — a text-mode
  write on Windows stores CRLF in the fixture blobs and `ffmpeg-shared-series.sh`
  sources CR into every pinned value; `mailsplit` strips CR from the patch's
  context lines, so every replay test then fails with "patch does not apply".
  `UpstreamBlobLineEndings` documents the mechanism offline;
  `RealReplay.test_fixture_upstream_blobs_and_patch_are_lf` guards the invariant
  with raw-bytes capture, because text capture would itself hide the CR.
- `scripts/ci/ffmpeg_patch_stack.py`: `settle_updates()` emits
  `path.relative_to(repo).as_posix()` in the receipt's `changed` list — the receipt
  is machine-readable and must not switch separators per platform. **On rebase**:
  any new receipt field that names a repository-relative path uses `.as_posix()`
  too (the tests compare POSIX names).
- `scripts/ci/ffmpeg-shared-series.sh`: `sha256_of()` strips coreutils' one-byte
  backslash escape marker (a `C:\`-style cache path makes `sha256sum` prefix the
  digest with `\`), and `fetch_series()` normalises the cache directory through the
  new `posix_path` (`cygpath -u`) before mktemp/sha256sum/tar consume it — GNU tar
  reads a leading `C:` as a remote host. **On rebase**: keep every new path that
  crosses a tool boundary inside the script through `posix_path` first; the apply
  side needs no conversion (`git -C` and `cp` accept Windows paths).
- The maintainer's #3678 (`1e2c9e2ee`) rewrote the replacement logic of
  `ffmpeg_patch_stack.py`; this branch is based on master `6f8d9451a` (which
  contains it) and touches disjoint regions (`settle_updates`, the fixture and the
  bash helper), so rebase conflicts are not expected.
