#!/bin/sh
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# embed_generated_object.sh — point the bpf2go binding at embeddedObject()
# (object_embed.go) instead of its own go:embed of the object, so that the
# package compiles when the object has not been generated (ADR-1622). Run by
# gen.go's third go:generate directive; idempotent. Fails when the binding has
# neither the embed bpf2go emits nor the rewritten form, which means bpf2go's
# output changed and this script has to follow it.
set -eu
for f in "$@"; do
  if grep -q '^var _RcloneTrackerBytes = embeddedObject()$' "$f"; then
    continue
  fi
  if ! grep -q '^//go:embed rclonebypass_bpfel\.o$' "$f"; then
    echo "embed_generated_object.sh: $f has no '//go:embed rclonebypass_bpfel.o' line" >&2
    exit 1
  fi
  awk '
    /^\/\/go:embed rclonebypass_bpfel\.o$/ { next }
    /^\t_ "embed"$/ { next }
    /^var _RcloneTrackerBytes \[\]byte$/ {
      print "// ADR-1622: bpf2go embeds the object here; embed_generated_object.sh reads it"
      print "// through embeddedObject() (object_embed.go) so the package builds without it."
      print "var _RcloneTrackerBytes = embeddedObject()"
      next
    }
    { print }
  ' "$f" >"$f.tmp"
  mv "$f.tmp" "$f"
  grep -q '^var _RcloneTrackerBytes = embeddedObject()$' "$f"
done
