# Documentation accessibility check

The docs build runs `scripts/docs/check_accessibility.py` after
`mkdocs build --strict`. It scans with the locked
[pa11y-ci](https://github.com/pa11y/pa11y-ci) and axe-core toolchain against
WCAG 2.2 A and AA, in the light and dark palettes. Pa11y's `WCAG2AA` preset
covers WCAG 2.1; the configuration explicitly adds axe's `target-size` rule
for WCAG 2.2 AA. Errors, warnings and notices are all checked. The two best-practice-only ids
`aria-dialog-name` and `landmark-unique` are printed as advisory; WCAG warnings
and notices still fail. This automated
check is not a conformance claim or a substitute for a manual or screen-reader pass.

## What is scanned

- the home page;
- `backends/sycl/overview/` (a backend page);
- `metrics/psnr/` (a metric page);
- `development/netflix-benchmark-baselines/` (a page with generated charts);
- an ADR page;
- the real Material search dialog on the home page, after entering `psnr` and
  waiting for a visible result item.

The checker starts a temporary HTTP server bound to `127.0.0.1` on an available
port, so search can load its index and worker. It shuts down the server after
the scan. A search action or page-load failure is a finding. The same six
scenarios run twice: once with the default palette and once with
`--force-dark-mode`, because `mkdocs.yml` selects the palette with
`prefers-color-scheme`. Each site scenario also selects the corresponding
Material palette radio and waits for the expected body scheme, so a palette
that failed to switch cannot silently pass.

Material manages overflowing code-block keyboard access. Our additional script
adds a tab stop only to a table's overflowing `.md-typeset__scrollwrap`, removes
its own stop when resizing removes overflow, and preserves author-set attributes.

## Run it locally

Use Node.js 24 or later and an installed Chrome or Edge. The committed npm lock
pins transitive scanner dependencies. Installation disables lifecycle scripts
and does not download a browser. CI uses the hosted runner's Chrome and prints
its version; local runs must also name the browser explicitly.

```bash
mkdocs build --strict
npm ci --prefix tools/accessibility --ignore-scripts --no-audit --no-fund
python3 scripts/docs/check_accessibility.py --site build-docs/site \
  --chrome /usr/bin/google-chrome
```

Pass `--node /path/to/node` if Node.js is not on `PATH`. `--pa11y-ci` can override
the default locked JavaScript entry point for diagnostics.

The check first runs `scripts/docs/accessibility-fixture.html`, which contains
an image without alt text, low-contrast text, an `h1` followed by
an `h3`, and two adjacent 16-by-16-pixel buttons. All four rules (`image-alt`,
`color-contrast`, `heading-order`, `target-size`) must report actual violations;
an incomplete result requiring review does not prove fixture detection.

Run the focused regression tests without a browser:

```bash
python3 -B scripts/ci/tests/test_docs_accessibility.py
node tools/accessibility/test-scroll-focus.cjs
```

The scanner returns 0 for a clean scan, 1 for findings or unused exceptions,
and 2 for missing inputs, invalid exceptions, or a negative control that failed
to detect a planted rule.

## Exceptions

Fix a finding rather than listing it. When a deviation cannot be fixed in this
change, add one entry to `scripts/docs/accessibility-allowlist.json`:

```json
{
  "page": "metrics/psnr/index.html",
  "rule": "color-contrast",
  "selector": "html > body > p:nth-child(3)",
  "reason": "why the deviation is retained and who owns it",
  "expires": "2027-01-31"
}
```

Every entry needs a reason and an expiry date. An optional `"scope": "incomplete"`
limits an exception to axe results that need review; an actual violation at the
same selector still fails. Results that axe cannot determine automatically remain
findings unless an exact, dated exception applies; an incomplete result does not
prove either a contrast violation or a passing contrast ratio. The checker fails
on an expired entry and on an entry that matches no issue, so the list cannot
silently rot.
The home hero keeps its gradient: axe cannot calculate contrast over that
background. Its dated exceptions record measured foreground-to-stop contrast
ratios, rather than flattening the design to satisfy the scanner. The
known-limitations section of `ACCESSIBILITY.md` records the same state.
