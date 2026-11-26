<!-- markdownlint-disable MD013 MD060 -->
# VMAFx encode packages and torcul

torcul is the Vidiko video encode service, in its own repository
vidiko/torcul. The name comes from Latin *torcular*, a wine or oil press, and
is said TOR-kul. [ADR-3382](../adr/3382-encode-service-leaves-vmafx.md) moved
the encode service there while VMAFx keeps its scoring, `vmafx-tune auto` and
compare surfaces. This page maps five VMAFx Go packages to their planned Rust
counterparts and defines when the Go packages can retire. The table lists the
torcul issues that track the work. See torcul's design records
[ADR-0001](https://github.com/vidiko/torcul/blob/main/docs/adr/0001-vmafx-integration.md)
and [ADR-0002](https://github.com/vidiko/torcul/blob/main/docs/adr/0002-pipeline-architecture.md).

## What VMAFx owes torcul

Issue [#2320](https://github.com/vidiko/vmafx/issues/2320) tracks a Rust crate
over `libvmafx.so.1` generated from
[`core/api/vmafx.toml`](../../core/api/vmafx.toml). Until that crate ships,
torcul may call the `vmaf` CLI instead
([ADR-3382](../adr/3382-encode-service-leaves-vmafx.md) decision 3).

## Capability map

| VMAFx package | What it does | VMAFx importers | torcul counterpart | Difference to plan for |
| --- | --- | --- | --- | --- |
| `pkg/hwdevice` | Intel QSV device chain for vmafx-tune encodes (VA-API + derived QSV); see `pkg/hwdevice/hwdevice.go` and [ADR-0601](../adr/0601-vmaftune-qsv-amf-hw-init-and-probe-fix.md) | `pkg/corpus`, `pkg/encoder`, `pkg/ffencode` | torcul issue 5 (hardware encoder probe), torcul issue 11 (Arc hardware encode/decode measurement) | VMAFx builds device arguments for the `ffmpeg` binary; torcul runs libav* in process and starts no `ffmpeg`/`ffprobe` at run time (torcul ADR-0002). |
| `pkg/ffencode` | Go port of `tools/vmaf-tune/src/vmaftune/encode.py`: shared FFmpeg encode driver for tuning subcommands | `cmd/vmafx-tune/cmd`, `pkg/corpus`, `pkg/corpusrow`, `pkg/encodeprofile`, `pkg/tune/executor` | torcul encode crate with one `Encoder` trait (torcul issue 13), encoder settings crate (torcul ADR-0002) | VMAFx builds an `ffmpeg` command line and runs the binary; torcul's encoder settings crate emits per-backend settings, not command lines. |
| `pkg/codecadapter` | Per-codec quality knob, range, defaults, encoder name, preset table; see `pkg/codecadapter/codecadapter.go` and [ADR-0237](../adr/0237-quality-aware-encode-automation.md), [ADR-0294](../adr/0294-vmaf-tune-codec-adapter-svtav1.md), [ADR-0326](../adr/0326-vmaf-tune-phase-b-bisect.md) | `cmd/vmafx-tune/cmd`, `pkg/corpus`, `pkg/encodeprofile`, `pkg/ffencode`, `pkg/predictor`, `pkg/tune/auto`, `pkg/tune/codec` (alias slated for deletion in [#1249](https://github.com/vidiko/vmafx/issues/1249)) | torcul issue 4 (codec tiering), encoder settings crate (torcul ADR-0002) | VMAFx adapters yield FFmpeg arguments; torcul's settings crate yields libavcodec options or SVT-AV1 fields. |
| `pkg/hdr` | HDR detection and codec-specific HDR encode flags; see `pkg/hdr/hdr.go` and [ADR-0300](../adr/0300-vmaf-tune-hdr-aware.md) | `pkg/corpus`, `pkg/ffencode` (test), `pkg/tune/auto` | torcul issue 8 | torcul issue 8 adds HDR10+ and Dolby Vision profile handling, which `pkg/hdr/hdr.go` does not have. |
| `pkg/tune/auto` | Phase F adaptive recipe-aware tuning tree; see `pkg/tune/auto/auto.go` and [ADR-0397](../adr/0397-vmaf-tune-phase-f-auto.md) | `cmd/vmafx-tune/cmd`, `pkg/tune/executor` | torcul issue 3 | [ADR-3382](../adr/3382-encode-service-leaves-vmafx.md) decision 2 keeps `vmafx-tune auto` in VMAFx. Package retirement also requires the command's own tests to pass without this package. |

## Retirement conditions

These apply to every package in the table:

1. Every counterpart listed in the package's row is satisfied: each listed
   torcul issue is **closed**, each listed torcul crate has shipped in a torcul
   release, and the tests of the listed counterparts together cover the cases
   exercised by the VMAFx package's own tests (`pkg/<package>/*_test.go`).
2. Each listed VMAFx importer drops its import of the package. Every importer
   that stays must preserve its behaviour. Its existing test cases must still
   pass. Test edits may replace references to the removed package (imports,
   fixture construction, and calls at call or assertion sites) only where the
   test inputs and expected results stay identical.
3. The VMAFx package is deleted in one pull request with a
   `changelog.d/removed/` fragment, a `!` conventional-commit subject, and a
   `Migration:` footer, because external Go code may import these paths
   (HISS-14 append-only ABI).

**Open.** Issue [#3640](https://github.com/vidiko/vmafx/issues/3640) tracks how
VMAFx Go code would call a Rust crate; the first affected Go caller is
[`cmd/vmafx-tune/cmd/auto.go`](../../cmd/vmafx-tune/cmd/auto.go).

Per-package extras:

- **`pkg/tune/auto`.** [ADR-3382](../adr/3382-encode-service-leaves-vmafx.md)
  decision 2 keeps the `vmafx-tune auto` command in VMAFx. Retiring the package
  requires both closing torcul issue 3 and showing that the command works
  without the package by passing its own tests in
  [`cmd/vmafx-tune/cmd/auto_test.go`](../../cmd/vmafx-tune/cmd/auto_test.go).
- **`pkg/ffencode`:** `pkg/encoder` (Stage-1 bisect) imports `pkg/hwdevice`
  only, not `pkg/ffencode`; it is out of scope for this package's retirement
  list.

## Re-check this page

The importer column was taken at commit `800122efd8`. Run this check from the
module root. `go list` reports imports under the current Go build settings.
The `test` and `xtest` labels identify in-package and external test imports.
The check lists matching imports in a target's own in-package and external
tests, which retire with the package and therefore do not block retirement.

```sh
(
  for target in pkg/hwdevice pkg/ffencode pkg/codecadapter pkg/hdr pkg/tune/auto
  do
    printf '%s\n' "$target"
    go list -f '
{{- $target := printf "%s/%s" .Module.Path "'"$target"'" -}}
{{- $code := false -}}{{- $test := false -}}{{- $xtest := false -}}
{{- range .Imports }}{{ if eq . $target }}{{ $code = true }}{{ end }}{{ end -}}
{{- range .TestImports }}{{ if eq . $target }}{{ $test = true }}{{ end }}{{ end -}}
{{- range .XTestImports }}{{ if eq . $target }}{{ $xtest = true }}{{ end }}{{ end -}}
{{- if or $code $test $xtest -}}
  {{- printf "  %s" .ImportPath }}{{ if $code }} code{{ end }}
  {{- if $test }} test{{ end }}{{ if $xtest }} xtest{{ end -}}
{{- end -}}
' ./... || exit
  done
)
```
