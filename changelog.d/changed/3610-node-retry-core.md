- **vmafx-node uses `core/retry.Do` for bounded report delivery.**
  `controllerClient.report`, the controller-HA driver RPC helper and the
  observability smoke test (`tools/obssmoke`) poll now share the injected
  `core/clock` clock and retry policy; the deliberately context-bounded
  RegisterNode, PullWork and sidecar reconnect loops stay hand-written with
  the reason documented next to them.
