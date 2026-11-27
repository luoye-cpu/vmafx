<!-- markdownlint-disable MD013 MD041 -->
# Research-3675: FFmpeg patch stack `git am --3way` fails on Windows

## Executive Summary

Issue #3675 reports that `scripts/ci/ffmpeg_patch_stack.py` fails on Windows: the
`re.subn` template half was fixed by the maintainer in PR #3678 (commit `1e2c9e2ee`,
already in master); this digest covers the `git am --3way` half, whose observable
failure is the replay suite of `scripts/ci/test_ffmpeg_patch_stack.py` collapsing on
Windows with `error: sample: patch does not apply` raised out of `apply_stack`
(`replay.git("am", "--3way", ...)`), on every test that replays the fixture stack.

The evidence chain below was gathered on a real Windows 10.0.26300 host with
Git 2.56.0.windows.2, under both CPython 3.12.10 (local) and 3.14.7 (the project's
CI Python, `requires-python = ">=3.14"`). The headline failure — `git am --3way`
reporting "patch does not apply" — is **not** in `ffmpeg_patch_stack.py` itself and
**not** in any Git line-ending configuration: it is the test fixture's own file
writes. `RealReplay.setUp` (and the `SharedSeriesReplay` / release-move sites) write
fixture upstream files with Python text mode (`Path.write_text`, `newline=None`),
which translates `\n` to `os.linesep` on Windows. The fixture Git helper strips every
configuration source (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=devnull`), so
`core.autocrlf` is unset and the CRLF bytes are committed into the fixture blobs
verbatim. The replay checkout then materialises CRLF worktree/index blobs, while
`git am` normalises the patch side (mailsplit strips CR from every CRLF line before
the apply machinery runs), so the patch's context lines no longer match the bytes
recorded in the replay index: `git apply --3way` reconstructs a pre-image from the
CR-free context that differs from the recorded CRLF blob and refuses with
"Did you hand edit your patch? It does not apply to blobs recorded in its index."

Fixing the fixture exposed four further Windows-only defects that the `git am`
conflict had been masking, each reproduced and fixed with the smallest change that
the evidence supports:

1. `ffmpeg_patch_stack.py`'s `settle_updates()` emitted OS-specific separators into
   the receipt's `changed` list (`ffmpeg-patches\README.md` on Windows) — the "path"
   half of the issue title beyond the maintainer's `re.subn` fix.
2. `SharedSeriesReplay.pin_series()` wrote `build-config.env` in text mode; on
   Windows the bash helper sources that file, so every pinned value ended with CR
   and `ffmpeg-shared-series.sh`'s `load_config` refused the sha256 pin.
3. The fixture tarball helper wrote `SHA256SUMS` in text mode; the script's
   `check_listed_sum` matches the member-name field with awk, and a CR-suffixed
   name matched nothing.
4. `ffmpeg-shared-series.sh` hashed and unpacked the shared series through
   Windows-style cache paths: coreutils `sha256sum` prefixes the digest line with a
   backslash when the echoed file name carries one (`\<digest>`, so the pin
   comparison failed with two look-alike values), and GNU tar reads the leading
   `C:` of a backslash path as a remote host ("tarball cannot be unpacked"). The
   script now strips the escape marker in `sha256_of` and normalises the cache
   directory through `cygpath` (`posix_path`) before mktemp/sha256sum/tar use it.

After the fixes the whole `test_ffmpeg_patch_stack.py` file passes on Windows under
CPython 3.14.7 (the CI version), so the maintainer's done-when is met without a
skip: `RealReplay.test_empty_or_corrupt_cache_is_refetched` passes on a Windows
host. Under CPython 3.12.10 the same suite passes everything except
`test_empty_or_corrupt_cache_is_refetched`, whose remaining failure is a
pre-existing 3.12-only environment artifact (see "Residual notes").

## Problem analysis

### 1. The failing surface

`apply_stack` (`scripts/ci/ffmpeg_patch_stack.py`, `replay.git("am", "--3way",
str(repo / "ffmpeg-patches" / name))`) is exercised on Windows exclusively through
`RealReplay` / `SharedSeriesReplay`, whose `setUp` builds a fixture upstream
repository and a fixture patch:

- `scripts/ci/test_ffmpeg_patch_stack.py` `RealReplay.setUp`:
  `(self.upstream / "sample").write_text("before\n")` and
  `(self.upstream / "sample").write_text("patched\n")` — these two writes become the
  committed blobs of the fixture upstream (`git add` runs with a config-stripped
  environment, so nothing filters CR on check-in);
- `self.patch.write_text(series_patch)` — the patch file the script later hands to
  `git am --3way`.

On Windows, `write_text` uses `os.linesep` translation (`newline=None`), so both the
fixture blobs and the fixture patch file carry CRLF.

### 2. The Windows reproduction (before the fix)

```text
$ python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py::RealReplay
19 failed, 25 passed in 94.03s

$ python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py
55 failed, 75 passed, 24 subtests passed in 134.10s
```

Every failure dies in `Replay.run_git` raising
`RuntimeError("git am failed: ...")` with the stderr signature:

```text
error: patch failed: sample:1
error: sample: patch does not apply
error: Did you hand edit your patch?
It does not apply to blobs recorded in its index.
hint: Use 'git am --show-current-patch=diff' to see the failed patch
```

`test_conflicting_release_preserves_every_input` still passes by coincidence (it
expects a `CONFLICT` for unrelated content reasons); the other 18 `RealReplay`
failures and all `SharedSeriesReplay` replay failures are this mechanism. The full
failure list is preserved in the branch NOTES (`worktrees/_r54/3675/NOTES.md` points
at the archived probe output).

### 3. The decisive experiment: blob endings x patch endings

A standalone probe (`probe_matrix.py`, archived outside the worktree) replayed the
script's exact environment — fresh `git init`, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=devnull`, `core.hooksPath=/dev/null`, `--depth=1` fetch of a
fixture tag, `switch --detach`, `git am --3way <patch>` — against every combination
of blob and patch line endings:

| fixture blob (`sample`) | patch file | `git am --3way` |
| --- | --- | --- |
| `b"before\n"` | LF | OK |
| `b"before\n"` | CRLF | OK |
| `b"before\r\n"` | LF | CONFLICT — "patch does not apply" |
| `b"before\r\n"` | CRLF | CONFLICT — "patch does not apply" |

Reading of the matrix:

1. **The patch file's own CRLF is harmless.** `git mailsplit` strips the CR from
   every line that ends `\r\n` before `git mailinfo`/`git apply --3way` ever see the
   hunks, so a CRLF patch applies cleanly to LF blobs. Any fix that only normalises
   patch bytes before `am` (the first suspect from the issue discussion) is provably
   a no-op in every reachable failure cell.
2. **The blob side is fatal.** With CRLF blobs the CR-free context lines match
   nothing; the `--3way` fallback builds a pre-image from the patch context that
   differs from the recorded blob and aborts with the exact error the issue reports.
3. **Configuration cannot save either side.** The replay environment and the fixture
   Git environment both strip system/global config (`isolated_git_environment()`),
   so `core.autocrlf` is unset and defaults to `false` inside the replay checkout
   already; and no Git setting can re-shape blobs that were committed with CR
   because Python wrote them that way. This is why the maintainer's
   "even with `core.autocrlf=false`" observation changed nothing.

### 4. Why Linux never reproduces

`Path.write_text` translates to `os.linesep`, which is `\n` on Linux and macOS: the
fixture blobs are LF there and the replay matches. The fixture half is Windows-only.

### 5. The masked defects behind the first fix

With the fixture blobs LF, the suite advanced past `git am` and failed on four
further Windows-only defects that the conflict had hidden. Each was reproduced on
this host (failure text verbatim) and fixed:

1. **`settle_updates` receipt separators.** `changed` listed
   `ffmpeg-patches\README.md` (backslashes) while the tests and every consumer
   expect repository-relative POSIX names; six `RealReplay` tests failed with
   `['ffmpeg-patches\\README.md'] != ['ffmpeg-patches/README.md']`. Fixed with
   `path.relative_to(repo).as_posix()`.
2. **Sourced CR in the pinned series configuration.** `SharedSeriesReplay.pin_series`
   wrote `build-config.env` in text mode; `ffmpeg-shared-series.sh` runs
   `. "$config"`, so `FFMPEG_FIX_SERIES_SHA256="<hex>"\r` bound a CR into the value
   and `load_config` refused the pin (or the sha256 equality died comparing two
   look-alike values). 36 `SharedSeriesReplay` failures. Fixed with `write_lf`.
3. **CRLF `SHA256SUMS`.** The fixture helper wrote the sums file in text mode;
   `check_listed_sum` matches `awk '$2 == name'` and the CR rides on the name field,
   so the release "lists nothing". Fixed with `write_bytes`.
4. **Windows cache paths in `ffmpeg-shared-series.sh`.** The cache directory is
   derived from `XDG_CACHE_HOME`, which Windows callers export with backslashes.
   (a) `sha256sum "C:\...\tarball"` echoes the name escaped, prefixing the digest
   line with `\` (verified standalone: `sha256sum 'C:\...\file' | cut -d' ' -f1`
   yields `\<digest>`), so every pin/sums comparison failed against two visually
   identical values — `sha256_of` now strips the one-byte escape marker. (b)
   `tar -xzf "C:\..."` misreads the `C:` as a remote host and dies with "tarball
   cannot be unpacked" — `fetch_series` now normalises the cache directory through
   `cygpath -u` (`posix_path`) before mktemp/sha256sum/tar consume it. The apply
   path needs no change: `git -C <Windows tree>` and `file://C:\...` downloads
   (`cp`) handle Windows paths natively.

## Root cause statement

The replay fixture wrote blob-carrying files with Python text mode, so on Windows
the fixture upstream stored CRLF blobs while `git am --3way` presents CR-free patch
context: context matching fails and the replay aborts with "patch does not apply".
The fixture's `write_text` calls are a fidelity bug — the fixture upstream simulates
the FFmpeg remote, whose blobs are LF. Behind that headline failure sat four further
Windows-only defects in the receipt surface (`settle_updates` separators), the
fixture's bash-consumed files (`build-config.env`, `SHA256SUMS`) and the shared
series script (`sha256_of` escape marker, `tar` drive-letter path), all masked by
the conflict and all reproduced on this host.

## Fix

`scripts/ci/test_ffmpeg_patch_stack.py`, `scripts/ci/ffmpeg_shared_series_fixture.py`,
`scripts/ci/ffmpeg_patch_stack.py` and `scripts/ci/ffmpeg-shared-series.sh`:

1. `RealReplay.write_lf(path, content)` — one helper with the rationale in its
   docstring, using `path.write_text(content, newline="\n")` (LF bytes on every
   platform). Applied to all seven fixture writes that feed blob content or the
   `git am` patch file (`RealReplay.setUp`'s `sample` pair and patch file,
   `test_latest_release_rebases_and_updates_all_mirrors`'s `other`,
   `tag_conflicting_release`'s `sample`, `SharedSeriesReplay.setUp`'s `shared-fix`,
   `test_new_ffmpeg_release_waits_for_a_series_release`'s `other`), and to
   `pin_series`'s `build-config.env` (sourced by the bash helper).
2. `ffmpeg_shared_series_fixture.tarball` writes `SHA256SUMS` as bytes (LF).
3. `ffmpeg_patch_stack.settle_updates` emits
   `path.relative_to(repo).as_posix()` in the receipt's `changed` list.
4. `ffmpeg-shared-series.sh`: `sha256_of` strips coreutils' one-byte backslash
   escape marker from the digest field; a new `posix_path` helper normalises the
   cache directory through `cygpath -u` when it exists, before mktemp, sha256sum
   and tar use it.
5. `RealReplay.git_bytes(path, *args)` — raw-bytes fixture Git capture (text capture
   would itself strip CR through universal newlines and hide the defect).

Regression pins (offline, no network):

- Pin A — `RealReplay.test_fixture_upstream_blobs_and_patch_are_lf`: the `sample`
  blobs at the pinned base and at HEAD, and the patch file, contain no CR bytes.
  Red on Windows before the fix, green after, on every OS.
- Pin B — `UpstreamBlobLineEndings.test_crlf_blobs_conflict_and_lf_blobs_apply`:
  the mechanism test. It builds two fixture upstreams differing only in the sample
  blob's line endings, applies the same CR-free patch through the script's replay
  choreography (isolated config, `--depth=1` tag fetch, `switch --detach`,
  `git am --3way`) and asserts the CRLF blob conflicts with "patch does not apply"
  while the LF blob applies. ~1 s.
- The end-to-end Windows coverage of the shared-series script fixes is carried by
  `SharedSeriesReplay` itself (it drives the real `ffmpeg-shared-series.sh` through
  `apply_shared_series`) and by `test_ffmpeg_shared_series_fallback.py`, whose
  fixture URLs are `file://` URLs of Windows temp paths on this host.

## Alternatives considered

1. **Normalise patch bytes to LF in `apply_stack` before `git am`.** Rejected: the
   matrix shows a CRLF patch applies cleanly to LF blobs (mailsplit strips CR), so
   this changes no reachable failure cell; it would be dead code in a script whose
   byte-level behaviour is otherwise pinned by tests.
2. **Renormalise the replay checkout to LF after `switch --detach`**
   (`git add --renormalize` or a `core.autocrlf` setting). Rejected: it mutates the
   checked-out trees, so the receipt's `patched_tree` (and the README claim's tree
   hash that `--check` gates against) would drift per platform instead of being a
   single canonical value, and blobs fetched for the rebase target would diverge
   between the refresh and the build lanes.
3. **Skip the replay suite on Windows naming the issue.** Rejected: the root cause
   is offline and cheap to fix; the done-when prefers the suite passing.
4. **Convert every Windows path at the `sha256_of`/`tar` call sites** instead of
   normalising the cache directory once. Rejected: two conversions per call and
   every future call site would repeat the hazard; one `posix_path` at the cache
   boundary covers mktemp, sha256sum, tar and mv.
5. **Chosen: fixture fidelity fix + receipt separator fix + shared-series path
   fixes + invariant and mechanism pins.**

## Reproduction and verification commands

```bash
# before the fix, on Windows (red):
python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py::RealReplay
#   -> 19 failed (all "git am failed: ... patch does not apply")
python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py
#   -> 55 failed, 75 passed (SharedSeriesReplay included)

# after the fixture fix, the masked defects surface:
#   ['ffmpeg-patches\\README.md'] != ['ffmpeg-patches/README.md']   (settle_updates)
#   ... sha256 of file://C:\... is \b8f9..., build-config.env pins b8f9...  (sourced CR
#       in build-config.env + coreutils' backslash escape marker)
#   ffmpeg-shared-series: tarball cannot be unpacked                       (tar C: host)

# the two new pins, offline, on any OS:
python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py::UpstreamBlobLineEndings
python -m pytest -q \
  scripts/ci/test_ffmpeg_patch_stack.py::RealReplay::test_fixture_upstream_blobs_and_patch_are_lf

# after all fixes, on the same Windows host, CPython 3.14.7 (the CI Python):
python -m pytest -q scripts/ci/test_ffmpeg_patch_stack.py
#   -> 133 passed, 0 failed
```

## Residual notes

- **Python floor.** The suite passes fully under CPython 3.14.7 (`requires-python
  = ">=3.14"`, CI `PYTHON_CI_VERSION` 3.14.7). Under a local CPython 3.12.10 the
  same run leaves exactly one failure,
  `RealReplay.test_empty_or_corrupt_cache_is_refetched`, whose traceback is
  `PermissionError (WinError 5)` on a read-only cached `.git` object followed by
  `NotImplementedError: chmod: follow_symlinks unavailable on this platform` from
  `scripts/lib/scratch_program.py`'s `remove_file` retry: `os.chmod(...,
  follow_symlinks=False)` reports support on 3.14 (`os.chmod in
  os.supports_follow_symlinks` is `True` there, verified on this host) and not on
  3.12. No production change is carried for it — the project floor is 3.14, where
  the path works. The same Windows-host run of `test_ffmpeg_shared_series_fallback.py`
  shows the identical failure set (17 failed / 6 passed) with the branch stashed
  back to pristine `upstream/master` `6f8d9451a`, confirming it as a pre-existing
  environment baseline unrelated to this change.
- **Production replay flow.** The canonical `ffmpeg-patches/*.patch` files are LF in
  the index and written as LF bytes by `format_patch_updates` (`write_bytes`); a
  working tree mangled by a `core.autocrlf=true` checkout still replays correctly
  because mailsplit strips CR (matrix row 2). The carried production changes are
  limited to the receipt surface (`settle_updates`) and the shared-series script's
  Windows path/digest handling.
- **Shared fix series in production.** `ffmpeg-shared-series.sh` runs in Linux
  containers for builds; the Windows paths only arise for a developer running the
  refresh locally on Windows, which is the scenario this issue is about.
