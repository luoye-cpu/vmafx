- **The Python lock refresh keeps the release-bot token out of the job that runs
  the freshly compiled uv (#3702).** `.github/workflows/python-lock-refresh.yml`
  now regenerates the manifest locks in a secret-free `regenerate` job that stages
  the changed lock files as a workflow artifact, and a `publish-locks` job restores
  them under the manifest-output allow-list before minting the installation token
  and pushing; `lock_refresh_publish.py` gains `stage` and `restore` subcommands
  that keep the stray-path refusal in the regeneration job and read no environment
  variable (`StageRestoreTest`).
