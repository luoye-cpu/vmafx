// Copyright 2026 Lusoris
// SPDX-License-Identifier: EUPL-1.2

// Package executil holds small shared settings for subprocess calls.
package executil

import "time"

// SubprocessWaitDelay is the post-cancel grace period used by most
// cmd.WaitDelay sites. After the context is cancelled, Go closes the child's
// I/O pipes and waits this long for the process to exit before killing it, so
// a child that ignores the cancellation cannot block the caller forever.
const SubprocessWaitDelay = 2 * time.Second

// ShortSubprocessWaitDelay is the one-second variant used by probes and
// one-shot helpers whose children are expected to exit immediately.
const ShortSubprocessWaitDelay = time.Second
