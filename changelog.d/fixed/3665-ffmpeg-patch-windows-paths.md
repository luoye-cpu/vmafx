- **FFmpeg patch replay handles Windows paths.** `mirrors()` now substitutes the
  remote as a literal value, so paths such as `C:\ffmpeg\source` no longer raise
  `bad escape \U`; the replay fixtures write LF bytes so `git am` applies the
  generated patch. The new Windows-path test fails without the callable
  replacement.
