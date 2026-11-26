// SPDX-License-Identifier: EUPL-1.2
// Copyright 2026 Lusoris
//
// clients_test.go — fake-clock tests for the driver's bounded RPC retry.

package main

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jonboulle/clockwork"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func waitCalls(t *testing.T, calls *atomic.Int32, want int32) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for calls.Load() < want && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if got := calls.Load(); got != want {
		t.Fatalf("calls = %d, want %d", got, want)
	}
}

// TestCallWithClock_RecoversAfterRetryableError: an Unavailable answer is retried
// and the next value is returned.
func TestCallWithClock_RecoversAfterRetryableError(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	type result struct {
		value int
		err   error
	}
	done := make(chan result, 1)
	go func() {
		value, err := callWithClock(context.Background(), func(context.Context) (int, error) {
			if calls.Add(1) == 1 {
				return 0, status.Error(codes.Unavailable, "starting")
			}
			return 42, nil
		}, clk)
		done <- result{value: value, err: err}
	}()
	waitCalls(t, &calls, 1)
	if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
		t.Fatalf("waiting for the retry timer: %v", err)
	}
	clk.Advance(pollEvery)
	got := <-done
	if got.err != nil || got.value != 42 || calls.Load() != 2 {
		t.Fatalf("call = (%d, %v) after %d calls, want (42, nil) after 2", got.value, got.err, calls.Load())
	}
}

// TestCallWithClock_PermanentErrorStopsAtOnce: InvalidArgument is not retried.
func TestCallWithClock_PermanentErrorStopsAtOnce(t *testing.T) {
	t.Parallel()
	var calls atomic.Int32
	_, err := callWithClock(context.Background(), func(context.Context) (int, error) {
		calls.Add(1)
		return 0, status.Error(codes.InvalidArgument, "bad request")
	}, clockwork.NewFakeClock())
	if status.Code(err) != codes.InvalidArgument || calls.Load() != 1 {
		t.Fatalf("call err = %v after %d calls, want InvalidArgument after 1", err, calls.Load())
	}
}

// TestCallWithClock_ExhaustsAtMaxRetries: a permanently unavailable controller
// stops at maxRetries.
func TestCallWithClock_ExhaustsAtMaxRetries(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	done := make(chan error, 1)
	go func() {
		_, err := callWithClock(context.Background(), func(context.Context) (int, error) {
			calls.Add(1)
			return 0, status.Error(codes.Unavailable, "down")
		}, clk)
		done <- err
	}()
	for want := int32(1); want < maxRetries; want++ {
		waitCalls(t, &calls, want)
		if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
			t.Fatalf("waiting for retry timer %d: %v", want, err)
		}
		clk.Advance(pollEvery)
	}
	waitCalls(t, &calls, maxRetries)
	if err := <-done; err == nil || calls.Load() != maxRetries {
		t.Fatalf("call err = %v after %d calls, want exhaustion after %d", err, calls.Load(), maxRetries)
	}
}

// TestCallWithClock_CancellationDuringWait: cancelling the caller ends the
// retry wait without another call.
func TestCallWithClock_CancellationDuringWait(t *testing.T) {
	t.Parallel()
	clk := clockwork.NewFakeClock()
	var calls atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := callWithClock(ctx, func(context.Context) (int, error) {
			calls.Add(1)
			return 0, status.Error(codes.Unavailable, "down")
		}, clk)
		done <- err
	}()
	waitCalls(t, &calls, 1)
	if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
		t.Fatalf("waiting for the retry timer: %v", err)
	}
	cancel()
	if err := <-done; err == nil || calls.Load() != 1 {
		t.Fatalf("call err = %v after %d calls, want cancellation after 1", err, calls.Load())
	}
}
