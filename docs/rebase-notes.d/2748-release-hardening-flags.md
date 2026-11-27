## Release hardening flags live in one native file (2026-11-28)

Fork-only (upstream Netflix/vmaf has no `core/native/`, no release image
recipes and no bundle script). The documented release hardening flags
(`-D_FORTIFY_SOURCE=3`, `-fstack-protector-strong`, `-fPIE`/`-pie`,
`-Wl,-z,relro,-z,now`) are defined once in
`core/native/release-hardening.ini` and applied only by release paths,
which pass `--native-file core/native/release-hardening.ini` to
`meson setup`:

- `scripts/release/build-native-release-artifacts.sh` (bundle),
- `docker/Dockerfile.production`, `.node`, `.controller`, `.production-gpu`
  (two builders), `.tester` (four legs),
- `scripts/ci/build-oneapi-libvmaf.sh` (icx),
- `scripts/ci/build-wheel-libvmafx.py` (Linux only).

- **On sync**: when a release recipe moves its `meson setup`, keep the
  `--native-file` argument — `scripts/release/check_release_hardening.py`
  (the supply-chain step beside the glibc-floor check) fails a bundle whose
  artifacts lack PIE/full-RELRO/stack-protector/fortify, so a lost
  `--native-file` fails the release at verify time, not at runtime. Do not
  "simplify" the explicit `-fPIE`/`-pie` into `b_pie`: Meson's nvcc wrapper
  raises `EnvironmentException` for PIE, which breaks the CUDA release legs.
- The published images stage `build/tools/vmaf` with
  `patchelf --remove-rpath` (the build-tree RUNPATH `$ORIGIN/../src`
  resolves nothing under `/usr/local/bin`; resolution goes through
  `LD_LIBRARY_PATH=/usr/local/lib` or `ldconfig`, as before). The tester
  image keeps its build-tree layout on purpose
  (`/opt/vmafx/build/src` is in its `LD_LIBRARY_PATH`).
- `patchelf` was added to the builder stages' apt lists; do not remove it.
- Development and CI test builds do not pass the native file and stay
  byte-identical to before; keep it that way so golden/twin comparisons
  measure the code, not the flags.
