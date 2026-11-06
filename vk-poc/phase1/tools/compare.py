#!/usr/bin/env python3
"""Bit-exact comparator for the VK PoC Phase 1 ADM contract files
(see README.md, "Output contract").

Compares two contract text files produced by the CPU reference tool
(src/cpu_ref.c) and the VK path (src/vk_adm.c). Every payload field is a hex
float printed with printf("%a", (double)float_value); fields are compared as
EXACT strings -- a 1-ULP difference is a FAIL. Header lines starting with '#'
are ignored except for the '# fields:' line, which fixes the field order.
Everything after '|' (the human-readable decimal mirror) is ignored.

Usage:
    python -I tools/compare.py --ref <ref.txt> --test <test.txt> [--max-report N]
    python -I tools/compare.py --selftest

Exit codes:
    0 = bit-exact on every field of every frame
    1 = differences found (first N mismatches printed + summary counts)
    2 = usage or parse error (missing/invalid file, malformed line, field
        lists of the two files disagree, ...)

--selftest proves the comparator is not vacuous: it writes two synthetic
contract files in a temp dir and asserts identical->0, one-hex-digit-
flip->1(naming frame+field), one-frame-dropped->1, garbage->2.
"""

import argparse
import os
import sys
import tempfile
from collections import OrderedDict


class ParseError(Exception):
    pass


def parse_contract(path):
    """Parse one contract file.

    Returns (fields, frames) where fields is the list of field names taken
    from the '# fields:' header line (first entry is 'frame') and frames is an
    OrderedDict frame_index -> {field_name: value_string} for the value fields.
    """
    fields = None
    frames = OrderedDict()
    field_set = None

    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as e:
        raise ParseError("cannot open %s: %s" % (path, e.strerror or e))

    with fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                if line.startswith("# fields:"):
                    names = line[len("# fields:"):].split()
                    if not names or names[0] != "frame":
                        raise ParseError(
                            "%s:%d: '# fields:' must start with 'frame'"
                            % (path, lineno))
                    if len(set(names)) != len(names):
                        raise ParseError(
                            "%s:%d: duplicate field name in '# fields:'"
                            % (path, lineno))
                    if fields is not None and names != fields:
                        raise ParseError(
                            "%s:%d: multiple conflicting '# fields:' lines"
                            % (path, lineno))
                    fields = names
                    field_set = set(names)
                continue  # any other '#...' line is ignored

            # Payload line: drop the decimal mirror after '|'.
            payload = line.split("|", 1)[0].strip()
            tokens = payload.split()
            if not tokens or not tokens[0].startswith("frame="):
                raise ParseError(
                    "%s:%d: line must start with 'frame=<i>'" % (path, lineno))
            ftoken = tokens[0][len("frame="):]
            try:
                fnum = int(ftoken, 10)
            except ValueError:
                raise ParseError(
                    "%s:%d: bad frame index %r" % (path, lineno, ftoken))
            if fnum in frames:
                raise ParseError("%s:%d: duplicate frame=%d" % (path, lineno, fnum))

            vals = {}
            for tok in tokens[1:]:
                name, sep, value = tok.partition("=")
                if not sep or not name or not value:
                    raise ParseError(
                        "%s:%d: malformed token %r" % (path, lineno, tok))
                if field_set is None:
                    raise ParseError(
                        "%s:%d: '# fields:' header line not found before data"
                        % (path, lineno))
                if name not in field_set or name == "frame":
                    raise ParseError(
                        "%s:%d: token %r not declared in '# fields:' header"
                        % (path, lineno, tok))
                if name in vals:
                    raise ParseError(
                        "%s:%d: duplicate field %r in frame=%d"
                        % (path, lineno, name, fnum))
                vals[name] = value

            missing = [n for n in fields[1:] if n not in vals]
            if missing:
                raise ParseError(
                    "%s:%d: frame=%d missing fields: %s"
                    % (path, lineno, fnum, " ".join(missing)))
            frames[fnum] = vals

    if fields is None:
        raise ParseError("%s: no '# fields:' header line found" % path)
    if not frames:
        raise ParseError("%s: no frame lines found" % path)
    return fields, frames


def compare(ref_path, test_path, max_report):
    """Compare two contract files. Returns (mismatches, summary_lines)."""
    ref_fields, ref_frames = parse_contract(ref_path)
    test_fields, test_frames = parse_contract(test_path)

    if ref_fields != test_fields:
        raise ParseError(
            "field lists disagree:\n  ref : %s\n  test: %s"
            % (" ".join(ref_fields), " ".join(test_fields)))

    value_fields = ref_fields[1:]
    mismatches = []          # (frame, field, ref_value, test_value)
    frames_missing_in = []   # (frame, side)

    ref_keys = set(ref_frames)
    test_keys = set(test_frames)
    for f in sorted(ref_keys - test_keys):
        frames_missing_in.append((f, "test"))
    for f in sorted(test_keys - ref_keys):
        frames_missing_in.append((f, "ref"))

    for f in sorted(ref_keys & test_keys):
        for name in value_fields:
            rv = ref_frames[f][name]
            tv = test_frames[f][name]
            if rv != tv:
                mismatches.append((f, name, rv, tv))

    summary = []
    summary.append("ref=%s test=%s" % (ref_path, test_path))
    summary.append(
        "frames: ref=%d test=%d compared=%d (ref-only=%d, test-only=%d)"
        % (len(ref_frames), len(test_frames), len(ref_keys & test_keys),
           len(ref_keys - test_keys), len(test_keys - ref_keys)))
    if frames_missing_in:
        for f, side in frames_missing_in:
            mismatches.append((f, "<frame-missing>", "present", "absent-in-%s" % side))
    if mismatches:
        per_field = OrderedDict()
        for _, name, _, _ in mismatches:
            per_field[name] = per_field.get(name, 0) + 1
        frames_with_diff = sorted({m[0] for m in mismatches})
        summary.append("mismatches: total=%d frames_with_diff=%d fields=%s"
                       % (len(mismatches), len(frames_with_diff),
                          " ".join("%s=%d" % kv for kv in per_field.items())))
        for f, name, rv, tv in mismatches[:max_report]:
            summary.append("frame=%d field=%s ref=%s test=%s" % (f, name, rv, tv))
        if len(mismatches) > max_report:
            summary.append("... %d more mismatch(es) suppressed "
                           "(--max-report %d)"
                           % (len(mismatches) - max_report, max_report))
    else:
        summary.append("OK: bit-exact (%d frames x %d fields)"
                       % (len(ref_keys & test_keys), len(value_fields)))
    return mismatches, summary


