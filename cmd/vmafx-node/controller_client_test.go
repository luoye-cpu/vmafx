// SPDX-License-Identifier: EUPL-1.2
// Copyright 2026 Lusoris
//
// cmd/vmafx-node/controller_client_test.go — behaviour of the node's
// controller client against the in-process fake controller
// (controller_fake_test.go): the pull/score/report loop, registration retries,
// re-registration after a refused session, report retries and the shutdown
// drain.

package main

import (
	"context"
	"errors"
	"log/slog"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	googlegrpc "google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	"github.com/jonboulle/clockwork"

	grpcmod "github.com/golusoris/golusoris/grpc"

	controllerv1 "github.com/VMAFx/vmafx/gen/go/controller"
	"github.com/VMAFx/vmafx/pkg/controllerclient"
)

func scoringJob(id string) *controllerv1.Job {
	return &controllerv1.Job{Id: id, Scoring: &controllerv1.ScoringParams{Reference: "/r.y4m", Distorted: "/d.y4m"}}
}

func constantResult(score float64) fakeExecutor {
	return fakeExecutor{fn: func(context.Context, *controllerv1.Job) ExecuteResult {
		return ExecuteResult{Score: score, Features: map[string]float64{"vmaf": score}}
	}}
}

// TestControllerClient_PullScoreReport: a pulled job is executed and its score
// reported as a final result under the current session (positive).
func TestControllerClient_PullScoreReport(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onPull = oneJob(scoringJob("job-1"), "")
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(42.5))

	r := awaitReport(t, f, 5*time.Second)
	if r.GetJobId() != "job-1" || !r.GetFinal() || r.GetScore() != 42.5 || r.GetError() != "" {
		t.Fatalf("report = %+v, want final job-1 score 42.5 without error", r)
	}
	if r.GetNodeId() != "node-1" || r.GetSessionToken() != "tok-1" {
		t.Fatalf("report carried node %q token %q, want node-1 tok-1", r.GetNodeId(), r.GetSessionToken())
	}
	if r.GetFeatures()["vmaf"] != 42.5 {
		t.Fatalf("features = %v, want vmaf 42.5", r.GetFeatures())
	}
}

// TestControllerClient_ExecutorErrorReportedAsFailure: a failed job reaches the
// controller as a final result with the error text (negative).
func TestControllerClient_ExecutorErrorReportedAsFailure(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onPull = oneJob(scoringJob("job-err"), "")
	exec := fakeExecutor{fn: func(context.Context, *controllerv1.Job) ExecuteResult {
		return ExecuteResult{Error: errors.New("vmaf binary failed")}
	}}
	startTestClient(t, testControllerConfig(serveFake(t, f)), exec)

	r := awaitReport(t, f, 5*time.Second)
	if !strings.Contains(r.GetError(), "vmaf binary failed") || r.GetScore() != 0 {
		t.Fatalf("report = %+v, want the executor error and no score", r)
	}
}

// TestControllerClient_RegisterRetries: RegisterNode is retried with backoff
// until the controller answers, then work flows.
func TestControllerClient_RegisterRetries(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onRegister = func(call int) error {
		if call <= 2 {
			return status.Error(codes.Unavailable, "controller starting")
		}
		return nil
	}
	f.onPull = oneJob(scoringJob("job-r"), "")
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(1))

	awaitReport(t, f, 10*time.Second)
	if got := f.callCount("RegisterNode"); got != 3 {
		t.Fatalf("RegisterNode calls = %d, want 3 (two refusals, one success)", got)
	}
}

// TestControllerClient_ReregistersAfterHeartbeatRejected: a heartbeat answered
// ok=false makes the node register again and use the new session.
func TestControllerClient_ReregistersAfterHeartbeatRejected(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onHeartbeat = func(call int) bool { return call != 1 }
	f.onPull = oneJob(scoringJob("job-hb"), "tok-2")
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(7))

	r := awaitReport(t, f, 10*time.Second)
	if r.GetSessionToken() != "tok-2" || r.GetNodeId() != "node-2" {
		t.Fatalf("report under %q/%q, want the second session node-2/tok-2", r.GetNodeId(), r.GetSessionToken())
	}
}

