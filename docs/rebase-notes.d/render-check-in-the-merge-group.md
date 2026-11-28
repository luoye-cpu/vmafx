## Check the render in the merge group (2026-11-23)

- Keep full history for the Docs job on `push` and `merge_group`. The
  merge-group step passes the event base SHA through `env` to
  `scripts/ci/check_render_on_master.py --group-base`.
- Check the last render commit on its own tree so fragment landings after
  it can still owe a render. The opener repairs an inexact last render
  immediately. See [ADR-3627](adr/3627-render-check-in-the-merge-group.md).
- Tests: `scripts/ci/tests/test_check_render_on_master.py`.
