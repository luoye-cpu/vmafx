- Windows CI now accepts Renovate updates to `TheMrMilchmann/setup-msvc-dev` when every workflow uses the same full lowercase commit SHA. Partial updates still fail the toolset contract.
  The contract scans both `.yml` and `.yaml` files in `.github/workflows/`.
