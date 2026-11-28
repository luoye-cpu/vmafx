# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
# shellcheck shell=bash

probe_host_chars='[A-Za-z0-9._-]'

# Probe v1 evidence rows. The checker fills the same shapes with ERE fields.
probe_header_row() {
  printf 'toolchain-probe v1 host=%s os=%s date=%s\n' "$1" "$2" "$3"
}

probe_found_fields() {
  printf 'toolchain-probe: %s found %s%s\n' "$1" "$2" "$3"
}

probe_found_row() {
  probe_found_fields "$1" "$(probe_encode_field "$2")" " $3"
}

probe_absent_field() {
  printf 'toolchain-probe: %s absent searched=%s\n' "$1" "$2"
}

probe_encode_field() {
  local field="${1//%/%25}"
  field="${field// /%20}"
  field="${field//$'\t'/%09}"
  field="${field//$'\n'/%0A}"
  field="${field//$'\v'/%0B}"
  field="${field//$'\f'/%0C}"
  field="${field//$'\r'/%0D}"
  printf '%s' "${field}"
}

probe_absent_row() {
  probe_absent_field "$1" "$(probe_encode_field "$2")"
}

probe_metal_os_row() {
  printf 'toolchain-probe: metal absent searched=none os=%s\n' "$1"
}

# Called only by the checker. MinGW detail rows are informational, never
# evidence of presence or absence on their own.
# shellcheck disable=SC2034 # The checker reads the result patterns.
probe_result_patterns() {
  local names="$1" token='[^[:space:]]+' pad='[[:space:]]*'
  local stamp='[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z'
  local windows='(MINGW(32|64)|MSYS|CYGWIN)_NT-[0-9]+([.-][0-9]+)*'
  local non_darwin="(Linux|FreeBSD|NetBSD|OpenBSD|DragonFly|SunOS|AIX|${windows})"
  local os="(Darwin|${non_darwin})" field='([^[:space:]%]|%(09|0A|0B|0C|0D|20|25))+'
  probe_header="^${pad}$(probe_header_row "(${probe_host_chars}+)" "${os}" "${stamp}")${pad}$"
  probe_row_start="^${pad}toolchain-probe: (${names})[[:space:]]"
  probe_found="^${pad}$(probe_found_fields "(${names})" "${field}([[:space:]]+.*)?" '')$"
  probe_absent="^${pad}$(probe_absent_field "(${names})" "${field}")${pad}$"
  probe_metal_os="^${pad}$(probe_metal_os_row "${os}")${pad}$"
  local mingw_name='x86_64-w64-mingw32-gcc'
  local mingw_rt_unres='\(gcc -print-file-name answered no absolute path; refusing instead of staging green\)'
  local mingw_opt_unres='unresolved \(evidence only; not every flavour ships it\)'
  local mingw_pair_ok="pairing ok thread-model (win32, ${token} imports no libwinpthread|posix, ${token} imports libwinpthread)"
  local mingw_pair_bad="pairing-mismatch thread-model (win32 but ${token} imports libwinpthread \(B24: a posix runtime next to a win32-model driver deadlocks on the target\)|posix but ${token} imports no libwinpthread \(B24 inverse: a win32 runtime next to a posix-model driver\))"
  local mingw_pair_unv="pairing-unverified (${token}: objdump -p exited [0-9]+|thread-model unknown \(${token} imports (libwinpthread|none)\); refusing instead of guessing a flavour|no objdump found \(target-prefixed or plain\); refusing to pass an unasserted stage)"
  probe_mingw_detail="^[[:space:]]*toolchain-probe: ${mingw_name} (dumpversion ${token}|thread-model ${token}|runtime (libstdc\+\+-6\.dll|libgcc_s_seh-1\.dll|libwinpthread-1\.dll)=${field}|runtime libwinpthread-1\.dll ${mingw_opt_unres}|runtime-unresolved ${token} ${mingw_rt_unres}|${mingw_pair_ok}|${mingw_pair_bad}|${mingw_pair_unv})[[:space:]]*$"
}
