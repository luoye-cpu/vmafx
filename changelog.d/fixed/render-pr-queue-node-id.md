- **CI:** The `docs-render-pr` pull request opener now requires a non-empty
  string pull request node ID in merge-queue GraphQL responses, stopping the
  run before any dequeue call when an answer is malformed.
