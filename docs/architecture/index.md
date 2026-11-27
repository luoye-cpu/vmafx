<!-- markdownlint-disable MD013 MD060 -->
# Architecture

Use this page to find where a change belongs in the repository. The decision
table comes first; the full directory map and the architecture pages follow.

## What lives where (decision tree)

| Concern                                | Home                                          |
| -------------------------------------- | --------------------------------------------- |
| Add a SIMD path                        | `core/src/feature/<isa>/`                  |
| Add a GPU backend                      | `core/src/<backend>/` + `core/src/feature/<backend>/` |
| Add a feature extractor                | `core/src/feature/`                        |
| Add or change a CLI tool               | `core/tools/`                              |
| Ship a new VMAF model                  | `model/` (JSON/pkl) or `model/tiny/` (ONNX)   |
| Train a new tiny model                 | `ai/src/vmaf_train/models/`                   |
| Add or change an MCP tool              | `cmd/vmafx-mcp/` (Go) and `mcp-server/vmaf-mcp/` (Python); see [MCP tools](../mcp/tools.md) |
| Change the scoring service, controller, node or operator | `cmd/vmafx-*/`, shared code in `pkg/`, contracts in `proto/`, chart in `deploy/helm/vmafx/` |
| Rust bindings or Rust metric pilots    | `bindings/rust/`, `core/src/feature/rust/`    |
| Python harness scratch                 | `compat/python-vmaf/workspace/` (see [workspace.md](workspace.md)) |
| CI / release workflow                  | `.github/workflows/`                          |
| Coding standards / style               | [`docs/principles.md`](../principles.md)      |
| Public design and decision records     | [`docs/`](../) and [`docs/adr/`](../adr/)    |
| Private session continuity             | `.workingdir/` (ignored; never public authority) |
| Local datasets / reusable derived data | `.corpus/` (ignored)                         |

## Repository layout

The tree lists the directories a contributor meets most often. Run `ls` at
the repository root for the complete list.

```text
vmafx/
├── core/               # The C library + CLI. The product. (was libvmaf/, ADR-0700)
│   ├── src/            # metric engine, feature extractors
│   │   ├── feature/    # per-feature CPU kernels
│   │   │   ├── x86/    # AVX2 / AVX-512 SIMD paths
│   │   │   ├── arm64/  # NEON SIMD paths
│   │   │   ├── cuda/   # CUDA kernels
│   │   │   ├── sycl/   # SYCL kernels
│   │   │   ├── hip/    # HIP kernels
│   │   │   ├── metal/  # Metal kernels
│   │   │   └── rust/   # Rust metric pilots (tad)
│   │   ├── cuda/       # CUDA backend runtime (picture, dispatch)
│   │   ├── sycl/       # SYCL backend runtime (queue, USM, dmabuf)
│   │   ├── hip/        # HIP backend runtime
│   │   ├── metal/      # Metal backend runtime
│   │   ├── mcp/        # Embedded MCP server (-Denable_mcp)
│   │   ├── dnn/        # ONNX Runtime integration (Tiny-AI, docs/ai/)
│   │   ├── x86/, arm/  # CPU feature detection and dispatch helpers
│   │   └── ...
│   ├── include/        # public C API headers
│   ├── tools/          # vmaf CLI, vmaf_bench, vmaf_per_shot, vmaf_roi, vmaf_vpl
│   └── test/           # libvmaf C unit tests
│
├── cmd/                # Go binaries: vmafx-server, -controller, -node,
│                       #   -operator, -mcp, -tune, -ort-runner
├── pkg/, internal/     # Go packages shared by the binaries
├── proto/, api/, gen/  # gRPC / OpenAPI contracts and generated Go code
├── deploy/             # Helm chart (deploy/helm/vmafx) + Grafana dashboards
├── docker/             # Production and node Dockerfiles
│
├── ai/                 # Fork: Tiny-AI training harness (torch + lightning)
│   └── src/vmaf_train/ # typer CLI → ONNX artefacts under model/tiny/
│
├── model/              # Shipped VMAF models (vmaf_v0.6.1.json, .pkl)
│   └── tiny/           # Fork: ONNX tiny models (C1/C2/C3 from ai/)
│
├── compat/python-vmaf/ # Classic training harness (core/, script/, workspace/)
├── python/             # Re-export shim + golden-data tests (python/test/)
├── bindings/rust/      # Rust FFI crates (vmafx-sys, vmafx)
├── mcp-server/         # Fork: Python MCP server (vmaf-mcp)
├── tools/              # vmaf-tune, vmaf-roi-score, rc1-tester, external-bench,
│                       #   ensemble-training-kit
├── ffmpeg-patches/     # Patch series for the libvmaf ffmpeg filters
├── dev/, dev-llm/      # Dev-MCP container and local-LLM helpers
├── scripts/            # CI, docs and developer scripts
│
├── testdata/           # YUV fixtures + fork benchmark JSONs
│
├── docs/               # All documentation (this tree)
│   ├── architecture/   # <-- you are here
│   ├── ai/             # Tiny-AI: train / infer / bench / security
│   ├── api/            # C API reference
│   ├── backends/       # CUDA / SYCL / HIP / Metal / x86 / arm backend notes
│   ├── k8s/            # Kubernetes deployment
│   ├── mcp/            # MCP servers and tools
│   ├── metrics/        # Per-metric (VMAF, SSIM, MS-SSIM, CAMBI, ...)
│   ├── models/         # Model files, training overview
│   ├── observability/  # Metrics, tracing, dashboards
│   ├── security/       # Security notes
│   ├── server/         # Scoring service, controller, node, operator
│   ├── usage/          # CLI, Python, FFmpeg, Docker
│   ├── development/    # Releases, contributor workflow
│   ├── reference/      # Papers, presentations, FAQ
│   ├── hardware-reports/, research/, adr/
│   └── getting-started # Installation, first build
│
├── .claude/            # Claude Code agent config (skills, hooks, agents)
├── .workingdir/        # Ignored private state, cache, and local evidence
├── .corpus/            # Ignored datasets and reusable derived data
└── .github/workflows/  # CI / release / supply chain
```

