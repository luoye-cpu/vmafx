# PR-body deliverables validator (pre-push hook)

To check a PR body before pushing, run `make pr-check PR=<number>`, run
`make pr-check BODY=<file> [TITLE=<text>]` (`TITLE` defaults to the `HEAD`
commit subject when empty), or install the pre-push hook with
`make hooks-install`. The title and body path are passed to the gates
literally without `make` or shell expansion, and `PR` must be a positive
decimal number. PR mode diffs the PR's base and head revisions, fetching them
from `origin` when missing, and fails when it cannot. In PR mode, the FFmpeg
surface gate also runs its tree checks against the PR head tree rather than the
local checkout; the head-tree check reads raw blobs, so archive attributes in
the PR cannot hide or rewrite inputs. `make pr-check` runs the
deliverables, `docs/state.md`
touch and `ffmpeg-patches` surface gates (`scripts/ci/pr-body-gates.sh`),
while the pre-push hook runs the deliverables parser, catching a malformed
body before a 3-10 minute CI cycle.

The [rule-enforcement workflow][rule-yml] runs a **deep-dive deliverables
checklist** gate (ADR-0108) on every non-draft PR. The parser is strict: a
tick that does not match the documented checkbox shape, or a label substring
that drifts by one character, fails the gate. For the user-facing syntax
(checkbox forms, opt-out sentences) read the
[PR body sentinel guide](pr-body-sentinel-guide.md); this page covers the
validator's internals and the hook.

## Numeric claims in drafts

Before posting a PR body or reply, compare its numeric tokens with one or more
evidence files:

```sh
python3 scripts/ci/draft_claims.py draft.md evidence.md [more-evidence.md ...]
```

The check reports tokens missing from the evidence and exits 1; unreadable or
over-1-MiB files and invalid usage exit 2. It ignores Markdown link destinations
(bare targets without whitespace up to three nested or escaped parenthesis
pairs, or `<...>` targets, plus optional single-line titles) that directly
follow a single-line link label in square brackets (one level of nested
bracket pairs, none directly followed by `(`), and single digits, and
normalizes thousands separators; malformed or more deeply nested targets,
parentheses after an escaped or unmatched `]`, and links whose `[` follows an
odd number of backslashes remain checked. Evidence files
are checked separately. A match confirms token presence only; verify that the
evidence supports each claim in context.

## What it checks

The hook reuses the parser in
[`scripts/ci/deliverables-check.sh`][deliv] verbatim — that script is
the single source of truth for both CI and local validation. The
parser enforces:

1. **Six deliverables, each addressed.** For every item below, the body
   must contain either a ticked checkbox (`- [x] **Item name** …`) or
   an opt-out sentence (`no <key> needed: <reason>` /
   `no rebase impact: <reason>` / `no rebase-sensitive invariants`).

   | Item                              | Opt-out key regex                  |
   |-----------------------------------|------------------------------------|
   | Research digest                   | `digest`                           |
   | Decision matrix                   | `alternatives`                     |
   | `AGENTS.md` invariant note        | `rebase-sensitive\|AGENTS`         |
   | Reproducer / smoke-test command   | `reproducer\|smoke`                |
   | CHANGELOG fragment                | `changelog`                        |
   | Rebase note                       | `rebase`                           |

2. **Ticked items reference real files.** When a ticked item names a
   file class, the corresponding path must appear in the PR diff:

   | Ticked item       | Required diff entry                              |
   |-------------------|--------------------------------------------------|
   | Research digest   | `^docs/research/[0-9]+-`                         |
   | CHANGELOG fragment| `^CHANGELOG\.md` or `^changelog\.d/<sec>/.*\.md` |
   | Rebase note       | `^docs/rebase-notes\.md$`                        |

