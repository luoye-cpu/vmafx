// Copyright 2026 luoye-cpu
// SPDX-License-Identifier: EUPL-1.2

// Package executil holds small shared settings for subprocess calls.
package executil

import "time"

// SubprocessWaitDelay is the post-cancel grace period used by most
// cmd.WaitDelay sites. After the context is done, or after the child exits
// with its pipes still open, Wait allows this long before it kills the child
// and closes its I/O pipes, so a child that ignores the cancellation cannot
// block the caller forever.
const SubprocessWaitDelay = 2 * time.Second

// ShortSubprocessWaitDelay is the one-second post-cancel grace period for
// probes and single-binary helpers that start no child processes of their own
// holding the pipes open.
const ShortSubprocessWaitDelay = time.Second
