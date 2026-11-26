<!-- markdownlint-disable MD013 MD060 -->
# ADR-3628: Refuse lossy rewrites in the Gitea artifact writer

- **Status**: Accepted
- **Date**: 2026-11-26
- **Deciders**: Project maintainer
- **Tags**: `ci`, `gitea`, `yaml`, `tooling`

## Context

`scripts/ci/check_gitea_artifact_steps.py --write` edits tracked workflows in
place. The previous line-and-indent reconstruction lost comments, quoting,
anchors and flow mappings; issue #3052 replaced it with ruamel.yaml 0.19.1
round-trip mode. A round-trip gate now refuses a file whose unchanged dump is
not byte-identical, because silently reformatting a workflow is worse than
leaving the artifact finding for a human.

That gate still left failure modes that a malformed or concurrent input can
trigger: an explicit `---` marker after a leading comment was not detected;
`if: false` became an unguarded step; an unsupported `with` key was interpolated
into a single-quoted shell string; an invalid adjacent v3 twin could be
duplicated; a write could truncate the file before serialization completed; and
the step cap counted inserted twins rather than source steps. Each is a
correctness or security problem in the writer, not in a workflow it edits.

## Decision

The writer is fail-closed:

1. Split a BOM/comments/blank prefix from an explicit `---` marker before
   round-tripping, and prepend the prefix unchanged to the dump.
2. Preserve boolean YAML conditions (`if: false` -> `(false)` in both guards)
   instead of treating them as an absent condition.
3. Quote the unsupported-input diagnostic with `shlex.quote`, so an input name
   is one shell argument and cannot close the `echo` quoting.
4. Replace an adjacent malformed v3 twin with the canonical twin; never insert
   a second twin beside the old pin.
5. Cap the number of v4+ source steps, not inserted twins, and refuse a file
   that exceeds `MAX_STEPS` instead of checking or writing a prefix.
6. Refuse to overwrite a file whose bytes changed after the initial read, and
   write through a same-directory temporary file followed by `os.replace`.

The round-trip byte gate remains the outer condition: every path that cannot
round-trip unchanged is refused, and the caller reports the refusal.

## Alternatives considered

| Option | Pros | Cons | Why discarded |
| --- | --- | --- | --- |
| Keep line-and-indent reconstruction | No new dependency; simple diff. | Loses comments, quoting, anchors and flow mappings; cannot guarantee byte stability. | The writer must preserve review comments and YAML semantics. |
| ruamel.yaml round-trip with no byte gate | Preserves most syntax and can edit every file. | Silently reformats files whose dump differs; review noise hides the intended guard/twin change. | A rewrite that changes unrelated bytes is not reviewable in a CI-tooling PR. |
| External formatter after ruamel | One formatter for all YAML. | Adds a second tool and a second style contract; not installed in the hook environment. | ruamel already owns the parse/serialize cycle; a second pass would defeat the byte gate. |
| Detect limits by total step index | Simple counter. | Inserted twins consume the cap, so a shifted source step escapes checking/writing. | The cap must bound source work, not generated output. |

## Consequences

- A workflow with a leading comment before `---`, `if: false`, or a malformed
  adjacent v3 twin is repaired without losing its prefix or disabling state.
- An unsupported `with` key is reported as data, never executed by the skip
  step.
- A concurrent editor wins: the writer refuses rather than overwriting newer
  bytes; an interrupted write leaves the original file intact.
- A file above the source-step cap fails with an explicit diagnostic; it is not
  partially processed.
- The round-trip refusal remains intentional for flow mappings and BOM/comment
  layouts that ruamel cannot reproduce; those need a manual edit.

## References

- [Issue #3052](https://github.com/vidiko/vmafx/issues/3052), [PR #3669](https://github.com/vidiko/vmafx/pull/3669).
- [Research-3052](../research/3052-gitea-artifact-writer.md).
- [ADR-0108](0108-deep-dive-deliverables-rule.md) (deep-dive deliverables).
- `scripts/ci/tests/test_check_gitea_artifact_steps.py` covers the byte gate,
  prefix marker, `if: false`, shell quoting, twin repair, source-step cap and
  atomic replace.
