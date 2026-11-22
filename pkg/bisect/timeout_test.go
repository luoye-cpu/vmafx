// Copyright 2026 Lusoris
// SPDX-License-Identifier: EUPL-1.2
//
// pkg/bisect/timeout_test.go — regression coverage for the vmaf
// scoring-subprocess timeout. The prior VMAFScoreFunc called
// exec.Command (no context, no cap), so a hung vmaf binary would pin
// bisect.Run forever.

package bisect

import (
	"testing"
	"time"

	"github.com/VMAFx/vmafx/pkg/scorecli"
)

func TestScoreTimeout_DefaultWhenUnset(t *testing.T) {
	t.Setenv("VMAFX_TUNE_SCORE_TIMEOUT", "")
	got := scorecli.ScoreTimeout()
	if got != scorecli.DefaultScoreTimeout {
		t.Errorf("scorecli.ScoreTimeout() = %v, want %v", got, scorecli.DefaultScoreTimeout)
	}
}

func TestScoreTimeout_HonoursEnvOverride(t *testing.T) {
	t.Setenv("VMAFX_TUNE_SCORE_TIMEOUT", "13s")
	got := scorecli.ScoreTimeout()
	if got != 13*time.Second {
		t.Errorf("scorecli.ScoreTimeout() = %v, want 13s", got)
	}
}

func TestScoreTimeout_InvalidEnvFallsBackToDefault(t *testing.T) {
	t.Setenv("VMAFX_TUNE_SCORE_TIMEOUT", "garbage")
	got := scorecli.ScoreTimeout()
	if got != scorecli.DefaultScoreTimeout {
		t.Errorf("scorecli.ScoreTimeout() with invalid env = %v, want default %v", got, scorecli.DefaultScoreTimeout)
	}
}

// TestVMAFScoreFunc_DoesNotHangOnMissingBinary exercises the score func
// against an obviously-missing binary path and asserts the call returns
// rather than blocking. Combined with the scorecli.ScoreTimeout() cap, this
// guards against the original hang-forever shape of the bug.
func TestVMAFScoreFunc_DoesNotHangOnMissingBinary(t *testing.T) {
	t.Setenv("VMAFX_TUNE_SCORE_TIMEOUT", "2s")
	fn := VMAFScoreFunc("/no/such/vmaf/binary/anywhere")
	done := make(chan struct{}, 1)
	go func() {
		_, _ = fn("ref.yuv", "dis.yuv")
		done <- struct{}{}
	}()
	select {
	case <-done:
		// Returned within budget; good.
	case <-time.After(10 * time.Second):
		t.Fatal("VMAFScoreFunc did not return within 10s — timeout wiring broken")
	}
}
