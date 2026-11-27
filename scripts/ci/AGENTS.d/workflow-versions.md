---
paths:
  - scripts/ci/check-workflow-versions.py
  - scripts/ci/tests/test_level_zero_single_source.py
  - scripts/ci/tests/test_formatter_pins_single_source.py
  - scripts/ci/tests/test_mypy_python_version_single_source.py
  - scripts/ci/tests/test_rust_toolchain_single_source.py
  - scripts/ci/tests/test_clang_major_single_source.py
  - scripts/ci/tests/test_dockerfile_sycl_pin_single_source.py
  - scripts/ci/tests/test_suite_registry_python_default.py
  - rust-toolchain.toml
invariant: Level Zero, ruff / black, mypy `python_version`, Rust toolchain, clang major, Dockerfile SYCL pin, suite Python default: one owner each plus checked mirrors.
area: release
---
<!-- markdownlint-disable MD013 MD060 -->
# Version pins checked by `check-workflow-versions.py`

## Level Zero version consumption (ADR-1231)

`dev/Containerfile` copies and sources `build-config.env` in its SDK download
RUN. Loader version = runtime shell input, so needs no Docker ARG
mirror. Preserve source step, both URL components and command-execution
fixture in `tests/test_level_zero_single_source.py`; base-image test hook
runs both single-source suites. `check-workflow-versions.py` verifies this
container consumer alongside Windows workflow mirror. Renovate tracks
Level Zero only in `build-config.env`; ROCm uses central image manager.

`check-workflow-versions.py` also owns formatter pins: `Makefile`'s
`RUFF_VERSION` / `BLACK_VERSION` must equal ruff-pre-commit and black revs
in `.pre-commit-config.yaml`, and recipe may not spell `ruff==<n>` or
`black==<n>` as literal. Renovate moves both files through
`pre-commit hooks` group (two regex managers on `Makefile`); keep group
name identical on both rules or bumps split into two pull requests and
first one fails this gate. Fixture: `tests/test_formatter_pins_single_source.py`.
same checker keeps `VIRTUAL_ENV_PATH` absolute and rejects recipe-local
`$(VENV)/bin` prefixes: Meson persists Ninja path and invokes it from
build directory while generating `compile_commands.json`, so relative path
breaks canonical `make lint` gate. Cython likewise invokes absolute
`$(VENV_PYTHON)` after changing into `python/`.

`check_mypy_python_version` owns third pairing (ADR-1282): `pyproject.toml`'s
`[tool.mypy] python_version` must equal `major.minor` floor of
`[project] requires-python` and `major.minor` of `PYTHON_CI_VERSION`.
Deleting key is finding too — mypy would then model whichever interpreter
caller runs. comment was only thing holding these together before, and
pin stayed at `3.10` against `>=3.14` floor long enough to abort every
`ai/src/` run (mypy will not parse numpy's PEP 695 `type` statement below 3.12).
Raise all three in one commit. Fixture:
`tests/test_mypy_python_version_single_source.py`; add its path and any new
trigger file to `test-base-image-single-source` hook's `files:` regex, which
is what decides when `test_*single_source.py` discovery runs.

Renovate side of `PYTHON_CI_VERSION` (ADR-3312): one `custom.regex` manager
(`depNameTemplate: python`, `packageNameTemplate: actions/python-versions`)
reads `build-config.env` + every workflow `python-version:`; `github-actions`
manager disabled for dep `python` / depType `uses-with`; group rule "CI Python
interpreter", `allowedVersions: "<3.15.0"` until 1.0.0. Manager matchStrings
read exactly what `PYTHON_RE` here reads. New pin form (matrix list etc.) ->
widen both + `PythonCiVersionGroup` fixture in
`tests/test_renovate_file_patterns.py`, same commit.

Rust toolchain (ADR-3318): `RUST_VERSION` in `build-config.env` owns it.
Mirrors: `rust-toolchain.toml` channel, `rust-version` of both workspaces
(crates inherit), `toolchain:` of `rust-ci.yml`, container rustup
`--default-toolchain "${RUST_VERSION}"` (no literal). `check_rust_pins()`
compares all; fixture `tests/test_rust_toolchain_single_source.py` (also holds
`build_staticlib.py` running cargo in manifest dir: rustup reads the toolchain
file from cwd). Renovate: one `custom.regex` manager, group "Rust toolchain",
no automerge, built-in `rust-toolchain` manager disabled; `RustToolchainGroup`
in `tests/test_renovate_file_patterns.py`. New pin site -> both, same commit.

Clang major (#3102): `CLANG_MAJOR` in `build-config.env` owns the LLVM major
the Linux CI legs build with. Mirrors are literals on purpose -- the matrix
`CC:` entries, the sanitizer env and the `install-llvm.sh` calls keep their
explicit compiler roles because YAML cannot source the config at parse time,
the same mirror shape a `python-version:` pin uses -- and the two
`.github/apt/*.txt` package lists repeat it in package names and comments.
`check_clang_major()` refuses any workflow or apt-list literal naming another
major, and refuses no literal at all (the pin would be orphaned). Fixture:
`tests/test_clang_major_single_source.py`.

Root Dockerfile SYCL pin (#3090): `DOCKERFILE_SYCL_ONEAPI_VERSION` in
`build-config.env` owns the optional `ENABLE_SYCL=true` layer's frozen 2025.3
oneAPI release (it pairs the compiler with the legacy
`intel-oneapi-runtime-libs` metapackage and nothing in CI builds the image,
so it deliberately does not follow ONEAPI_VERSION). The layer must COPY and
source `build-config.env` inside its RUN (the dev/Containerfile Level Zero
pattern, no ARG mirror) and name no oneAPI package literal.
`check_dockerfile_sycl_pin()` compares; fixture
`tests/test_dockerfile_sycl_pin_single_source.py`. `clang-tidy-sycl.sh`'s
`/opt/intel/oneapi-2025.3/...` search candidates stay: they are a legacy
host-install fallback, not a pin.

Suite Python default (#3103): a suite install without `install.python`
inherits the `PYTHON_CI_VERSION` series from `build-config.env`
(`suite_registry.default_python`); an explicit value is a per-suite override.
`check_suite_registry_python()` refuses an install that repeats the default
and requires at least one suite to inherit it, so the config stays the only
owner. Fixture: `tests/test_suite_registry_python_default.py`.
