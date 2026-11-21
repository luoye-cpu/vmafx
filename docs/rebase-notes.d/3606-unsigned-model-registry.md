## Tiny-model registry bundle metadata stays optional (2026-11-21)

`fix/issue-3606-unsigned-registry`, [ADR-0211](../adr/0211-model-registry-sigstore.md).

- Keep `sigstore_bundle` optional for unsigned registry entries. A declared path must remain under
  the registry directory and name an existing `.sigstore.json` file; runtime verification stays
  opt-in and fails closed when the bundle field or file is missing.
- Preserve each model's `license` metadata when removing stale bundle declarations.
  `python/test/model_registry_schema_test.py` guards the shipped rows.
- Native release-archive provenance does not replace per-model bundles; signing follow-up remains
  tracked by [#1246](https://github.com/vidiko/vmafx/issues/1246).
