- **The repeated MSVC C11-atomics flag has one meson-side owner.**
  `core/meson.build` adds `/experimental:c11atomics` as an MSVC project
  argument (cl.exe only) instead of the four workflow steps and the Windows
  tester bundle script injecting it through `CFLAGS`/`CXXFLAGS`; the icx-cl
  SYCL builds keep going without it, as the SYCL leg already does (#3072).
- **One composite action replaces six repeated release-PR exemption steps.**
  `.github/actions/release-pr-exempt` carries the env block and script call
  the six gates in `rule-enforcement.yml` used to repeat; the steps keep
  their id and step-level `if:` (#3078).
