#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Freeze existing Accepted, Superseded and Deprecated ADR bodies.

Allow status lines, References sections, appended dated status-update sections,
new supersession notes directly below the status block, link target repairs,
whole-line markdownlint directives, and whitespace-only reflow. An ADR-Body-Edit
trailer of at least 20 characters on a commit in the range overrides refusals only in
the ADRs whose ADR-NNNN id its reason names; a reason that names no ADR id is refused.
Compare the base tip with the proposed merge tree.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import importlib.util
import os
import re
import shutil
import sys
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
safe_subprocess = importlib.import_module("scripts.lib.safe_subprocess")
run = safe_subprocess.run
GIT = shutil.which("git") or "git"
SECTION_LEVEL = 2
FROZEN = {"accepted", "superseded", "deprecated"}
REASON_MIN_LENGTH = 20

spec = importlib.util.spec_from_file_location(
    "adr_status_drift", Path(__file__).with_name("check-adr-status-drift.py")
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load ADR status parser")
status_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status_module)
STATUS_RE = status_module.STATUS_RE
ADR = re.compile(r"^docs/adr/[0-9]{4}-[^/]*\.md$")
ADR_ID = re.compile(r"\bADR-([0-9]{4})\b")
UPDATE = re.compile(r"^Status update \d{4}-\d{2}-\d{2}(?:\b|$)")
QUOTE = re.compile(r" {0,3}>[ ]?")
SUCCESSOR = re.compile(r"> Superseded by \[ADR-([0-9]{4})\]\((\1-[a-z0-9]+(?:-[a-z0-9]+)*\.md)\)")
DEPRECATION = re.compile(r"> Deprecated: [^.!?\s][^.!?]*[.!?]")
DIRECTIVE = re.compile(
    r"^<!--\s*markdownlint-"
    r"(?:disable|enable|disable-line|disable-next-line|capture|restore)\b[^>]*-->\s*$"
)
LINK_TITLE = (
    r"""(?:[ \t]+(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\((?:[^()\\\n]|\\.)*\)))?[ \t]*"""
)
LINK_END = re.compile(LINK_TITLE + r"\)")
DEFINITION_END = re.compile(LINK_TITLE + r"$")
DEFINITION = re.compile(r"^ {0,3}\[(?:\\.|[^\]\\])+\]:[ \t]*")
MARKDOWN = MarkdownIt("commonmark")
MAP_ENDS = 2
MESSAGE = (
    "body of an Accepted, Superseded or Deprecated ADR is frozen (docs/adr/_index_fragments/_header.md); "
    'append a dated "### Status update YYYY-MM-DD" section or supersede it with a new ADR'
)
RENAME_MESSAGE = (
    "Accepted, Superseded or Deprecated ADR cannot be renumbered or moved out of docs/adr/NNNN-*.md"
)


def git_status(*args: str, accepted: tuple[int, ...] = (0,)) -> tuple[int, str]:
    """Keep the operation and revisions in every bounded Git failure."""
    operation = "git " + " ".join(args)
    try:
        result = run(
            [GIT, *args],
            allowed_executables=(GIT,),
            capture_output=True,
            text=True,
            timeout_seconds=120,
            check=False,
        )
    except (OSError, RuntimeError, safe_subprocess.CommandValidationError) as exc:
        raise RuntimeError(f"{operation}: {exc}") from exc
    if result.returncode not in accepted:
        raise RuntimeError(f"{operation}: {result.stderr.strip()}")
    return result.returncode, str(result.stdout)


def git(*args: str) -> str:
    return git_status(*args)[1]


@dataclass
class Line:
    text: str
    depth: int
    kind: str = "prose"
    level: int = 0
    title: str = ""
    references: bool = False
    definition: bool = False
    literal: list[tuple[int, int]] = field(default_factory=list)

    @property
    def update(self) -> bool:
        return self.depth == 0 and self.level in (2, 3) and bool(UPDATE.match(self.title))

    @property
    def ignored(self) -> bool:
        return self.kind == "prose" and (
            not self.text.strip(" \t") or DIRECTIVE.fullmatch(self.text) is not None
        )


