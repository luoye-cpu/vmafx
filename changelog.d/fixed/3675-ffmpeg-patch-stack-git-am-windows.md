- FFmpeg patch-stack replay and refresh no longer fail on Windows. The replay
  fixture wrote its upstream files with Python text mode, so on Windows the fixture
  upstream stored CRLF blobs while `git am --3way` normalises the patch's context
  lines, and the two no longer matched (`patch does not apply`); the fixture now
  writes LF bytes on every platform, and new tests pin the fixture invariant and the
  blob-line-ending mechanism. Behind that, the refresh receipt's `changed` list now
  carries POSIX separators (`ffmpeg-patches/README.md`, not
  `ffmpeg-patches\README.md`), and `ffmpeg-shared-series.sh` accepts Windows-style
  cache paths: it strips coreutils' backslash escape marker from `sha256sum` digests
  and normalises the cache directory with `cygpath` before mktemp, sha256sum and tar
  read it, so a locally run refresh no longer dies on the sha256 pin or at unpack
  ([research digest](docs/research/3675-ffmpeg-patch-stack-git-am-windows.md), #3675).
