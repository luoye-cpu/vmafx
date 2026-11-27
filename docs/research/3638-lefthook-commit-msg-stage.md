# Research digest: the lefthook commit-msg stage (issue #3638)

## Question

`lefthook.yml` owns `.git/hooks` (ADR-1249) but declared no `commit-msg`
hook, while `.pre-commit-config.yaml` runs conventional-pre-commit at the
`commit-msg` stage and the ADR-1241 dispatcher installs it for
`make install-hooks` checkouts. A lefthook-only checkout never validated
commit messages locally, and the squash-merge judge skipped the check
("declares no lefthook commit-msg hook on master"). How should lefthook run
the stage?

## Measurements

- `.pre-commit-config.yaml:6` installs `commit-msg` among the hook types and
  pins conventional-pre-commit v4.5.0 `--strict` at that stage; 58 of the
  config's 173 hooks declare `stages:` explicitly.
- `scripts/githooks/dispatch.sh` runs every framework hook type through
  `pre-commit hook-impl --config=.pre-commit-config.yaml --hook-type=<type>
  --hook-dir <dir> -- "$@"`, which for commit-msg takes the message file Git
  hands over as the positional argument. This path is exercised by every
  dispatcher-based checkout.
- In the commit-msg stage pre-commit has no changed-file set, so the
  stage-selective hooks run while the filename-filtered linters skip; the
  battery cost on a no-op message is the conventional check only.
- `lefthook` passes the message file to a commit-msg hook as its first
  positional argument, the same protocol Git uses, so `{1}` maps onto it
  directly.

## Decision

Delegate the stage through `framework-hooks.sh commit-msg {1}` and run the
framework's commit-msg stage with the `hook-impl` entry the dispatcher
already uses, rather than a second invocation form.

## Alternatives considered

| Option | Why not |
|---|---|
| `pre-commit run --hook-stage commit-msg --commit-msg-filename "$2"` | Works, but introduces a second invocation form for the same stage; hook-impl is what every dispatcher checkout already runs, keeps `.legacy` hook handling identical and needs no separate flag plumbing. |
| Run conventional-pre-commit directly from `lefthook.yml` | Bypasses the framework: skips `install-hooks`, the virtualenv resolution of `framework-hooks.sh`, the GIT-variable scrub and `.pre-commit-config.yaml` as the single source of the convention. |
| Teach only `make install-hooks` to write the dispatcher after `lefthook install` | Leaves the squash-merge judge (which reads `lefthook.yml`) skipping the check; the judge's skip is the acceptance criterion the issue names. |

## Evidence

- `scripts/githooks/tests/test_commit_msg_hook.py` drives the real framework
  offline through `framework-hooks.sh commit-msg` in a fixture repository: a
  planted non-conventional subject (`update stuff`) refuses, a conventional
  subject passes.
- `LefthookBridgeTests` pins the hook-impl command line and the missing-
  argument failure; `test_install.py` proves the installer keeps lefthook's
  `commit-msg` shim (`lefthook_owned` is content-based, so ownership follows
  automatically).
