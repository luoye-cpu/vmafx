- **Route GitHub release tool downloads through the repository's measured retry policy, so a short 5xx does not fail a required job (#3349).**
  `scripts/ci/download-release.sh` validates a `github.com/<owner>/<repo>/releases/download/...` URL and runs
  `compat/python-vmaf/tools/retrying_download.py` (seven attempts, waits of 5/10/20/40/80/120 s). Each transfer
  is streamed with deadline-aware `read1` calls (each socket wait capped by the remaining 300 s deadline, plus a
  60 s per-read timeout), and the deadline starts before the request, so a stalled body or one that keeps
  sending past the transfer deadline fails and is retried instead of hanging the job. Chunk framing may take
  more than one socket read, but each socket wait is capped by the remaining deadline.
  A `Content-Length` short-body check is kept. The Linux
  workflow installers (Spectral and Trivy in `lint-and-format.yml`; Gitleaks in `security-scans.yml`;
  kuttl in `e2e-k8s.yml`) and `scripts/ci/pinned-tool.sh` now call it instead of their own `curl` flags.
  `scripts/ci/tests/test_release_downloads_retry.py` audits every workflow step and CI script, including
  release URLs declared in workflow- or job-level `env`, refuses a direct `curl`/`wget` of a release asset,
  and has planted cases. `scripts/ci/ffmpeg-shared-series.sh` gains
  `FFMPEG_FIX_SERIES_SUMS_SIGSTORE_URL`, so a mirror can serve the signature bundle next to `SHA256SUMS`.
