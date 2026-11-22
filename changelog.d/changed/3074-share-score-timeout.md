- Share the score-subprocess timeout: `pkg/scorecli` now exports
  `DefaultScoreTimeout` and `ScoreTimeout()`, and `pkg/bisect` and `pkg/fast`
  call it instead of keeping three copies of the 30-minute default and the
  `VMAFX_TUNE_SCORE_TIMEOUT` parser (#3074).
