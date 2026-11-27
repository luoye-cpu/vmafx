## The lefthook commit-msg stage (2026-11-28)

no rebase impact beyond the two files themselves: the change adds a
`commit-msg:` block to `lefthook.yml` and a `commit-msg` case branch to
`scripts/git-hooks/framework-hooks.sh`; no file shared with Netflix/vmaf
changes. Keep the `run:` value quote-free on one line (ADR-2012, enforced by
`test_run_values_survive_the_windows_command_line`) and keep the `hook-impl`
entry identical to the ADR-1241 dispatcher's commit-msg path
(`test_commit_msg_runs_the_framework_stage_on_the_message_file`), or the two
checkout setups diverge and the squash-merge judge's check re-anchors to a
different command shape.
