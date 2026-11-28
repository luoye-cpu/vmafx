## Scorecard gate trusts v5.7.0 (2026-11-25)

- `scripts/ci/scorecard_gate.py`: `VERSION`, `TOOL_COMMIT` and
  `ACTION_COMMIT` name Scorecard v5.7.0 and `ossf/scorecard-action` v2.6.0.
  Fork-only, nothing upstream to merge.
- **On sync or a later bump**: Renovate opens the `ossf/scorecard-action` bump
  in its own reviewed pull request (`OpenSSF Scorecard action (gate identity)`
  in `renovate.json`), which is where `VERSION`, `TOOL_COMMIT` and
  `ACTION_COMMIT` move together with `scorecard.yml` and `security-scans.yml`;
  `test_every_scan_uses_the_action_the_gate_reviewed` fails otherwise. Compare
  `docs/checks/internal/checks.yaml` of both tool commits before keeping
  `WEIGHTS`.
