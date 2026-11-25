- **Python tests can remove scratch `.git` directories on Windows.** Read-only
  Git objects no longer stop the cleanup. The FFmpeg patch-stack cache reset
  now removes the old bare repository even when it contains read-only objects.
