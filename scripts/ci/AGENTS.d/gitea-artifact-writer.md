---
paths:
  - scripts/ci/check_gitea_artifact_steps.py
  - scripts/ci/tests/test_check_gitea_artifact_steps.py
  - docs/rebase-notes.d/3052-ruamel-yaml.md
invariant: Round-trip byte gate outer; source-step cap, boolean guards, shell quoting, atomic replace, v3 twin repair intact.
area: gates
---
<!-- markdownlint-disable MD013 MD060 -->
# Gitea artifact writer (`check_gitea_artifact_steps.py`)

`scripts/ci/check_gitea_artifact_steps.py --write` edits tracked workflows in
place. The parser is ruamel.yaml round-trip mode; every file whose unchanged
dump differs from its captured bytes is refused rather than reformatted. The
byte gate is the outer contract: a rewrite may add only the guard/twin lines
and the trailing-comment moves that the fixtures pin.

`if:` is a string expression, not a boolean passthrough. A source `if: false`
must become `(false)` in both the guard and the twin; dropping it makes a
disabled artifact step run. The `if:` update keeps its original mapping
position, so the step order remains reviewable.

A skip twin exists when a v4+ input has no v3 equivalent. Its warning is built
with `shlex.quote`; the input names are data and never a shell fragment. The
generated `run:` stays a literal block so Gitea receives one command.

`MAX_STEPS` bounds v4+ source steps, not inserted twins. A file over the cap is
refused explicitly; neither `check_steps` nor `write_file` may examine a prefix
and leave a shifted source unguarded.

`write_file` compares the final bytes with the captured source before replacing
the target, and writes through a same-directory temp file plus `os.replace`.
A concurrent edit wins; an interruption or replace failure leaves the original
file intact. An adjacent malformed v3 twin is replaced in place; an unrelated
next step remains a step and the twin is inserted before it. Do not regress the
full-tree replay test: it deletes every accepted twin and requires byte-for-byte
reproduction.
