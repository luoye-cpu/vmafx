// Copyright 2026 Lusoris
// SPDX-License-Identifier: EUPL-1.2

// Package bpf contains the eBPF descriptor tracker for rclone FUSE mounts and
// its Go-side loader.
//
// rclone_bypass.bpf.c is compiled with clang through bpf2go into
// rclonebypass_bpfel.o and its Go binding rclonebypass_bpfel.go. The object is
// generated at build time and not committed (ADR-1622); the binding is source
// and is committed. Run `make node-bpf` (scripts/dev/gen-node-bpf.sh), which
// checks for the tools and names a missing one; `go generate
// ./cmd/vmafx-node/bpf/` is the underlying command. Without the object the
// package still compiles, and the node refuses to start the tracker and names
// the generator (object_embed.go). The release build uses the clang pinned by
// BPF_CLANG_VERSION in build-config.env and checks the object against
// BPF_OBJECT_SHA256 and the binding against the committed file; change the C
// source, vmlinux.h or the flags below, re-record that digest and commit the
// regenerated binding in the same change. Only the little-endian object is
// generated (amd64, arm64 and the other bpf2go little-endian targets): the
// package does not build on a big-endian architecture, and the node refuses
// VMAFX_EBPF_BYPASS there. vmlinux.h is a minimal header with only the types
// the program uses (tracepoint contexts follow the stable tracepoint ABI).
//
// The second directive (add_spdx_header.sh) prepends the licence header the
// repository requires on every Go file (ADR-1250) to the generated binding; the
// third (embed_generated_object.sh) routes the binding's object through
// object_embed.go. bpf2go is the github.com/cilium/ebpf module version in
// go.mod, the one the loader links. TestEmbeddedObjectMatchesMirrors checks the
// generated object against the layouts the loader relies on;
// TestEmbeddedObjectMatchesPinnedDigest checks it against the recorded digest.
//
// The bpf2go identifier is rcloneTracker, and -output-stem keeps the file
// names rclonebypass_bpfel.*. gosec G101 reads the generated constant names:
// the earlier identifier rcloneBypass put "pass" into every one of them, and
// four object names then read as hard-coded credentials (issue 3630). Keep the
// identifier free of the words G101 matches (pass, pwd, pw, token, secret,
// cred, bearer, apiKey); the second gosec pass of `make lint-go` scans this
// generated file.
//
// ADR-0779, ADR-1539, ADR-1622.
package bpf

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cflags "-O2 -g -Wall -Wextra" -target bpfel -type event_t -output-stem rclonebypass rcloneTracker rclone_bypass.bpf.c -- -I/usr/include/bpf -I.
//go:generate sh ./add_spdx_header.sh rclonebypass_bpfel.go
//go:generate sh ./embed_generated_object.sh rclonebypass_bpfel.go