def container(text: str, limit: int) -> tuple[int, str]:
    # CommonMark 0.31.2 §5.1: each explicit marker opens one quote container.
    text = text.expandtabs(4)  # CommonMark 0.31.2 §2.2: block indentation uses tab stops.
    pos = depth = 0
    for _ in range(min(limit, len(text))):
        match = QUOTE.match(text, pos)
        if not match:
            break
        pos = match.end()
        depth += 1
    return depth, text[pos:]


def mark_heading(line: Line, level: int, title: str) -> None:
    line.kind = "heading"
    line.level = level
    line.title = title


def mark_block(lines: list[Line], tokens: list[Token], index: int, token: Token) -> None:
    """Copy one Markdown block token's line classification onto its lines."""
    mapping = token.map
    if not mapping:
        return
    start, stop = mapping
    stop = min(stop, len(lines))
    if start >= stop:
        return
    block_type = token.type
    if block_type == "heading_open":
        level = int(token.tag[1:])
        title = tokens[index + 1].content if index + 1 < len(tokens) else ""
        for line in lines[start:stop]:
            mark_heading(line, level, title)
    elif block_type in {"fence", "code_block", "html_block"}:
        for line in lines[start:stop]:
            if line.kind == "prose":
                line.kind = block_type


def mark_references(lines: list[Line], env: dict[str, object]) -> None:
    """Copy CommonMark's reference-definition maps onto their source lines."""
    references = env.get("references")
    if not isinstance(references, dict):
        return
    for value in references.values():
        if not isinstance(value, dict):
            continue
        span = value.get("map")
        if not isinstance(span, list) or len(span) != MAP_ENDS:
            continue
        start, stop = int(span[0]), min(int(span[1]), len(lines))
        for line in lines[start:stop]:
            line.definition = True


def classify(text: str) -> list[Line]:
    """Map every source line to the CommonMark block that owns it."""
    source = text.removesuffix("\n").split("\n") if text else []
    lines: list[Line] = []
    for raw in source:
        line_text = raw.removesuffix("\r")
        depth, _ = container(line_text, len(line_text))
        lines.append(Line(line_text, depth))
    env: dict[str, object] = {}
    tokens = MARKDOWN.parse(text, env)
    for index, token in enumerate(tokens):
        mark_block(lines, tokens, index, token)
    mark_references(lines, env)
    for line in lines:
        if line.kind == "html_block" and DIRECTIVE.fullmatch(line.text):
            line.kind = "prose"
    annotate(lines)
    return lines


def code_spans(text: str) -> Iterator[tuple[int, int]]:
    # CommonMark 0.31.2 §6.1: the first equal-length run closes a code span.
    runs = list(re.finditer(r"`+", text))
    following: dict[int, int] = {}
    spans = []
    for i in reversed(range(len(runs))):
        length = len(runs[i][0])
        # A backslash before an opener escapes just the first backtick.
        skip = int(escaped(text, runs[i].start()))
        closer = following.get(length - skip)
        if closer is not None:
            spans.append((runs[i].start() + skip, runs[closer].end()))
        following[length] = i
    end = 0
    for left, right in reversed(spans):
        if left >= end:
            yield left, right
            end = right


def escaped(text: str, pos: int) -> bool:
    start = pos
    for i in range(pos - 1, -1, -1):
        if text[i] != "\\":
            break
        start = i
    return (pos - start) % 2 == 1


def mark_spans(lines: list[Line]) -> None:
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line.text) + 1)
    for left, right in code_spans("\n".join(line.text for line in lines)):
        first = bisect_right(starts, left) - 1
        last = bisect_right(starts, right - 1) - 1
        for i in range(first, last + 1):
            line = lines[i]
            line.literal.append((max(0, left - starts[i]), min(len(line.text), right - starts[i])))


def annotate(lines: list[Line]) -> None:
    references = False
    paragraph = 0
    for i, line in enumerate(lines):
        if line.level and not line.depth and (line.level <= SECTION_LEVEL or line.update):
            references = line.level == SECTION_LEVEL and line.title == "References"
        line.references = references
        boundary = line.kind != "prose" or line.ignored
        if boundary or (i and lines[i - 1].depth != line.depth):
            mark_spans(lines[paragraph:i])
            if line.kind == "heading":
                mark_spans([line])
            paragraph = i + int(boundary)
    mark_spans(lines[paragraph:])


