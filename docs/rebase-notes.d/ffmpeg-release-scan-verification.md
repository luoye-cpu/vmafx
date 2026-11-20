## FFmpeg release scan verification (2026-11-21)

Keep the future-base scan read-only and separate from same-base Renovate updates.
Candidate discovery uses the existing shared-series verifier's `inspect` command:
signed checksums establish the digest, and both the base tag and peeled commit
must match before direct replay. Download, verification and replay failures must
remain failures. Retain failure provenance before checking successful proposals.
The bounded OpenSSL fixture verifies local control flow, not the Sigstore trust chain.
