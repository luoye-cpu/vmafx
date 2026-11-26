"""Bounded cryptographic fixture: real shell/checksums/replay, not a Sigstore integration test."""

# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ci import ffmpeg_patch_candidates as candidates
from scripts.ci import ffmpeg_patch_stack as stack
from scripts.ci import ffmpeg_shared_series_fixture as shared
from scripts.ci import test_ffmpeg_patch_stack as fixtures
from scripts.lib.safe_subprocess import run as run_command


class BoundedSignedScan(unittest.TestCase):
    """Network fixture plus OpenSSL signature stand-in; production verifier is never mocked."""

    def setUp(self) -> None:
        self.fixture = fixtures.RealReplay()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.assets = self.root / "assets"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.openssl = shutil.which("openssl")
        if self.openssl is None:
            self.fail("OpenSSL is required for the bounded signature fixture")
        self.crypto(
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(self.root / "private.pem"),
        )
        self.crypto(
            "pkey",
            "-in",
            str(self.root / "private.pem"),
            "-pubout",
            "-out",
            str(self.root / "public.pem"),
        )
        self.install_transport()
        self.environment = patch.dict(
            os.environ,
            {
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "FIXTURE_ASSETS": self.assets.as_posix(),
                "FIXTURE_PUBLIC": (self.root / "public.pem").as_posix(),
                "FIXTURE_OPENSSL": str(self.openssl),
                "FFMPEG_FIX_SERIES_VERIFY": "cosign",
                "FFMPEG_FIX_SERIES_CACHE": "none",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.create_release("v0.1.0", "n9.0.1", self.fixture.pin, "current-shared")
        current = candidates.metadata(self.assets / "v0.1.0")
        self.pins = {
            "FFMPEG_FIX_SERIES_REPO": "https://github.com/fixture/ffmpeg-patches",
            "FFMPEG_FIX_SERIES_TAG": "v0.1.0",
            "FFMPEG_FIX_SERIES_SHA256": current["sha256"],
        }
        config = self.fixture.repo / "build-config.env"
        config.write_text(
            config.read_text() + "".join(f'{k}="{v}"\n' for k, v in self.pins.items())
        )

    def crypto(self, *args: str) -> None:
        assert self.openssl is not None
        run_command(
            [self.openssl, *args],
            allowed_executables=(self.openssl,),
            timeout_seconds=30,
            capture_output=True,
            text=True,
            check=True,
        )

    def install_transport(self) -> None:
        # curl is only a transport fixture. cosign stand-in checks CLI identity/issuer
        # and a real RSA/SHA256 signature; it does not implement Sigstore certificates/TUF.
        scripts = {
            "curl": """#!/usr/bin/env bash
set -euo pipefail
destination=""; next=false
for arg in "$@"; do
  if [ "$next" = true ]; then destination="$arg"; next=false; fi
  if [ "$arg" = --output ]; then next=true; fi
done
url="${!#}"; asset="${url##*/}"; prefix="${url%/*}"; tag="${prefix##*/}"
cp -- "$FIXTURE_ASSETS/$tag/$asset" "$destination"
""",
            "cosign": """#!/usr/bin/env bash
set -euo pipefail
[ "$#" -eq 8 ] && [ "$1" = verify-blob ] && [ "$2" = --bundle ]
[ "$4" = --certificate-identity ] && [ "$6" = --certificate-oidc-issuer ]
[ "$7" = https://token.actions.githubusercontent.com ]
[[ "$5" =~ ^https://github.com/fixture/ffmpeg-patches/.github/workflows/release-build.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]
"$FIXTURE_OPENSSL" dgst -sha256 -verify "$FIXTURE_PUBLIC" -signature "$3" "${8}"
""",
        }
        for name, content in scripts.items():
            path = self.bin / name
            path.write_bytes(content.encode())
            path.chmod(0o755)

    def create_release(self, tag: str, base_tag: str, commit: str, filename: str) -> None:
        f = self.fixture
        f.git(f.upstream, "switch", "--detach", commit)
        (f.upstream / filename).write_text("shared fix\n")
        f.git(f.upstream, "add", filename)
        f.git(f.upstream, "commit", "-qm", "shared candidate")
        content = f.git(f.upstream, "format-patch", "-1", "--stdout")
        with patch.object(shared, "TAG", tag):
            _, digest = shared.tarball(
                self.assets / tag, base_tag, commit, {shared.PATCH_NAME: content}
            )
        directory = self.assets / tag
        self.crypto(
            "dgst",
            "-sha256",
            "-sign",
            str(self.root / "private.pem"),
            "-out",
            str(directory / "SHA256SUMS.sigstore.json"),
            str(directory / "SHA256SUMS"),
        )
        (directory / "verified.env").write_text(
            f"FFMPEG_FIX_SERIES_TAG={tag}\nFFMPEG_FIX_SERIES_SHA256={digest}\n"
            f"FFMPEG_TAG={base_tag}\nFFMPEG_COMMIT={commit}\n"
        )

    def new_base(self, conflict: bool = False) -> str:
        f = self.fixture
        f.git(f.upstream, "switch", "--detach", f.pin)
        (f.upstream / "current-shared").write_text("old shared would conflict\n")
        (f.upstream / "other").write_text("new base\n")
        if conflict:
            (f.upstream / "sample").write_text("upstream conflict\n")
        f.git(f.upstream, "add", ".")
        f.git(f.upstream, "commit", "-qm", "future release")
        f.git(f.upstream, "tag", "-a", "n9.1", "-m", "annotated future release")
        return f.git(f.upstream, "rev-parse", "n9.1^{commit}").strip()

    def scan(self, tags: list[str]) -> stack.Receipt:
        # Release-list boundary only; signed sums, base inspection and am remain real.
        with patch.object(candidates, "release_tags", return_value=tags):
            return stack.maintain(
                self.fixture.repo, self.fixture.output, False, True, artifact_only=True
            )

    def test_matching_candidate_replays_directly_and_proposal_applies(self) -> None:
        target = self.new_base()
        self.create_release("v0.2.0", "n9.1", target, "new-shared")
        self.create_release("v0.3.0", "n9.1", self.fixture.pin, "wrong-commit")
        before = self.fixture.contents()
        result = self.scan(["v0.3.0", "v0.2.0"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["candidate_shared"]["tag"], "v0.2.0")
        self.assertEqual(result["upstream_commit"], target)
        self.assertEqual(len(result["verified_candidates"]), 2)
        self.assertEqual(self.fixture.contents(), before)
        proposal = self.fixture.output / "proposed-refresh.patch"
        self.fixture.git(self.fixture.repo, "apply", "--check", str(proposal))
        self.fixture.git(self.fixture.repo, "apply", str(proposal))
        self.assertEqual(
            (self.fixture.repo / "ffmpeg-patches/0001-integration.patch").read_bytes(),
            (self.fixture.output / "patches/0001-integration.patch").read_bytes(),
        )

    def test_an_inspected_tag_that_differs_fails_closed(self) -> None:
        checkout = self.root / "checkout"
        (checkout / ".git" / "candidate").mkdir(parents=True)

        def shared_command(_directory: object, args: list[str], _timeout: float) -> None:
            destination = Path(args[-1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "verified.env").write_text(
                "FFMPEG_FIX_SERIES_TAG=v9.9.9\n"
                f"FFMPEG_FIX_SERIES_SHA256={'a' * 64}\n"
                "FFMPEG_TAG=n9.1\n"
                f"FFMPEG_COMMIT={'b' * 40}\n"
            )

        replay = SimpleNamespace(checkout=checkout, shared_command=shared_command)
        with (
            patch.object(candidates, "release_tags", return_value=["v0.2.0"]),
            self.assertRaisesRegex(ValueError, "inspected shared-series tag differs"),
        ):
            stack.scan_candidate(replay, self.fixture.repo, "n9.1", "b" * 40, {})

    def test_invalid_sha256_pins_are_refused(self) -> None:
        config = self.fixture.repo / "build-config.env"
        original = config.read_text()
        current = self.pins["FFMPEG_FIX_SERIES_SHA256"]
        for value in ("A" * 64, "0" * 63, "g" * 64):
            with self.subTest(value=value):
                config.write_text(
                    "\n".join(
                        f'FFMPEG_FIX_SERIES_SHA256="{value}"'
                        if line.startswith("FFMPEG_FIX_SERIES_SHA256=")
                        else line
                        for line in original.splitlines()
                    )
                    + "\n"
                )
                with self.assertRaisesRegex(ValueError, "invalid shared-series SHA256 pin"):
                    candidates.pin_values(self.fixture.repo)
        config.write_text(original)
        self.assertEqual(candidates.pin_values(self.fixture.repo)["FFMPEG_FIX_SERIES_SHA256"], current)

    def release_tags_with(self, releases: list[dict[str, object]] | int) -> list[str]:
        script = self.bin / "gh"
        script.write_text('#!/usr/bin/env bash\nset -euo pipefail\ncat "$FIXTURE_RELEASES"\n')
        script.chmod(0o755)
        if os.name == "nt":
            (self.bin / "gh.bat").write_text('@type "%FIXTURE_RELEASES%"\r\n')
        payload = (
            [{"tag_name": f"v0.1.{index}", "draft": False, "prerelease": False} for index in range(releases)]
            if isinstance(releases, int)
            else releases
        )
        path = self.root / "releases.json"
        path.write_text(json.dumps(payload))
        with patch.dict(os.environ, {"FIXTURE_RELEASES": str(path)}):
            return candidates.release_tags(self.pins["FFMPEG_FIX_SERIES_REPO"])

    def test_release_discovery_stops_at_the_page_boundary(self) -> None:
        self.assertIn("v0.1.0", self.release_tags_with(64))
        with self.assertRaisesRegex(ValueError, "exceeded its bounded page"):
            self.release_tags_with(65)

    def test_draft_prerelease_and_invalid_metadata_are_refused(self) -> None:
        tags = self.release_tags_with(
            [
                {"tag_name": "v0.3.0", "draft": True, "prerelease": False},
                {"tag_name": "v0.2.0", "draft": False, "prerelease": True},
                {"tag_name": "v0.1.0", "draft": False, "prerelease": False},
            ]
        )
        self.assertEqual(tags, ["v0.1.0"])
        with self.assertRaisesRegex(ValueError, "invalid shared-series release metadata"):
            self.release_tags_with(
                [{"tag_name": "v0.1.0", "draft": "no", "prerelease": False}]
            )

    def test_verified_wrong_tag_and_commit_are_explicit_noop(self) -> None:
        target = self.new_base()
        self.create_release("v0.2.0", "n9.2", target, "wrong-tag")
        self.create_release("v0.3.0", "n9.1", self.fixture.pin, "wrong-commit")
        result = self.scan(["v0.3.0", "v0.2.0"])
        self.assertEqual(result["status"], "no-op")
        self.assertEqual(result["reason_code"], "no-compatible-series")
        self.assertEqual(len(result["verified_candidates"]), 2)
        self.assertEqual((self.fixture.output / "proposed-refresh.patch").read_bytes(), b"")

    def test_checkout_cleanup_failure_leaves_no_proposal(self) -> None:
        """A failed scan cleanup must not leave a stale or empty proposal."""
        self.fixture.output.mkdir(exist_ok=True)
        (self.fixture.output / "proposed-refresh.patch").write_bytes(b"stale proposal")
        before = self.fixture.contents()
        with (
            patch.object(stack.tempfile, "TemporaryDirectory", fixtures.FailingCheckoutCleanup),
            self.assertRaises(OSError),
        ):
            self.scan([])
        self.assertFalse((self.fixture.output / "proposed-refresh.patch").exists())
        self.assertEqual(self.fixture.contents(), before)
        receipt = json.loads((self.fixture.output / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")

    def test_no_new_base_is_verified_noop(self) -> None:
        result = self.scan([])
        self.assertEqual(result["reason_code"], "no-new-release")
        self.assertIn("cosign verified", (self.fixture.output / "replay.log").read_text())

    def test_bad_signature_checksum_download_and_replay_fail_closed(self) -> None:
        target = self.new_base()
        for failure in ("signature", "checksum", "download"):
            with self.subTest(failure=failure):
                self.create_release("v0.2.0", "n9.1", target, "candidate-shared")
                directory = self.assets / "v0.2.0"
                if failure == "signature":
                    (directory / "SHA256SUMS").write_text("forged checksum manifest\n")
                elif failure == "checksum":
                    (directory / "ffmpeg-patches-v0.2.0.tar.gz").write_bytes(b"bad archive")
                else:
                    (directory / "SHA256SUMS.sigstore.json").unlink()
                self.assert_failed_scan()
        # A real local integration conflict is still a failed replay.
        self.fixture.git(self.fixture.upstream, "tag", "-d", "n9.1")
        target = self.new_base(conflict=True)
        self.create_release("v0.2.0", "n9.1", target, "candidate-shared")
        self.assert_failed_scan()

    def assert_failed_scan(self) -> None:
        self.fixture.output.mkdir(exist_ok=True)
        (self.fixture.output / "proposed-refresh.patch").write_bytes(b"stale proposal")
        before = self.fixture.contents()
        with self.assertRaises(RuntimeError):
            self.scan(["v0.2.0"])
        receipt = json.loads((self.fixture.output / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse((self.fixture.output / "proposed-refresh.patch").exists())
        self.assertEqual(self.fixture.contents(), before)


class ScanArguments(unittest.TestCase):
    """The three --scan-latest refusals fail before any network or worktree access."""

    def test_missing_latest_refresh_output_or_latest_is_refused(self) -> None:
        cases = (
            (["--scan-latest", "--output-dir", "out"], "--scan-latest requires --latest"),
            (["--scan-latest", "--latest"], "--scan-latest requires --output-dir"),
            (["--latest"], "--latest requires --refresh or --scan-latest"),
        )
        for options, message in cases:
            with self.subTest(options=options):
                stderr = StringIO()
                with (
                    patch.object(sys, "argv", ["ffmpeg_patch_stack.py", *options]),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as raised,
                ):
                    stack.parse_args()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(message, stderr.getvalue())


class ProposalBoundaries(unittest.TestCase):
    def test_no_final_newline_and_new_file_apply_exactly(self) -> None:
        f = fixtures.RealReplay()
        f.setUp()
        self.addCleanup(f.doCleanups)
        original = f.repo / "ffmpeg-patches/README.md"
        original.write_bytes(b"before without LF")
        added = f.repo / "ffmpeg-patches/new.txt"
        updates = {original: b"after without LF", added: b"new without LF"}
        proposal = f.root / "proposal.patch"
        proposal.write_bytes(stack.proposed_patch(f.repo, updates))
        f.git(f.repo, "apply", "--check", str(proposal))
        f.git(f.repo, "apply", str(proposal))
        for path, content in updates.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
