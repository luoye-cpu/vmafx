- **Documentation site:** CI scans five built pages and a real search with
  visible results over local HTTP in both palettes, using locked `pa11y-ci`
  and axe-core dependencies. WCAG 2.2 `target-size`, WCAG warnings and notices
  are included. A four-rule planted fixture must fail first; retained exceptions
  need a dated reason and unused exceptions fail (issue 2552).