// TestControllerClient_ReregistersAfterPullRefused: PullWork refused with
// PermissionDenied drops the session; the node registers again.
func TestControllerClient_ReregistersAfterPullRefused(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	job := oneJob(scoringJob("job-pd"), "tok-2")
	f.onPull = func(call int, token string) (*controllerv1.Job, error) {
		if call == 1 {
			return nil, status.Error(codes.PermissionDenied, "session expired")
		}
		return job(call, token)
	}
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(3))

	if r := awaitReport(t, f, 10*time.Second); r.GetSessionToken() != "tok-2" {
		t.Fatalf("report under %q, want tok-2", r.GetSessionToken())
	}
	if got := f.callCount("RegisterNode"); got < 2 {
		t.Fatalf("RegisterNode calls = %d, want a second registration", got)
	}
}

// reportClient returns a started-session client on a fake clock without
// launching the pull/heartbeat loops, so report() can be driven directly.
func reportClient(t *testing.T, f *fakeController, clk clockwork.Clock) *controllerClient {
	t.Helper()
	conn, err := googlegrpc.NewClient(serveFake(t, f), googlegrpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial fake controller: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	capability, err := nodeCapability("cpu", 1)
	if err != nil {
		t.Fatalf("nodeCapability: %v", err)
	}
	resp, err := f.RegisterNode(context.Background(), &controllerv1.RegisterNodeRequest{Name: "test-node"})
	if err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}
	c := newControllerClient(testControllerConfig(""), controllerv1.NewVmafxControllerClient(conn),
		fakeExecutor{fn: func(context.Context, *controllerv1.Job) ExecuteResult { return ExecuteResult{} }},
		capability, slog.New(slog.DiscardHandler), clk)
	c.sessions.set(resp.GetNodeId(), resp.GetSessionToken())
	return c
}

// TestControllerClient_ReportExhaustsAtMaxAttempts: eight retryable failures
// stop at maxReportAttempts.
func TestControllerClient_ReportExhaustsAtMaxAttempts(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onReport = func(int) error { return status.Error(codes.Internal, "storage down") }
	clk := clockwork.NewFakeClock()
	c := reportClient(t, f, clk)
	done := make(chan struct{})
	go func() {
		c.report("job-exhaust", ExecuteResult{Score: 1})
		close(done)
	}()
	for want := 1; want < maxReportAttempts; want++ {
		waitReportCalls(t, f, want)
		if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
			t.Fatalf("waiting for report timer %d: %v", want, err)
		}
		clk.Advance(reportRetryMax)
	}
	waitReportCalls(t, f, maxReportAttempts)
	<-done
	if got := f.callCount("ReportResult"); got != maxReportAttempts {
		t.Fatalf("ReportResult calls = %d, want %d", got, maxReportAttempts)
	}
}

// TestControllerClient_ReportDeliversOnLastAttempt: seven failures then success
// are all used, and the result is delivered on attempt eight.
func TestControllerClient_ReportDeliversOnLastAttempt(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onReport = func(call int) error {
		if call < maxReportAttempts {
			return status.Error(codes.Internal, "storage down")
		}
		return nil
	}
	clk := clockwork.NewFakeClock()
	c := reportClient(t, f, clk)
	done := make(chan struct{})
	go func() {
		c.report("job-last", ExecuteResult{Score: 2})
		close(done)
	}()
	for want := 1; want < maxReportAttempts; want++ {
		waitReportCalls(t, f, want)
		if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
			t.Fatalf("waiting for report timer %d: %v", want, err)
		}
		clk.Advance(reportRetryMax)
	}
	waitReportCalls(t, f, maxReportAttempts)
	<-done
	if got := f.callCount("ReportResult"); got != maxReportAttempts {
		t.Fatalf("ReportResult calls = %d, want %d", got, maxReportAttempts)
	}
	if r := awaitReport(t, f, time.Second); r.GetJobId() != "job-last" {
		t.Fatalf("delivered job %q, want job-last", r.GetJobId())
	}
}

