- **CI:** When master has moved and the fresh render differs from the queued
  one, the `docs-render-pr` workflow now dequeues the queued render pull
  request, confirms removal and pushes the fresh render. A failed queue query
  stops the push. When master's render is already current, it closes the stale
  render pull request instead of pushing an empty commit, dequeuing first on
  not-due closes too.