!!! note
    `docs/backends/vulkan/` is a leftover of the Vulkan backend, which was
    removed in [ADR-0726](../adr/0726-drop-vulkan-backend.md). Treat it as
    historical.

## C4 model

The fork uses the [C4 model](https://c4model.com) for top-down architecture
views. Levels are scaffolded and evolve as internal boundaries stabilise:

- **[c4-context.md](c4-context.md)** — Level 1: system context (users,
  external systems).
- **[c4-container.md](c4-container.md)** — Level 2: containers (libvmaf,
  vmaf CLI, ai/, MCP servers, Go platform binaries, …).
- *Level 3 (component) added per-container as needed.*
- *Level 4 (code) generated on demand — not hand-maintained.*

## Related reading

- **[workspace.md](workspace.md)** — the Python harness scratch tree (and why it
  moved).
- **[phase4b-distributed-platform.md](phase4b-distributed-platform.md)** — the
  controller, node and operator platform.
- **[grpc-streaming.md](grpc-streaming.md)** — vmafx-server `ScoreStream`
  bidirectional RPC (ADR-0933, Phase 1).
- **[mcp-cgo-direct-migration.md](mcp-cgo-direct-migration.md)** — the Go MCP
  server's direct cgo scoring path.
- **[vmaf-picture-v2-migration.md](vmaf-picture-v2-migration.md)** — migration
  to the v2 picture type.
- **[torcul-capability-map.md](torcul-capability-map.md)**: the five Go
  encode packages mapped to torcul counterparts and VMAFx retirement conditions.
- **[../principles.md](../principles.md)** — coding standards (NASA/JPL, CERT
  C).
- **[../ai/overview.md](../ai/overview.md)** — Tiny-AI architecture (C1 / C2 /
  C3 / C4).
- **[../backends/](../backends/)** — CUDA / SYCL / HIP / Metal backend
  internals.
- **[../adr/README.md](../adr/README.md)** — Architectural Decision Records
  (ADRs).
