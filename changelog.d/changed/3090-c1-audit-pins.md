- **CI version pins have single owners again (three audit paths).**
  The root Dockerfile's optional SYCL layer installs its frozen 2025.3 oneAPI
  compiler/runtime from `DOCKERFILE_SYCL_ONEAPI_VERSION` in `build-config.env`
  instead of two inline package literals (#3090); `CLANG_MAJOR` now owns the
  LLVM major the Linux CI legs and `.github/apt` package lists repeat, with
  `scripts/ci/check-workflow-versions.py` refusing a disagreeing literal
  (#3102); and a test suite's `install.python` inherits the `PYTHON_CI_VERSION`
  series instead of repeating `3.14`, while an explicit value stays a
  per-suite override (#3103).
