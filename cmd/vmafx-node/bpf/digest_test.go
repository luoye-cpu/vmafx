// Copyright 2026 Lusoris
// SPDX-License-Identifier: EUPL-1.2

// digest_test.go — the generated object against the digest recorded for the
// pinned clang (ADR-1622). The object is not committed; scripts/dev/gen-node-bpf.sh
// writes it and records, in .rclonebypass.stamp, which clang built it.

package bpf

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// requireGeneratedObject skips a test that needs the generated object, naming
// the precondition. go-ci, the container builds and `make go-test` generate the
// object first, so these tests run there.
func requireGeneratedObject(t *testing.T) {
	t.Helper()
	if len(_RcloneTrackerBytes) == 0 {
		t.Skip("no generated eBPF object in this build: run `make node-bpf` (scripts/dev/gen-node-bpf.sh), then rebuild")
	}
}

// pinFromConfig reads KEY="value" from build-config.env.
func pinFromConfig(t *testing.T, key string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "build-config.env"))
	if err != nil {
		t.Fatalf("read build-config.env: %v", err)
	}
	m := regexp.MustCompile(`(?m)^` + key + `="([^"]*)"`).FindSubmatch(raw)
	if m == nil {
		t.Fatalf("%s is not set in build-config.env", key)
	}
	return string(m[1])
}

// TestEmbeddedObjectMatchesPinnedDigest: an object that the pinned clang built
// has the sha256 recorded as BPF_OBJECT_SHA256, so the same clang gives the
// same bytes (the reproducibility check ADR-1539 had as a committed file).
// With another clang the bytes differ by design and the test skips naming both
// versions; without the stamp it fails, because the object was not generated
// by the script.
func TestEmbeddedObjectMatchesPinnedDigest(t *testing.T) {
	t.Parallel()
	requireGeneratedObject(t)
	stamp, err := os.ReadFile(".rclonebypass.stamp")
	if err != nil {
		t.Fatalf("no generation stamp: run scripts/dev/gen-node-bpf.sh (make node-bpf): %v", err)
	}
	fields := strings.Fields(string(stamp))
	if len(fields) != 2 {
		t.Fatalf("stamp %q is not '<inputs sha256>  <clang version>'", stamp)
	}
	pinned := pinFromConfig(t, "BPF_CLANG_VERSION")
	if fields[1] != pinned {
		t.Skipf("object built with clang %s, the pin is %s: not byte-identical to a release build", fields[1], pinned)
	}
	sum := sha256.Sum256(_RcloneTrackerBytes)
	if got, want := hex.EncodeToString(sum[:]), pinFromConfig(t, "BPF_OBJECT_SHA256"); got != want {
		t.Errorf("object sha256 = %s, BPF_OBJECT_SHA256 = %s: re-record the digest in build-config.env if the source or flags changed on purpose", got, want)
	}
}

// TestPinnedDigestIsAHexSHA256: the recorded digest is well formed (boundary).
func TestPinnedDigestIsAHexSHA256(t *testing.T) {
	t.Parallel()
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(pinFromConfig(t, "BPF_OBJECT_SHA256")) {
		t.Error("BPF_OBJECT_SHA256 is not 64 lower-case hex digits")
	}
}

// TestRequireObject: a build without the object refuses to start the tracker
// and names the generator; a build with one passes (negative and positive).
func TestRequireObject(t *testing.T) {
	t.Parallel()
	for _, empty := range [][]byte{nil, {}} {
		err := requireObject(empty)
		if !errors.Is(err, errObjectMissing) {
			t.Fatalf("requireObject(%v) = %v, want errObjectMissing", empty, err)
		}
		for _, want := range []string{"make node-bpf", "scripts/dev/gen-node-bpf.sh"} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("error %q does not name %q", err, want)
			}
		}
	}
	if err := requireObject([]byte{0x7f, 'E', 'L', 'F'}); err != nil {
		t.Errorf("requireObject(non-empty) = %v, want nil", err)
	}
}

// TestObjectFSNoticeKeepsThePatternSatisfiable: the committed notice is what the
// go:embed pattern matches on a checkout without the object, and it is not the
// object (boundary: embeddedObject returns nil, not the notice).
func TestObjectFSNoticeKeepsThePatternSatisfiable(t *testing.T) {
	t.Parallel()
	if _, err := objectFS.ReadFile("rclonebypass_bpfel.o.NOTICE"); err != nil {
		t.Fatalf("the committed notice is not embedded: %v", err)
	}
	if object := embeddedObject(); len(object) > 0 && string(object[:4]) != "\x7fELF" {
		t.Errorf("embeddedObject starts with %q, want an ELF object or nothing", object[:4])
	}
}
