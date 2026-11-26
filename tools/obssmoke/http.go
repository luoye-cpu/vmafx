// SPDX-License-Identifier: EUPL-1.2
// Copyright 2026 Lusoris

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/golusoris/golusoris/core/retry"
	"github.com/jonboulle/clockwork"
)

// requestTimeout bounds every HTTP call.
const requestTimeout = 30 * time.Second

// httpCall is one request; user and password set basic auth when user is
// not empty, body a JSON request body when not empty.
type httpCall struct {
	method, url, user, password, body string
}

// do sends c and returns the status and the body.
func (c httpCall) do(ctx context.Context) (int, []byte, error) {
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	var body io.Reader
	if c.body != "" {
		body = strings.NewReader(c.body)
	}
	req, err := http.NewRequestWithContext(ctx, c.method, c.url, body)
	if err != nil {
		return 0, nil, fmt.Errorf("%s %s: %w", c.method, c.url, err)
	}
	if c.body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.user != "" {
		req.SetBasicAuth(c.user, c.password)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("%s %s: %w", c.method, c.url, err)
	}
	defer closeLogged(resp.Body, "response body of "+c.url)
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return resp.StatusCode, nil, fmt.Errorf("%s %s: read body: %w", c.method, c.url, err)
	}
	return resp.StatusCode, raw, nil
}

// closeLogged closes c and logs a failure: nothing the smoke test checks
// depends on it, and the result has already been read.
func closeLogged(c io.Closer, what string) {
	if err := c.Close(); err != nil {
		slog.Warn("close", "what", what, "err", err)
	}
}

// getJSON decodes the JSON answer of a GET that must return 200.
func getJSON(ctx context.Context, c httpCall, out any) error {
	c.method = http.MethodGet
	status, raw, err := c.do(ctx)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("GET %s: HTTP %d: %.200s", c.url, status, raw)
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("GET %s: decode: %w", c.url, err)
	}
	return nil
}

// pollInterval and pollAttempts bound every wait of the smoke test.
const (
	pollInterval = 2 * time.Second
	pollAttempts = 150
)

// errPollPending lets retry.Do treat a check that is simply not ready yet as a
// retryable condition, without inventing a transport error.
var errPollPending = errors.New("condition not met")

// poll calls check until it reports done, at most pollAttempts times.
func poll(ctx context.Context, check func(context.Context) (bool, error)) error {
	return pollWithClock(ctx, check, clockwork.NewRealClock())
}

// pollWithClock is poll with the retry clock injected for tests.
func pollWithClock(
	ctx context.Context, check func(context.Context) (bool, error), clk clockwork.Clock,
) error {
	err := retry.Do(ctx, func(ctx context.Context) error {
		done, err := check(ctx)
		if done {
			return nil
		}
		if err != nil {
			return err
		}
		return errPollPending
	}, retry.Policy{
		Initial:     pollInterval,
		Max:         pollInterval,
		Multiplier:  1,
		MaxAttempts: pollAttempts,
	}, clk)
	if err != nil {
		return fmt.Errorf("poll: %w", err)
	}
	return nil
}
