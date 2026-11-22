// SPDX-License-Identifier: EUPL-1.2
// Copyright 2026 Lusoris
//
// cmd/vmafx-node/backoff.go — jittered exponential backoff for the controller
// client (ADR-0713 node loop).
//
// RegisterNode and PullWork retries wait through a backoff value. The bounded
// ReportResult retry goes through core/retry instead. The delay doubles per
// consecutive failure up to a cap, and each wait is drawn from the upper half
// of the current window ("equal jitter") so a fleet of nodes that lost the
// controller at the same moment does not reconnect in lockstep.

package main

import (
	"context"
	"math/rand/v2"
	"time"
)

// backoff produces jittered exponential delays. The zero value is not usable;
// construct it with newBackoff.
type backoff struct {
	base    time.Duration
	max     time.Duration
	current time.Duration
}

// newBackoff returns a backoff starting at base and capped at max. A max below
// base is raised to base so the window never shrinks.
func newBackoff(base, maxDelay time.Duration) *backoff {
	if maxDelay < base {
		maxDelay = base
	}
	return &backoff{base: base, max: maxDelay, current: base}
}

// next returns the delay for the coming wait and doubles the window for the
// wait after it, up to the cap. The returned delay lies in [window/2, window].
func (b *backoff) next() time.Duration {
	window := b.current
	b.current *= 2
	if b.current > b.max || b.current <= 0 {
		b.current = b.max
	}
	half := window / 2
	if half <= 0 {
		return window
	}
	// #nosec G404 -- the jitter only spreads reconnect times; it guards no secret.
	return half + rand.N(half+1)
}

// reset returns the window to its base after a successful call.
func (b *backoff) reset() { b.current = b.base }

// sleepCtx waits for d or until ctx is done, whichever comes first, and
// returns ctx.Err() when the context ended the wait.
func sleepCtx(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