// TestControllerClient_ReportStopDuringWait: stopping the node during the retry
// wait makes no further call and does not wait out the timer.
func TestControllerClient_ReportStopDuringWait(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onReport = func(int) error { return status.Error(codes.Internal, "storage down") }
	clk := clockwork.NewFakeClock()
	c := reportClient(t, f, clk)
	done := make(chan struct{})
	go func() {
		c.report("job-stop", ExecuteResult{Score: 3})
		close(done)
	}()
	waitReportCalls(t, f, 1)
	if err := clk.BlockUntilContext(context.Background(), 1); err != nil {
		t.Fatalf("waiting for report timer: %v", err)
	}
	c.cancelKeep()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("report did not stop during the retry wait")
	}
	if got := f.callCount("ReportResult"); got != 1 {
		t.Fatalf("ReportResult calls = %d, want 1", got)
	}
}

func waitReportCalls(t *testing.T, f *fakeController, want int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for f.callCount("ReportResult") < want && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if got := f.callCount("ReportResult"); got != want {
		t.Fatalf("ReportResult calls = %d, want %d", got, want)
	}
}

// TestControllerClient_ReportRetriesTransientFailure: an Unavailable report is
// retried and delivered.
func TestControllerClient_ReportRetriesTransientFailure(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onPull = oneJob(scoringJob("job-rr"), "")
	f.onReport = func(call int) error {
		if call == 1 {
			return status.Error(codes.Unavailable, "controller restarting")
		}
		return nil
	}
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(9))

	if r := awaitReport(t, f, 10*time.Second); r.GetJobId() != "job-rr" {
		t.Fatalf("report for %q, want job-rr", r.GetJobId())
	}
	if got := f.callCount("ReportResult"); got != 2 {
		t.Fatalf("ReportResult calls = %d, want 2", got)
	}
}

// TestControllerClient_ReportNotRetriedOnInvalidArgument: a malformed-request
// refusal is final; the client does not hammer the controller (negative).
func TestControllerClient_ReportNotRetriedOnInvalidArgument(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onPull = oneJob(scoringJob("job-ia"), "")
	f.onReport = func(int) error { return status.Error(codes.InvalidArgument, "bad job id") }
	startTestClient(t, testControllerConfig(serveFake(t, f)), constantResult(1))

	deadline := time.Now().Add(5 * time.Second)
	for f.callCount("ReportResult") == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	time.Sleep(2 * reportRetryBase)
	if got := f.callCount("ReportResult"); got != 1 {
		t.Fatalf("ReportResult calls = %d, want exactly 1", got)
	}
}

// TestControllerClient_ShutdownReportsInterruptedJob: a job still running at
// the stop deadline is cancelled and reported as failed, not left RUNNING
// (boundary).
func TestControllerClient_ShutdownReportsInterruptedJob(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	f.onPull = oneJob(scoringJob("job-long"), "")
	started := make(chan struct{})
	exec := fakeExecutor{fn: func(ctx context.Context, _ *controllerv1.Job) ExecuteResult {
		close(started)
		<-ctx.Done()
		return ExecuteResult{Error: ctx.Err()}
	}}
	c := startTestClient(t, testControllerConfig(serveFake(t, f)), exec)
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("job never started")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	c.stop(ctx)

	r := awaitReport(t, f, time.Second)
	if !strings.Contains(r.GetError(), "node shutting down") {
		t.Fatalf("report error = %q, want the shutdown interruption", r.GetError())
	}
}