def metadata(lines: list[Line]) -> tuple[int, str]:
    for i, line in enumerate(lines):
        if line.level == SECTION_LEVEL and not line.depth:
            break
        match = STATUS_RE.match(line.text) if line.kind == "prose" and not line.depth else None
        if match:
            return i, match[1].lower()
    return -1, ""


def destination_end(text: str, start: int) -> int | None:
    # CommonMark 0.31.2 §6.3: bracketed or balanced unbracketed destinations.
    angle = text.startswith("<", start)
    depth = 0
    for token in re.finditer(r"\\[!-/:-@\[-`{-~]|.", text[start + int(angle) :]):
        char = token[0]
        pos = start + int(angle) + token.start()
        if angle:
            if char == ">":
                return pos + 1
            if char == "<":
                return None
        elif char in " \t" or char[0] < " " or (char == ")" and not depth):
            return None if depth else pos
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
    return None if angle or depth else len(text)


@dataclass(frozen=True)
class Link:
    start: int
    end: int
    target: int
    target_end: int


def parsed_link(text: str, start: int, target: int, suffix: re.Pattern[str]) -> Link | None:
    target_end = destination_end(text, target)
    if target_end is None:
        return None
    match = suffix.match(text, target_end)
    return Link(start, match.end(), target, target_end) if match else None


def links(line: Line) -> Iterator[Link]:
    if line.kind in {"fence", "code_block", "html_block"}:
        return
    text = line.text
    # Mask code spans in the bracket scan, including code inside a link label.
    masked = list(text)
    for a, b in line.literal:
        masked[a:b] = "x" * (b - a)
    syntax = "".join(masked)
    # §4.7: preserve the existing single-line link reference definition repair.
    definition = DEFINITION.match(text) if line.definition else None
    if definition and DEFINITION.match(syntax):
        link = parsed_link(text, 0, definition.end(), DEFINITION_END)
        if link and link.target < link.target_end:
            yield link
            return
    stack: list[int] = []
    consumed = 0
    for match in re.finditer(r"\[|\](?:\([ \t]*)?", syntax):
        if match.start() < consumed or escaped(text, match.start()):
            continue
        if match[0] == "[":
            stack.append(match.start())
        elif stack:
            start = stack.pop()
            if match[0] != "]":
                link = parsed_link(text, start, match.end(), LINK_END)
                if link:
                    yield link
                    consumed = link.end
                    stack.clear()


def repair_key(line: Line, *, retained: bool = False) -> tuple[str, ...]:
    """Omit destinations. Ignore surrounding inline whitespace only for retained entries."""
    parts = []
    start = 0
    for link in links(line):
        prefix = line.text[start : link.target]
        start = link.target_end
        if retained and prefix.rstrip(" \t").endswith("]("):
            prefix = prefix.rstrip(" \t")
            suffix = line.text[start : link.end]
            start += len(suffix) - len(suffix.lstrip(" \t"))
        parts.append(prefix)
    return (*parts, line.text[start:])