def selftest():
    """Prove the gate is not vacuous (offline, synthetic fixtures only).

    Asserts: identical -> no mismatch; one flipped hex digit -> exactly one
    mismatch naming the frame and field; a dropped frame -> a mismatch;
    disagreeing '# fields:' lists / malformed lines / empty files -> ParseError.
    Returns 0 when every check passes, 1 otherwise.
    """
    fails = []
    ran = [0]

    def check(name, cond):
        ran[0] += 1
        print("selftest: %-44s %s" % (name, "ok" if cond else "FAIL"))
        if not cond:
            fails.append(name)

    hdr = ["# tool=selftest",
           "# fields: frame s0num s0den adm2"]

    def frame(i, s0num="0x1.d904dep+4"):
        return ("frame=%d s0num=%s s0den=0x1.f064p+4 adm2=0x1p+0"
                " | s0num=29.5636883 (decimal mirror, ignored)"
                % (i, s0num))

    base = hdr + [frame(i) for i in range(3)]
    flipped = hdr + [frame(0), frame(1, "0x1.d904cfp+4"), frame(2)]
    dropped = hdr + [frame(0), frame(2)]
    short_fields = ["# tool=selftest", "# fields: frame s0num s0den",
                    frame(0)]
    malformed = hdr + ["frame=x s0num=0x1p+0 s0den=0x1p+0 adm2=0x1p+0"]
    missing_value = hdr + ["frame=0 s0num=0x1p+0 adm2=0x1p+0"]
    empty = ["# tool=selftest"]

    def expect_parse_error(name, ref_lines, test_lines):
        with tempfile.TemporaryDirectory(prefix="adm-compare-selftest-") as d:
            rp = os.path.join(d, "ref.txt")
            tp = os.path.join(d, "test.txt")
            for p, lines in ((rp, ref_lines), (tp, test_lines)):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            try:
                compare(rp, tp, 10)
            except ParseError:
                check(name, True)
            else:
                check(name, False)

    with tempfile.TemporaryDirectory(prefix="adm-compare-selftest-") as d:
        def w(name, lines):
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            return p

        ref = w("ref.txt", base)
        m, _ = compare(ref, w("same.txt", base), 10)
        check("identical files -> 0 mismatches", not m)
        m, _ = compare(ref, w("flip.txt", flipped), 10)
        check("1-hex-digit flip -> exactly 1 mismatch", len(m) == 1)
        check("flip reported as frame=1 field=s0num",
              len(m) == 1 and m[0][0] == 1 and m[0][1] == "s0num")
        m, _ = compare(ref, w("drop.txt", dropped), 10)
        check("dropped frame -> frame-missing mismatch",
              any(x[1] == "<frame-missing>" and x[0] == 1 for x in m))

    expect_parse_error("disagreeing '# fields:' -> exit-2 path",
                       base, short_fields)
    expect_parse_error("malformed frame index -> exit-2 path",
                       base, malformed)
    expect_parse_error("missing value field -> exit-2 path",
                       base, missing_value)
    expect_parse_error("no frame lines -> exit-2 path", base, empty)

    if fails:
        print("selftest: %d of %d check(s) FAILED: %s"
              % (len(fails), ran[0], ", ".join(fails)), file=sys.stderr)
        return 1
    print("selftest: all %d checks passed" % ran[0])
    return 0


def main(argv):
    ap = argparse.ArgumentParser(
        description="Bit-exact comparator for VK PoC Phase 1 ADM contract files.")
    ap.add_argument("--ref", help="reference contract file (CPU)")
    ap.add_argument("--test", help="test contract file (VK)")
    ap.add_argument("--max-report", type=int, default=10, metavar="N",
                    help="print at most N mismatches (default 10)")
    ap.add_argument("--selftest", action="store_true",
                    help="run built-in tamper tests and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if args.ref or args.test:
            ap.error("--selftest takes no --ref/--test")
        return selftest()
    if not args.ref or not args.test:
        ap.error("--ref and --test are required (or use --selftest)")
    if args.max_report < 0:
        ap.error("--max-report must be >= 0")

    try:
        mismatches, summary = compare(args.ref, args.test, args.max_report)
    except ParseError as e:
        print("compare.py: parse error: %s" % e, file=sys.stderr)
        return 2

    for line in summary:
        print(line)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