// TestControllerClient_SendsBearerTokenFromFile: the token file is attached as
// a Bearer header and re-read, so a rotated token is used on later calls.
func TestControllerClient_SendsBearerTokenFromFile(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	cfg := testControllerConfig(serveFake(t, f))
	cfg.Creds.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(cfg.Creds.TokenFile, []byte("first\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	conn, err := dialController(grpcmod.NewConnFactory(), cfg)
	if err != nil {
		t.Fatalf("dialController: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	rpc := controllerv1.NewVmafxControllerClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := rpc.RegisterNode(ctx, &controllerv1.RegisterNodeRequest{Name: "n"}); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}
	if err := os.WriteFile(cfg.Creds.TokenFile, []byte("second"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := rpc.RegisterNode(ctx, &controllerv1.RegisterNodeRequest{Name: "n"}); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}
	if got := f.authHeaders(); len(got) != 2 || got[0] != "Bearer first" || got[1] != "Bearer second" {
		t.Fatalf("authorization headers = %q, want [Bearer first, Bearer second]", got)
	}
}

// TestControllerClient_EmptyTokenFileFailsTheCall: an empty token file fails
// the RPC instead of sending an empty bearer (negative).
func TestControllerClient_EmptyTokenFileFailsTheCall(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	cfg := testControllerConfig(serveFake(t, f))
	cfg.Creds.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(cfg.Creds.TokenFile, []byte("  \n"), 0o600); err != nil {
		t.Fatal(err)
	}
	conn, err := dialController(grpcmod.NewConnFactory(), cfg)
	if err != nil {
		t.Fatalf("dialController: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err = controllerv1.NewVmafxControllerClient(conn).RegisterNode(ctx, &controllerv1.RegisterNodeRequest{Name: "n"})
	if err == nil || !strings.Contains(err.Error(), "is empty") {
		t.Fatalf("RegisterNode error = %v, want the empty-token-file refusal", err)
	}
	if f.callCount("RegisterNode") != 0 {
		t.Fatal("the call reached the controller without a token")
	}
}

// TestControllerClient_TLSTokenRefusedOverPlaintext: credentials that demand
// TLS are never sent over a plaintext connection (negative).
func TestControllerClient_TLSTokenRefusedOverPlaintext(t *testing.T) {
	t.Parallel()
	f := newFakeController()
	addr := serveFake(t, f)
	creds := controllerclient.Credentials{Token: "secret", TLS: true}.Bearer()
	conn, err := googlegrpc.NewClient(addr,
		googlegrpc.WithTransportCredentials(insecure.NewCredentials()), googlegrpc.WithPerRPCCredentials(creds))
	if err != nil {
		// grpc refuses the combination when the connection is created.
		if !strings.Contains(err.Error(), "transport level security") {
			t.Fatalf("NewClient error = %v, want the transport-security refusal", err)
		}
		return
	}
	t.Cleanup(func() { _ = conn.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err = controllerv1.NewVmafxControllerClient(conn).RegisterNode(ctx, &controllerv1.RegisterNodeRequest{Name: "n"})
	if err == nil {
		t.Fatal("a TLS-requiring token was sent over plaintext")
	}
	if got := f.authHeaders(); len(got) != 0 {
		t.Fatalf("controller saw authorization headers %q over plaintext", got)
	}
}

// TestFinalResultRequest_NonFiniteBecomesFailure: a NaN feature is reported as
// a failure naming it, not dropped (negative).
func TestFinalResultRequest_NonFiniteBecomesFailure(t *testing.T) {
	t.Parallel()
	req := finalResultRequest("j", ExecuteResult{Score: 50, Features: map[string]float64{"adm2": math.NaN()}})
	if !strings.Contains(req.GetError(), "feature adm2") || req.GetScore() != 0 || len(req.GetFeatures()) != 0 {
		t.Fatalf("request = %+v, want a failure naming feature adm2", req)
	}
	inf := finalResultRequest("j", ExecuteResult{Score: math.Inf(1)})
	if !strings.Contains(inf.GetError(), "score") {
		t.Fatalf("request = %+v, want a failure naming the score", inf)
	}
}

// TestRetryableReport covers the code classification.
func TestRetryableReport(t *testing.T) {
	t.Parallel()
	for code, want := range map[codes.Code]bool{
		codes.Unavailable: true, codes.DeadlineExceeded: true, codes.PermissionDenied: true,
		codes.Internal: true, codes.InvalidArgument: false, codes.NotFound: false,
		codes.FailedPrecondition: false, codes.Canceled: false,
	} {
		if got := retryableReport(status.Error(code, "x")); got != want {
			t.Errorf("retryableReport(%v) = %v, want %v", code, got, want)
		}
	}
}