def words(lines: list[Line]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for line in lines:
        pos = 0
        protected = sorted([*line.literal, *((link.start, link.end) for link in links(line))])
        for a, b in [*protected, (len(line.text), len(line.text))]:
            if a < pos:
                continue
            part = line.text[pos:a]
            if line.text.strip().startswith("|"):
                part = re.sub(r"\s*\|\s*", "|", part)
                if re.fullmatch(r"[|:-]+", part):
                    part = re.sub(r"-+", "-", part)
            result.extend(("word", word) for word in part.split())
            if b > a:
                result.append(("literal", line.text[a:b]))
            pos = b
    return result


def formatting(before: list[Line], after: list[Line]) -> bool:
    if all(line.kind == "prose" for line in before + after) and words(before) == words(after):
        return True
    return len(before) == len(after) and all(
        a.kind == b.kind
        and a.kind != "fence"
        and a.level == b.level
        and repair_key(a) == repair_key(b)
        for a, b in zip(before, after, strict=True)
    )


def note_start(lines: list[Line], status: int) -> int | None:
    """Locate a note immediately below the status metadata."""
    if status < 0:
        return None
    for start in range(status + 1, len(lines)):
        text = lines[start].text
        if text.strip() and not re.match(r"^- \*\*[^*]+\*\*:", text):
            return start if text == "> [!NOTE]" and start + 1 < len(lines) else None
    return None


def note_range(lines: list[Line], start: int | None, state: str, head: str, path: str) -> range:
    """Check the entire blockquote and resolve its successor in the outgoing revision."""
    if start is None:
        return range(0)
    successor = SUCCESSOR.fullmatch(lines[start + 1].text)
    if not successor:
        return range(0)
    end = start + 2
    if state == "deprecated" and end < len(lines) and DEPRECATION.fullmatch(lines[end].text):
        end += 1
    # §5.1: an unquoted paragraph continuation can still belong to the quote.
    if end < len(lines) and (
        lines[end].depth or (lines[end].text.strip() and lines[end].kind == "prose")
    ):
        return range(0)
    target = "docs/adr/" + successor[2]
    entry = git("ls-tree", head, "--", target)
    if target == path or not entry.startswith(("100644 blob ", "100755 blob ")):
        return range(0)
    return range(start, end)


def updates(lines: list[Line]) -> bool:
    level = 0
    for line in lines:
        if line.update and (not level or line.level <= level):
            level = line.level
        elif not level or (line.level and line.level <= level):
            return False
    return bool(level)


def reference(line: Line) -> bool:
    return line.references and not line.level


def retained_prefix(old: list[Line], new: list[Line], update: int) -> bool:
    """Retained entries precede the appended update, in their original order."""
    entries = [
        repair_key(line, retained=True) for line in old if reference(line) and not line.ignored
    ]
    identities = set(entries)
    tail = [repair_key(line, retained=True) for line in new[update:] if not line.ignored]
    if any(key in identities for key in tail):
        return False
    prefix = [
        repair_key(line, retained=True)
        for line in new[:update]
        if reference(line) and not line.ignored
    ]
    kept = [key for key in prefix if key in identities]
    candidates = iter(entries)
    return kept == prefix[: len(kept)] and all(key in candidates for key in kept)


def permitted(old: list[Line], new: list[Line], a: int, b: int, c: int, d: int) -> bool:
    before = [line for line in old[a:b] if not line.ignored]
    after = [line for line in new[c:d] if not line.ignored]
    if formatting(before, after):
        return True
    ref_edit = (
        all(reference(line) for line in before) if before else bool(a and old[a - 1].references)
    )
    if ref_edit and all(reference(line) for line in after):
        return True
    if (before and not ref_edit) or any(not line.ignored for line in old[b:]):
        return False
    update = next((i for i in range(c, d) if new[i].update), d)
    prefix = [line for line in new[c:update] if not line.ignored]
    return (not prefix or (ref_edit and all(reference(line) for line in prefix))) and (
        updates([line for line in new[update:d] if not line.ignored])
        and retained_prefix(old, new, update)
    )


def classification(line: Line) -> tuple[object, ...]:
    return (
        line.kind,
        line.depth,
        line.level,
        line.references and not line.ignored,
        line.definition,
        line.literal,
    )


def refused(
    old: list[Line], new: list[Line], frozen_successor: int
) -> Iterator[tuple[int, str, str]]:
    diff = difflib.SequenceMatcher(
        None, [x.text for x in old], [x.text for x in new], autojunk=False
    )
    for tag, a, b, c, d in diff.get_opcodes():
        if tag == "equal":
            if all(
                classification(x) == classification(y)
                for x, y in zip(old[a:b], new[c:d], strict=True)
            ):
                continue
        elif a <= frozen_successor < b:
            yield c + 1, "Status", "supersession note must name another regular ADR file"
            continue
        elif permitted(old, new, a, b, c, d):
            continue
        heading = next(
            (line.text for line in reversed(old[: a + 1]) if line.level and not line.depth),
            "(preamble)",
        )
        yield c + 1, heading, MESSAGE


def body_problems(
    old: list[Line], new: list[Line], head: str, path: str
) -> Iterator[tuple[int, str, str]]:
    a, previous = metadata(old)
    b, current = metadata(new)
    note = note_range(new, note_start(new, b), current, head, path)
    if previous != current:
        if current not in {"superseded", "deprecated"} or not note:
            transition = f"{previous} -> {current or '(missing)'}"
            message = f"refused status transition {transition}; supersession note required"
            yield max(1, b + 1), "Status", message
        elif a >= 0:
            new[b] = old[a]
    old_start = note_start(old, a)
    frozen_successor = -1
    if old_start is not None and SUCCESSOR.fullmatch(old[old_start + 1].text):
        if not note:
            # Link repair must not rewrite an existing note to an invalid successor.
            frozen_successor = old_start + 1
    elif note:
        new = new[: note.start] + new[note.stop :]
    yield from refused(old, new, frozen_successor)


def changes(base: str, tree: str) -> Iterator[tuple[str, str, str]]:
    fields = git("diff", "--name-status", "-z", "-M", base, tree).split("\0")
    cursor = 0
    while cursor < len(fields) - 1:
        kind, path = fields[cursor : cursor + 2]
        cursor += 2
        target = path
        if kind.startswith(("R", "C")):
            target = fields[cursor]
            cursor += 1
        yield kind, path, target


def frozen_at(tree: str, path: str) -> bool:
    if not git("ls-tree", tree, "--", path):
        return False
    return metadata(classify(git("show", f"{tree}:{path}")))[1] in FROZEN


def file_problems(
    base: str, head: str, tree: str, fork: str, ancestor: str, kind: str, path: str, target: str
) -> Iterator[tuple[int, str, str]]:
    old = classify(git("show", f"{base}:{path}"))
    if metadata(old)[1] not in FROZEN and not frozen_at(fork, ancestor):
        return
    if target != path and (
        not ADR.fullmatch(target) or Path(target).name[:4] != Path(path).name[:4]
    ):
        yield 1, path, RENAME_MESSAGE
    if kind == "D":
        yield 1, path, MESSAGE
        return
    yield from body_problems(old, classify(git("show", f"{tree}:{target}")), head, target)


class InvalidReason(Exception):
    """The override trailer does not explain its reason sufficiently."""


def override(base: str, head: str) -> dict[str, tuple[str, str]]:
    """Map each ADR number a trailer in the checked range names to its commit and reason."""
    output = git(
        "log", "--format=%H%x00%(trailers:key=ADR-Body-Edit,unfold=true)%x00", f"{base}..{head}"
    )
    fields = output.split("\0")
    waivers: dict[str, tuple[str, str]] = {}
    for index in range(0, len(fields) - 1, 2):
        sha, trailers = fields[index].strip(), fields[index + 1].strip()
        for trailer in trailers.splitlines():
            reason = trailer.partition(":")[2].strip()
            if len(reason) < REASON_MIN_LENGTH:
                raise InvalidReason(
                    f"{sha}: ADR-Body-Edit reason must contain at least 20 characters"
                )
            numbers = ADR_ID.findall(reason)
            if not numbers:
                raise InvalidReason(
                    f"{sha}: ADR-Body-Edit reason must name each waived ADR as ADR-NNNN"
                )
            for number in numbers:
                waivers.setdefault(number, (sha[:10], reason))
    return waivers


def merge_result(base: str, head: str) -> tuple[str, list[str]]:
    status, output = git_status(
        "merge-tree", "--write-tree", "--name-only", "-z", base, head, accepted=(0, 1)
    )
    tree = output.partition("\0")[0]
    if status == 0:
        return tree, []
    # Git 2.56.1 file/directory fixture: tree NUL path~side NUL NUL, then
    # 2 NUL path~side NUL path NUL type NUL message NUL; further records follow.
    records = output.partition("\0\0")[2].split("\0")
    cursor = 0
    found = False
    paths: set[str] = set()
    while cursor < len(records) - 1:
        count = records[cursor]
        if not re.fullmatch(r"[0-9]+", count):
            break
        kind = cursor + 1 + int(count)
        if kind + 2 >= len(records):
            break
        if records[kind].startswith("CONFLICT"):
            found = True
            paths.update(path for path in records[cursor + 1 : kind] if ADR.fullmatch(path))
        cursor = kind + 2
    if not found or cursor != len(records) - 1 or records[-1]:
        raise RuntimeError(
            f"missing or malformed merge-tree conflict record between {base} and {head}"
        )
    return tree, sorted(paths)


def check(base: str, head: str) -> int:
    """Check changed ADRs, including renames, against their base versions."""
    git("rev-parse", "--verify", f"{base}^{{commit}}")
    git("rev-parse", "--verify", f"{head}^{{commit}}")
    merge_base = git("merge-base", base, head).strip()
    if not merge_base:
        raise RuntimeError(f"no merge base for {base} and {head}")
    tree, conflicts = merge_result(base, head)
    if conflicts:
        for path in conflicts:
            print(f"{path}: merge conflict between {base} and {head}")
        return 1
    ancestors = {
        target: path for kind, path, target in changes(merge_base, base) if kind.startswith("R")
    }
    waivers = override(base, head)
    failures: list[tuple[str, str]] = []
    for kind, path, target in changes(base, tree):
        if not ADR.fullmatch(path) or path.endswith("/0000-template.md") or kind == "A":
            continue
        number = Path(path).name[:4]
        for line, heading, message in file_problems(
            base, head, tree, merge_base, ancestors.get(path, path), kind, path, target
        ):
            failures.append((number, f"{target}:{line}: {heading}: {message}"))
    return report(failures, waivers)


def report(failures: list[tuple[str, str]], waivers: dict[str, tuple[str, str]]) -> int:
    """Print refusals and waived hunks; return 1 when a refusal has no waiver."""
    for number, failure in failures:
        print(f"note: {failure}" if number in waivers else failure)
    for number, (sha, reason) in sorted(waivers.items()):
        accepted = sum(1 for item, _ in failures if item == number)
        if accepted:
            print(
                f"ADR body freeze: ADR-{number}: {accepted} hunk(s) accepted under "
                f"ADR-Body-Edit in {sha}: {reason}"
            )
    return int(any(number not in waivers for number, _ in failures))


def check_revision(base: str, head: str, ref: str = "") -> int:
    try:
        if ref:
            status, _ = git_status(
                "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}", accepted=(0, 1)
            )
            if status:
                print(f"ADR body freeze: skipping {ref}: no commit", file=sys.stderr)
                return 0
            print(f"ADR body freeze: checking {ref}", file=sys.stderr)
        return check(base, head)
    except InvalidReason as exc:
        print(f"ADR body freeze: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ADR body freeze: {exc}", file=sys.stderr)
        return 2


OID = r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})"
PUSH_REF = re.compile(rf"(\S+) ({OID}) (\S+) ({OID})")


