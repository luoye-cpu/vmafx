<!-- markdownlint-disable MD013 MD041 MD060 -->
# Research-3052: Gitea artifact writer round-trip invariants

Companion digest for [ADR-3628](../adr/3628-gitea-artifact-writer-fail-closed.md)
and [issue #3052](https://github.com/VMAFx/vmafx/issues/3052). It records why
`scripts/ci/check_gitea_artifact_steps.py` uses ruamel.yaml round-trip mode
and which failure modes the later hardening had to close.

## Problem

The Gitea v3 twin writer originally reconstructed each step block as lines and
re-indented it. That lost any comment, quoted scalar, anchor or flow mapping
the block contained. A workflow edit could therefore change unrelated YAML
semantics, and a file with comments could not be replayed byte-for-byte.

Issue #3052 moved the writer to ruamel.yaml 0.19.1 round-trip mode with
`preserve_quotes`, `width = 1000`, `explicit_start` and
`indent(mapping=2, sequence=4, offset=2)`. The writer now refuses a file whose
unchanged dump is not byte-identical, so a rewrite cannot silently reformat a
workflow.

## Reconciliation with the current writer

| Failure mode | Current behaviour | Evidence |
| --- | --- | --- |
| Explicit `---` after comments/blank/BOM | Prefix split before the marker, then prepend the prefix to the dump | `test_write_preserves_explicit_marker_after_comment` |
| `if: false` | Boolean becomes `(false)` in both guards, preserving disabled semantics | `test_write_preserves_if_false` |
| Unsupported `with` key in shell diagnostic | `shlex.quote` makes the whole message one shell argument | `test_skip_twin_quotes_unsupported_input_names` |
| Malformed adjacent v3 twin | Replaced with the canonical twin, not duplicated | `test_write_repairs_a_malformed_v3_twin` |
| `MAX_STEPS` counts inserted twins | Source v4+ steps are counted; over-cap files are refused | `test_check_steps_refuses_partial_scan_at_max_steps` |
| Concurrent editor | Final bytes are compared with the captured source; mismatch refuses the write | code review of `write_file` |
| Interrupted write | Same-directory temp file plus `os.replace`; failure leaves the original | `test_atomic_write_leaves_original_on_replace_failure` |

## Alternatives considered

| Option | Result |
| --- | --- |
| Line-and-indent reconstruction | Rejected: loses YAML syntax and cannot guarantee byte stability. |
| ruamel round-trip without the byte gate | Rejected: reformats unrelated bytes, hides the intended diff. |
| External formatter after ruamel | Rejected: a second style contract, not installed in the hook environment. |

## Verification

`python -m pytest -q scripts/ci/tests/test_check_gitea_artifact_steps.py`
covers the 24 pre-existing cases plus the explicit-marker, boolean, quoting,
twin-replacement, cap and atomic-write cases. `test_write_replays_every_deleted_twin`
deletes every accepted twin from the tracked workflows and requires the writer
to reproduce the original bytes.

The repository-wide replay remains the strongest guard: any change to ruamel
settings, twin field order or comment attachment that changes one byte is
caught by the full-tree case, not only by a fixture.
