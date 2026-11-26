// SPDX-License-Identifier: EUPL-1.2
// Copyright 2026 Lusoris
//
// retry_test.go — fake-clock tests for the smoke test's bounded poll.

package main

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jonboulle/clockwork"
)

func waitPollCalls(t *testing.T, calls *atomic.Int32, want int32) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for calls.Load() < want && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if got := calls.Load(); got != want {
		t.Fatalf("poll calls = %d, want %d", got, want)
	}
}

// TestPollWithClock_RecoversAfterPending: a not-ready check is retried and the
// next answer completes the poll.
func TestPollWithClock_RecoversAfterPending(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	done := make(chan error, 1)
	go func() {
		done <- pollWithClock(context.Background(), func(context.Context) (bool, error) {
			return calls.Add(1) > 1, nil
		}, clk)
	}()
	waitPollCalls(t, &calls, 1)
	if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
		t.Fatalf("waiting for the poll timer: %v", err)
	}
	clk.Advance(pollInterval)
	if err := <-done; err != nil || calls.Load() != 2 {
		t.Fatalf("poll err = %v after %d calls, want nil after 2", err, calls.Load())
	}
}

// TestPollWithClock_ExhaustsAtPollAttempts: a condition that never becomes true
// stops at pollAttempts.
func TestPollWithClock_ExhaustsAtPollAttempts(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	done := make(chan error, 1)
	go func() {
		done <- pollWithClock(context.Background(), func(context.Context) (bool, error) {
			calls.Add(1)
			return false, nil
		}, clk)
	}()
	for want := int32(1); want < pollAttempts; want++ {
		waitPollCalls(t, &calls, want)
		if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
			t.Fatalf("waiting for poll timer %d: %v", want, err)
		}
		clk.Advance(pollInterval)
	}
	waitPollCalls(t, &calls, pollAttempts)
	if err := <-done; err == nil || calls.Load() != pollAttempts {
		t.Fatalf("poll err = %v after %d calls, want exhaustion after %d", err, calls.Load(), pollAttempts)
	}
}

// TestPollWithClock_CancellationDuringWait: cancelling the caller ends the wait
// without another check.
func TestPollWithClock_CancellationDuringWait(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- pollWithClock(ctx, func(context.Context) (bool, error) {
			calls.Add(1)
			return false, nil
		}, clk)
	}()
	waitPollCalls(t, &calls, 1)
	if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
		t.Fatalf("waiting for the poll timer: %v", err)
	}
	cancel()
	if err := <-done; err == nil || calls.Load() != 1 {
		t.Fatalf("poll err = %v after %d calls, want cancellation after 1", err, calls.Load())
	}
}