3. **Toolchain claims backed by probe output.** Paste the output of
   `scripts/dev/toolchain-probe.sh` when claiming a toolchain is missing or a
   backend was skipped. See [toolchain claims and probe verification](#toolchain-claims-and-probe-verification)
   for the evidence required by `scripts/ci/check-toolchain-claims.sh`.

## Parser shape gotchas

These patterns have each failed real PRs under the strict parser:

- **Numbered-list shape fails.** `1. **Research digest** …` is not a
  checkbox — the parser only recognises `- [x]` (or `- [ ]`).
- **Bold-bracket label substring must be exact.** The label string
  inside the regex is matched case-insensitively but as a literal
  substring after markdown emphasis is stripped. `**Reproducer /
  smoke-test command**` matches; `**Reproducer / smoke-test**` (no
  trailing "command") does **not**.
- **A sentence next to a ticked box is redundant.** A ticked box
  satisfies the gate on its own. The opt-out sentence is only required
  when the box is unticked. Mixing both is harmless.
- **Sentinel without ticked-OR-unticked checkbox is fine.** A bare
  sentence anywhere in the body satisfies the opt-out branch. The
  PR template still strongly recommends pairing it with `- [ ]` for
  reviewer legibility.

## Installing the hook

Run `make hooks-install` (an alias of `make install-hooks`,
[ADR-1241](../adr/1241-worktree-hook-dispatch.md)). It needs `pre-commit` in
the active Python environment and runs
[`scripts/githooks/install.py`](../../scripts/githooks/install.py), which:

1. Validates `.pre-commit-config.yaml` and prepares the hook environments.
2. Writes the dispatcher `scripts/githooks/dispatch.sh` into `.git/hooks/` as
   `pre-commit`, `commit-msg`, `pre-push` and `pre-rebase` (a copy, not a
   symlink).
3. Preserves any existing managed hook next to it as
   `<hook>.vmafx-backup-*`, and refuses to touch a custom hook it does not
   own.

The PR-body check is the `validate-pr-body` hook in
[`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) (stage
`pre-push`). Its entry point is
[`scripts/git-hooks/pre-push-pr-body-lint.sh`](../../scripts/git-hooks/pre-push-pr-body-lint.sh);
the dispatcher reaches it through the pre-commit framework.

## Standalone CLI

For one-off checks without installing the hook:

```bash
# Body via stdin, diff auto-computed from origin/master..HEAD
gh pr view 260 --json body -q .body \
  | scripts/ci/validate-pr-body.sh

# Explicit body file + explicit diff file
git diff --name-only origin/master..HEAD > /tmp/diff.txt
scripts/ci/validate-pr-body.sh --body pr-body.md --diff /tmp/diff.txt
```

Exit codes:

| Code | Meaning                                                  |
|------|----------------------------------------------------------|
| 0    | PR body would pass the deliverables gate.                |
| 1    | PR body would fail (same `::error` lines as CI emits).   |
| 2    | Usage error — missing body, unreadable diff file, etc.   |

## Where the body comes from

Five gates read a PR body, and all five classify `fd 0` the same way,
through the shared helper [`scripts/ci/pr-body-input.sh`][input]:

| Gate                              | Rule enforced                    |
|-----------------------------------|----------------------------------|
| [`deliverables-check.sh`][deliv]  | ADR-0108 six deliverables        |
| `validate-pr-body.sh`             | the same parser, run locally     |
| `ffmpeg-patches-surface-check.sh` | ADR-0409 ffmpeg-patch surface    |
| `state-md-touch-check.sh`         | ADR-0165 `docs/state.md` hygiene |
| `check-toolchain-claims.sh`       | ADR-0108 toolchain claims probe  |

The body is resolved in this order:

1. `--body PATH` (`validate-pr-body.sh` only).
2. `$PR_BODY`. For `deliverables-check.sh`, `validate-pr-body.sh`, and
   `check-toolchain-claims.sh`, **set counts** (`${PR_BODY+x}`), even to the
   empty string, because setting it is the caller's answer. In
   `deliverables-check.sh` and `validate-pr-body.sh`, a blank `$PR_BODY` is
   reported against the variable (exit 1) rather than quietly replaced from
   `stdin`; in `check-toolchain-claims.sh`, a set but blank `$PR_BODY` makes no
   claims and exits 0. This is the path CI takes
   (`PR_BODY: ${{ github.event.pull_request.body }}`) and the path
   `make pr-check` takes. The surface and `state.md` gates instead take the
   variable only when it is **non-empty**: for them a blank `$PR_BODY` and a
   blank `stdin` end at the same empty body, so falling through costs nothing
   and lets an explicitly blank variable still be overridden by a real pipe.
3. `stdin`, **when fd 0 is a pipe, a regular file or a socket**.

What happens when none of those supplies a body differs by gate, because the
gates differ in what else they have to go on:

- `deliverables-check.sh`, `validate-pr-body.sh`, and
  `check-toolchain-claims.sh` exist only to parse a body. No body is a
  **usage error (exit 2)** naming what `fd 0` actually is; an open `stdin` pipe
  carrying no bytes also fails closed (`validate-pr-body.sh` and
  `check-toolchain-claims.sh` exit 2, and `deliverables-check.sh` exits 1).
- `ffmpeg-patches-surface-check.sh` and `state-md-touch-check.sh` also have a
  diff to check. No body makes their opt-out sentinel unclaimable, so they
  **name what fd 0 was and fall through to the diff** — passing when it is
  clean, failing when it is not. Neither is weakened by an absent body; both
  fail closed.

The classification matters because `[ ! -t 0 ]` — the test all four scripts
used until it was replaced — answers "is fd 0 something other than a
terminal", not "did anybody pipe a PR body":

| fd 0                    | `[ ! -t 0 ]` | Now                                          |
|-------------------------|--------------|----------------------------------------------|
| pipe / file with a body | true         | read (unchanged)                             |
| pipe carrying no bytes  | true         | fail closed: the producer sent nothing       |
| `/dev/null`             | true         | named as unreadable, never read as a body    |
| closed (`0<&-`)         | true         | named as closed, never read — used to hang   |
| terminal                | false        | named as a terminal (unchanged)              |

### Why fd 0 is classified

The closed case was the sharp one. `PR_BODY="$(cat)"` with fd 0 closed does
not fail, it **deadlocks**: the command substitution opens a pipe, the
kernel hands out the lowest free descriptor, with fd 0 free that pipe's
read end lands on fd 0, and `cat` reads the pipe it is writing to. Reproduce
the old behaviour on any pre-fix checkout with:

```bash
timeout 10 bash scripts/ci/deliverables-check.sh 0<&- ; echo $?            # 124
timeout 12 env -u PR_BODY bash -c \
    'bash scripts/ci/state-md-touch-check.sh 0<&-' ; echo $?               # 124
```

### Reading the classified stream

Reading the classified stream is a three-step contract, and the third step is
not optional:

1. `pr_body_classify_stdin` hands out a **duplicate** of fd 0. The duplicate
   makes the read safe, since no later command substitution can claim a
   descriptor that is already taken.
2. `pr_body_read_stdin` reads the duplicate.
3. `pr_body_close_stdin` releases it.

The release cannot be folded into the read. The read runs inside `$( )`, so
closing there would close the subshell's copy and leave the caller's open,
inherited by every `git`, `python3` and `mktemp` the gate spawns afterwards.

`scripts/ci/tests/test-pr-body-input-selection.sh` pins every row of that
table for all five gates, plus the descriptor release, under `timeout`, so a
re-regression is reported as a hang instead of becoming one.

## What the hook does on push

1. Resolves the current branch via `git rev-parse --abbrev-ref HEAD`.
2. Looks up the open PR for that branch with a bounded `gh pr view`
   (timeout `VMAFX_PR_LOOKUP_TIMEOUT_SECONDS`, default 15 s). If `gh` is not
   installed or fails, it falls back to the public GitHub pull-request pages.
3. **Fails closed** when neither source can establish the PR state: the push
   is blocked with `cannot determine PR state via gh or GitHub's public
   pages`. Restore connectivity or credentials and push again.
4. **Skips** (exit 0, with a message) in these cases, which mirror CI's own
   skip conditions so the hook is never stricter than CI:
   - the branch has no open PR (first push of a feature branch),
   - the PR is `MERGED` / `CLOSED`,
   - the PR is a draft (the `deep-dive-checklist` job has the same
     `pull_request.draft == false` predicate),
   - the PR is the machine-generated release-please PR (ADR-1151; needs
     `gh` metadata, the public-page fallback never exempts),
   - the PR is a strictly dependency-only bot PR, such as a Renovate update of
     `go.mod` and `go.sum` (ADR-1152: `scripts/ci/classify-dependency-pr.sh`
     with the author, head ref and changed files CI passes it; needs `gh`
     metadata, the public-page fallback never exempts),
   - the PR body is empty (CI will catch it),
   - `origin/master` is missing locally (run `git fetch origin master`).
5. Otherwise computes
   `git diff --name-only $(git merge-base origin/master HEAD)..HEAD` and
   feeds body and diff into `scripts/ci/validate-pr-body.sh`.
6. A non-zero exit blocks the push and prints the same `::error` lines the CI
   gate would emit.

## Bypassing

Do not skip the hook. AGENTS.md operational rule 6 forbids agents from
evading hooks, and the PR body is checked again in CI.

If `gh` authentication is broken, the public-page fallback usually still
reads the PR. When it cannot, fix connectivity or credentials and retry.

!!! note
    Only a human, in their own terminal, may skip pre-push checks with
    git's standard skip flag, and that skips **all** pre-push checks, not
    just this one. Correct the PR body (`gh pr edit --body-file <path>`)
    instead whenever possible.

## Caveats — local pass is **not** a guarantee

This validator passing locally is not a guarantee that the CI gate
will pass. CI is authoritative for two reasons:

- **Diff source differs.** CI uses
  `git diff --name-only ${BASE_SHA}..${HEAD_SHA}` from the PR object;
  the hook uses `git diff --name-only $(git merge-base origin/master
  HEAD)..HEAD`. These usually agree but can diverge on stale local
  `origin/master` refs. Run `git fetch origin master` before relying
  on the hook.
- **Body source differs.** The hook fetches the body via `gh pr view`
  *as last saved on GitHub*. If you have unsaved edits in a local
  draft, the hook will validate the stale upstream body instead.

When the local validator and CI disagree, treat CI as the truth and
file the divergence as a bug against this script.

## How the parser works internally

Implementation summary (read [`deliverables-check.sh`][deliv] for the
exact regexes):

1. Strip markdown emphasis characters (`` ` *_\ ``) so labels wrapped
   in backticks/asterisks/underscores collapse to plain text.
2. For each of the six items, run two regex probes:
   - `- \[x\].*<item-name>` — case-insensitive, item-name as literal
     substring.
   - `no .*(<opt-out-key-regex>)` — case-insensitive.
3. If a ticked-only item references a file class, additionally probe
   the diff for the expected `^docs/research/`, `^changelog\.d/`,
   or `^docs/rebase-notes\.md$` paths.

Failure modes emit `::error title=ADR-0108 …::<message>` lines so the
GitHub Actions log surfaces them as inline annotations.

[rule-yml]: ../../.github/workflows/rule-enforcement.yml
[deliv]: ../../scripts/ci/deliverables-check.sh
[input]: ../../scripts/ci/pr-body-input.sh

## Toolchain claims and probe verification

When contributors build on machines where toolchains live off `$PATH` (e.g.
Intel oneAPI under `/opt/intel/oneapi`, CUDA under `/opt/cuda/bin`, ROCm under
`/opt/rocm/bin`), asserting that a toolchain is absent or that code was not
compiled must be backed by evidence from the repository's probe script:

```bash
scripts/dev/toolchain-probe.sh
```

The script searches standard vendor directories and sources oneAPI's
`setvars.sh` in a subshell. Paste its output into the description as text.

```text
toolchain-probe v1 host=build-01 os=Linux date=2026-11-04T00:00:00Z
toolchain-probe: icpx found /opt/intel/oneapi/compiler/2026.0/bin/icpx ...
toolchain-probe: nvcc found /opt/cuda/bin/nvcc ...
toolchain-probe: metal absent searched=none os=Linux
```

The producer and checker share the v1 format in
`scripts/dev/toolchain-probe-format.sh`. The header carries the hostname
(ASCII letters, digits, dots, hyphens, and underscores), the kernel name from
`uname -s`, and a UTC timestamp in `YYYY-MM-DDTHH:MM:SSZ` format. Recognized
kernel names are `Linux`, `Darwin`, `FreeBSD`, `NetBSD`, `OpenBSD`, `DragonFly`,
`SunOS`, `AIX`, and the versioned Windows names beginning with `MINGW32_NT-`,
`MINGW64_NT-`, `MSYS_NT-`, or `CYGWIN_NT-`. The pasted block is
**unauthenticated**. Anyone can forge it. The checker catches claims with
missing or contradictory evidence. If `uname -n` contains other characters,
set `TOOLCHAIN_PROBE_HOST` to an identifying name in the accepted alphabet.
The probe validates the override before printing its header.

Probe rows count as evidence after a valid header in the same fence, or on
consecutive nonblank lines after an unfenced header. A `found` row carries a
percent-encoded path and optional free-form version text. An `absent` row
ends with one `searched=` field. The producer percent-encodes ASCII whitespace
and the percent sign, so `/tmp/oneAPI SDK%20` becomes `/tmp/oneAPI%20SDK%2520`.
The non-Darwin Metal row counts only with `searched=none os=<kernel>` matching
the header kernel.
The oneAPI compiler fallback inspects up to 50 candidates. If more remain,
its absence row ends in `,budget-exhausted`.
MinGW runtime paths use the same percent encoding. MinGW detail rows
(`dumpversion`, `thread-model`, `runtime`, `runtime-unresolved`, and `pairing*`)
describe the cross compiler. The checker skips valid probe rows inside a block
and checks claims in ordinary fenced text, blockquotes, same-line backtick
spans such as
`` ```nvcc``` `` (not a fence opener under CommonMark because the info string
contains a backtick), and prose mixed into probe output.

`scripts/ci/check-toolchain-claims.sh` splits each checked line into clauses at
semicolons, sentence ends (`.`, `!`, or `?` followed by whitespace), and
contrastive joins such as `, but`, `, while`, `, whereas`, or `, although`.
It checks each predicate with its own subject, trailing tool list, and host
qualifier. In `No SYCL here and the CI runner lacks icx`, the first predicate
requires a local `icpx` absence and the second requires an `icx` result.
Descriptions over 4096 lines or prose lines over 64 clauses produce a finding.

The checker recognizes `no <tool>`,
`(lacks|lacking) <tool>` (with an optional article), and
`<tool> (is|are|was|were)? <status>`, where `<status>` can be `not installed`,
`not found`, `not available`, `missing`, `unavailable`, or `absent`. It also
recognizes `without <tool>` with a nearby host qualifier (`on this host`,
`this host`, `on the host`, `here`, or `locally`), and compilation claims when
the subject names a tool or backend directly before `not compiled`,
`(was|were) not built`, or `compilation (was|were)? skipped`. Active forms
such as `did not compile CUDA` and `did not build HIP` take a following object.
One `toolchain_map` vocabulary in `scripts/ci/check-toolchain-claims.sh`
covers both absence and compilation claims: every probe name below plus
`cuda`, `cuda toolkit`, `sycl`, `oneapi`, `hip`, `hip cc`, `rocm`, and `vulkan`.
A hyphen joined to a name is part of the
word, so `tidy-metal` does not match `metal` while `x86_64-w64-mingw32-gcc`
matches as one name.

A `no <tool>` phrase counts as an absence claim unless it matches a change
description. In `no <tool> [objects] changed`, the verb can be `change`,
`changes`, `changed`, `edit`, `edits`, `edited`, `touched`, or `modified`.
Zero to three change objects may intervene. The checker's `change_objects`
list allows `build`, `flag`, `flags`, `option`, `options`, `code`, `source`,
`sources`, `file`, `files`, `path`, `paths`, `kernel`, `kernels`, `backend`,
`test`, `tests`, `config`, `setting`, `settings`, `dependency`, and
`dependencies`. For example, `no CUDA build flag changes` describes a change.
`Having no nvcc installed changed our build plan` still claims absence.
A change verb immediately before `no <tool>` also qualifies (`touch`,
`touches`, `touched`, `change`, `changes`, `changed`, `edit`, `edits`, `edited`,
`modify`, `modifies`, or `modified`).

The checker strips up to 32 change-scope matches per clause before checking
the remaining text. A second, unscoped mention of the same tool still needs
evidence. If a 33rd change-scope match remains, the checker uses the original
clause for evidence checking.

A runner, image, container, CI, or golden-build subject identifies a
non-host claim. For example, `I verified locally that the CI runner has no
nvcc` describes the runner. In `The runner on this host has no nvcc`, the
qualifier identifies the local host. Other absence claims default to the host.
A compilation claim needs host agreement when its own subject or qualifier
identifies the host.

Each segment needs **one probe block covering every tool it claims**. In
`No nvcc on build-01`, only blocks whose `host=` matches `build-01` count.
Use `host buildbox` or `on host buildbox` to name a host explicitly. After
`on`, `at`, or `for`, a backticked hostname also names a host. An unquoted
hostname in those positions needs a digit, dot, or hyphen. A trailing sentence
period is punctuation, so `on Linux.` leaves the host unnamed. A hostname of
the same shape directly before `has`, `have`, `lacks`, or `is` also identifies
the host, as in `build-01 has no nvcc`. Tool names and aliases in subject
position identify tools. The generic qualifiers `this host`, `the host`,
`host` alone, `this machine`, `my machine`, `here`, and `locally` leave the
host unnamed. A named host needs a header with the same name, compared
case-insensitively. Any matching block reporting a claimed tool found
contradicts that named-host claim. When a host claim leaves
the host unnamed, one complete agreeing block suffices if all headers name the
same host. If the headers name different hosts, name the probed host in the
claim. For a CI claim, a found compiler can supply the required result.

Missing-result diagnostics identify the closest eligible block (first in a
tie). Contradictions identify the block that reported the compiler found.
Both include its hostname and header line in the description.

For absence claims, the checker resolves the predicate's object and its
trailing tool list. In `I built with icx and this host lacks SYCL`, the object
`SYCL` requires an `icpx` result. In `no SYCL compiler icx` and
`no SYCL (icx)`, the explicit compiler requires an `icx` result. These
overrides apply within each family (`icpx`/`icx` for `sycl`/`oneapi`,
`nvcc` for `cuda`/`cuda toolkit`, `hipcc`/`hip cc` for `hip`/`rocm`,
and `glslc`/`glslangValidator` for `vulkan`).

| Probe result name | Claim words mapped to it |
| --- | --- |
| `icpx` | `icpx`, `sycl`, `oneapi` |
| `icx` | `icx` |
| `nvcc` | `nvcc`, `cuda`, `cuda toolkit` |
| `hipcc` | `hipcc`, `hip cc`, `hip`, `rocm` |
| `glslc` | `glslc`, `vulkan` |
| `glslangValidator` | `glslangValidator` |
| `metal` | `metal` |
| `x86_64-w64-mingw32-gcc` | `x86_64-w64-mingw32-gcc` |

`scripts/ci/deliverables-check.sh` runs the checker after body validation and
before deliverables sections that can exit early, including the render-only
route. `scripts/ci/validate-pr-body.sh` delegates to the same deliverables
script.

## History

The strict parser tripped PRs #461, #438, #470, #473, #486, #511, #468
and #526 on the shape patterns listed under "Parser shape gotchas". The fd 0
classification was added after a closed `fd 0` deadlocked all four body-reading
gates.
