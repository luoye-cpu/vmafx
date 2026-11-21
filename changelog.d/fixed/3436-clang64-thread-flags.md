- CI: keep GCC's `-mthreads` in the UCRT64 and MINGW64 environments only.
  CLANG64 uses the existing Meson threads dependency instead, so Clang's
  compile-only `std::expected` probe no longer rejects an unused link flag.
  All three MSYS2 environments retain compiler and linker warnings as errors.