def check_push_refs(base: str) -> int:
    lines = sys.stdin.readlines()
    if not lines:
        print("ADR body freeze: no outgoing refs", file=sys.stderr)
    seen: set[str] = set()
    result = 0
    for number, line in enumerate(lines, 1):
        match = PUSH_REF.fullmatch(line.removesuffix("\n"))
        if not match or len(match[2]) != len(match[4]):
            print(f"ADR body freeze: malformed push protocol line {number}", file=sys.stderr)
            result = 2
            continue
        local, sha, remote, _ = match.groups()
        sha = sha.lower()
        if sha in seen or not sha.strip("0"):
            continue
        seen.add(sha)
        ref = local if local == remote else f"{local} -> {remote}"
        result = max(result, check_revision(base, sha, f"{ref} ({sha[:10]})"))
        sys.stdout.flush()
    return result


def main() -> int:
    """Parse CLI arguments and run the ADR body freeze check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    revision = parser.add_mutually_exclusive_group(required=True)
    revision.add_argument("--head")
    revision.add_argument(
        "--head-from-env",
        action="store_true",
        help="manual invocation only: use PRE_COMMIT_TO_REF when set, otherwise HEAD",
    )
    revision.add_argument(
        "--push-refs-stdin", action="store_true", help="check each outgoing pre-push ref on stdin"
    )
    args = parser.parse_args()
    try:
        if args.push_refs_stdin:
            return check_push_refs(args.base)
        head = args.head
        if args.head_from_env:
            head = os.environ.get("PRE_COMMIT_TO_REF")
            if not head:
                head = "HEAD"
                print(
                    "ADR body freeze: PRE_COMMIT_TO_REF unset (not a push); checking HEAD",
                    file=sys.stderr,
                )
        return check_revision(args.base, head)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ADR body freeze: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
