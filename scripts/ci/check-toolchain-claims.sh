#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# scripts/ci/check-toolchain-claims.sh — gate for PR-body toolchain absence claims.
#
# Enforces that any claim asserting a toolchain is missing or that a GPU backend
# was not compiled is backed by evidence from `scripts/dev/toolchain-probe.sh`.
#
# A claim requires one block covering every tool. A host claim also requires
# a covering block with no claimed tool found. Pasted output is unauthenticated:
# the checker detects missing or contradictory evidence, not forged blocks.
#
# Checks prose in fences and skips the probe's header and result lines.
#
# Reads PR body from $PR_BODY env var or stdin via scripts/ci/pr-body-input.sh.
#
# Exit codes:
#   0 — pass (claims justified by probe, or no claims made).
#   1 — claim violation (missing probe evidence or contradicted by probe).
#   2 — usage error (no body supplied).

set -euo pipefail

_check_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ci/pr-body-input.sh
. "${_check_dir}/pr-body-input.sh"
# shellcheck source=scripts/dev/toolchain-probe-format.sh
. "${_check_dir}/../dev/toolchain-probe-format.sh"

# Preserve trailing blank lines for the body limit. Command substitution strips
# trailing newlines unless a final non-newline byte protects them.
read_body_stream() {
  body_text="$(pr_body_read_stdin && printf .)"
  body_text="${body_text%.}"
}

# Bash 3.2 has no {variable} fd allocation. Use a scoped duplicate for the
# stdin read on that shell. Redirection on the function restores fd 9 on return.
read_legacy_stdin() {
  PR_BODY_STDIN_FD=9
  if [ -t 9 ]; then
    PR_BODY_STDIN_KIND="tty"
  elif [ -e /dev/fd/9 ] && [ ! -f /dev/fd/9 ] && [ ! -p /dev/fd/9 ] && [ ! -S /dev/fd/9 ]; then
    PR_BODY_STDIN_KIND=unreadable
  else
    PR_BODY_STDIN_KIND=stream
    read_body_stream
  fi
}

if [ -n "${PR_BODY+x}" ]; then
  body_text="${PR_BODY}"
  body_src="env"
else
  body_src="stdin"
  if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] == 0))); then
    if ! { : 9<&0; } 2>/dev/null; then
      PR_BODY_STDIN_KIND=closed
    else
      read_legacy_stdin 9<&0
    fi
  else
    pr_body_classify_stdin
    if [ "${PR_BODY_STDIN_KIND}" = "stream" ]; then
      read_body_stream
      pr_body_close_stdin
    fi
  fi
  if [ "${PR_BODY_STDIN_KIND}" != "stream" ]; then
    echo "check-toolchain-claims: no PR body supplied — $(pr_body_stdin_reason)" >&2
    echo "  Set PR_BODY env var, OR pipe body on stdin." >&2
    exit 2
  fi
fi

# Family; probe name; explicit compiler aliases; backend aliases.
# Single source for claim vocabulary, accepted probe names and evidence lookup.
toolchain_map=(
  "sycl;icpx;;oneapi|sycl"
  "sycl;icx;;"
  "cuda;nvcc;;cuda[[:space:]]+toolkit|cuda"
  "hip;hipcc;hip[[:space:]]+cc;rocm|hip"
  "vulkan;glslc;;vulkan"
  "vulkan;glslangValidator;;"
  "metal;metal;;"
  "mingw;x86_64-w64-mingw32-gcc;;"
)

wb_l='(^|[^a-zA-Z0-9_-])'
wb_r='([^a-zA-Z0-9_-]|$)'
wb_tok_l='(^|[^a-zA-Z0-9_`-])'
wb_tok_r='([^a-zA-Z0-9_`-]|$)'
claim_words=''
probe_names=''
for spec in "${toolchain_map[@]}"; do
  IFS=';' read -r _family name compiler_alias backend_alias <<<"${spec}"
  probe_names+="${probe_names:+|}${name}"
  claim_words+="${claim_words:+|}${name}${compiler_alias:+|${compiler_alias}}${backend_alias:+|${backend_alias}}"
done

space='[[:space:]]+'
probe_result_patterns "${probe_names}"
tick='`'
fence_tick="^[[:space:]]*(${tick}{3,})[^${tick}]*\$"
fence_tilde='^[[:space:]]*(~{3,})'
blank='^[[:space:]]*$'

# Only fence markers, valid headers, and probe rows inside a strict header
# block (`after_header=1`) are exempt from prose claim checking.
declare -a lines=() exempt_lines=()
# Each indexed entry is a space-delimited set of canonical probe names.
declare -a probed_tools=() found_tools=() block_host=() block_os=() header_line=()
block_count=0
fence_start=0
fence_close=''
after_header=0

# Opening and closing fences reset the evidence scope. Under CommonMark, a
# backtick fence info string cannot contain a backtick (same-line spans pass).
is_fence_marker() {
  local line="$1" idx="$2" marker
  if [ "${fence_start}" -eq 0 ]; then
    if [[ "${line}" =~ ${fence_tick} || "${line}" =~ ${fence_tilde} ]]; then
      marker="${BASH_REMATCH[1]}"
      fence_start="${idx}"
      fence_close="^[[:space:]]*${marker:0:1}{${#marker},}[[:space:]]*$"
    else
      return 1
    fi
  else
    [[ "${line}" =~ ${fence_close} ]] || return 1
    fence_start=0
  fi
  after_header=0
}

# Classify strict rows as found, absent, or informational. A row outside
# the producer grammar remains prose, even when it starts with a tool name.
classify_probe_row() {
  local row="$1" tool="$2" os="$3"
  row_kind=prose
  case "${tool}:${os}" in
    metal:Darwin) ;;
    metal:*)
      if [[ "${row}" =~ ${probe_metal_os} && "${BASH_REMATCH[1]}" == "${os}" ]]; then
        row_kind=absent
      elif [[ "${row}" =~ ${probe_found} || "${row}" =~ ${probe_absent} || "${row}" =~ ${probe_metal_os} ]]; then
        row_kind=detail
      fi
      return 0
      ;;
  esac
  if [[ "${row}" =~ ${probe_found} ]]; then
    row_kind=found
  elif [[ "${row}" =~ ${probe_absent} ]]; then
    row_kind=absent
  elif [[ "${row}" =~ ${probe_mingw_detail} || "${row}" =~ ${probe_metal_os} ]]; then
    row_kind=detail
  fi
}

record_probe_row() {
  local line="$1" idx="$2" name='' row_kind
  if [[ "${line}" =~ ${probe_row_start} ]]; then name="${BASH_REMATCH[1]}"; fi
  classify_probe_row "${line}" "${name}" "${block_os[block_count]}"
  case "${row_kind}" in
    prose) return 0 ;;
    found) found_tools[block_count]+=" ${name} " ;;
  esac
  if [[ "${row_kind}" != detail ]]; then probed_tools[block_count]+=" ${name} "; fi
  exempt_lines[idx]=1
}

read_probe_line() {
  local line="$1" idx="$2"
  if is_fence_marker "${line}" "${idx}"; then
    exempt_lines[idx]=1
  elif [[ "${line}" =~ ${blank} ]]; then
    if [ "${fence_start}" -eq 0 ]; then
      after_header=0
    fi
  elif [[ "${line}" =~ ${probe_header} ]]; then
    block_count=$((block_count + 1))
    block_host[block_count]="${BASH_REMATCH[1]}"
    block_os[block_count]="${BASH_REMATCH[2]}"
    header_line[block_count]="${idx}"
    probed_tools[block_count]=''
    found_tools[block_count]=''
    after_header=1
    exempt_lines[idx]=1
  elif [[ "${line}" =~ ^[[:space:]]*toolchain-probe[[:space:]]+v[0-9]+([[:space:]]|$) ]]; then
    after_header=0
  elif [ "${after_header}" -eq 1 ]; then
    record_probe_row "${line}" "${idx}"
  fi
}

# Read one extra record to distinguish an exact limit from truncated evidence.
readonly max_body_lines=4096 max_clauses_per_line=64
idx=0
body_has_text=0
while ((idx <= max_body_lines)) && IFS= read -r line; do
  idx=$((idx + 1))
  if ((idx > max_body_lines)); then
    echo "check-toolchain-claims: PR body exceeds ${max_body_lines} lines" >&2
    exit 1
  fi
  lines[idx]="${line}"
  if [[ ! "${line}" =~ ${blank} ]]; then body_has_text=1; fi
  read_probe_line "${line}" "${idx}"
done <<<"${body_text%$'\n'}"

# An open stdin stream carrying no bytes fails closed (producer sent nothing);
# an explicitly set blank $PR_BODY makes no claims and exits 0.
if ((body_has_text == 0)); then
  if [ "${body_src}" = "stdin" ]; then
    echo "check-toolchain-claims: nothing arrived on stdin." >&2
    echo "  The pipe was open and carried no bytes." >&2
    echo "  Set PR_BODY env var, OR pipe body on stdin." >&2
    exit 2
  fi
  exit 0
fi

# Allow-list change-scope `no <tool>` phrases in two shapes:
#   (a) change word follows after zero to three change objects (`no SYCL changes`, `no nvcc flags changed`);
#   (b) change verb directly precedes `no <tool>` (`Touches no CUDA`, `changes no HIP`).
change_objects='build|flag|flags|option|options|code|source|sources|file|files|path|paths|kernel|kernels|backend|test|tests|config|setting|settings|dependency|dependencies'
p_scope_object="\`*(${change_objects})\`*"
p_no_scope_after="no${space}\`*(${claim_words})\`*${space}(${p_scope_object}${space}){0,3}(change|changes|changed|edit|edits|edited|touched|modified)${wb_r}"
p_no_scope_before="(touch|touches|touched|change|changes|changed|edit|edits|edited|modify|modifies|modified)${space}no${space}\`*(${claim_words})\`*${wb_tok_r}"
p_no_scope="${wb_l}(${p_no_scope_after}|${p_no_scope_before})"
tool_word="\`*(${claim_words})\`*"
join_tools='([[:space:]]*,[[:space:]]*((and|or)[[:space:]]+)?|[[:space:]]+(and|or)[[:space:]]+)'
compiler_note="[(][[:space:]]*${tool_word}[[:space:]]*[)]"
qualified_tool="${tool_word}(${space}(compiler${space}${tool_word}|${compiler_note}))?"
tool_group="${qualified_tool}(${join_tools}${qualified_tool})*"
no_tool="no${space}${qualified_tool}"
lacks_tool="(lacks|lacking)${space}((a|an|the)${space})?${qualified_tool}"
status_subject="(${qualified_tool}${space}(is|was)|${tool_group}(${space}(are|were))?)"
tool_status="${status_subject}${space}(not${space}(installed|found|available)|missing|unavailable|absent)"
without_tool="without${space}${qualified_tool}"
compile="not${space}compiled|(was|were)${space}not${space}built|did${space}not${space}(compile|build)|compilation(${space}(was|were))?${space}skipped"
predicate="(${wb_tok_l}\`*)(${no_tool}|${lacks_tool}|${tool_status}|${without_tool}|${compile})${wb_tok_r}"
clause_break=';|[.!?][[:space:]]+|,[[:space:]]*(but|while|whereas|although)([[:space:]]+|$)'
tool_list="^((${join_tools}${qualified_tool})+)${wb_tok_r}"
compile_object="^(${space}${qualified_tool})${wb_tok_r}"
compile_noun="(${space}(backend|compiler|toolchain))?"
compile_subject="${wb_tok_l}(${tool_group})${compile_noun}(${space}(were|are))?[[:space:]]*$"
singular_compile_subject="${wb_tok_l}(${qualified_tool})${compile_noun}${space}(was|is)[[:space:]]*$"
p_host="${wb_l}(on${space}(this|the)${space}host|(this|the)${space}host|(this|my)${space}machine|here|locally)${wb_r}"
nonhost="ci|runners?|images?|containers?|golden(${space}|-)builds?"
p_nonhost="${wb_l}(${nonhost})${wb_r}"
nonhost_subject="${wb_l}(${nonhost})(${space}(has|have|is|are|was|were))?[[:space:]]*$"
# Inside a single subject, retain the proximity rule for informal "CI notes"
# introductions. A directly named runner or host takes priority.
explicit_host_subject="${wb_l}((this|the)${space}host|(this|my)${space}machine)(${space}(has|have|is|are|was|were))?[[:space:]]*$"
subject_local="${wb_l}(locally|here)([^[:alnum:]_-]+[[:alnum:]_-]+){0,3}[^[:alnum:]_-]*$"
qualifier_start="^[[:space:]]+((found|installed|available)[[:space:]]+)?(on|in|inside|under|at|for|from|host|here|locally)${wb_r}"
next_subject='[[:space:]]+(and|or|but|while|whereas|although|because|since|as|yet)[[:space:]]+|[:,]'

# Parse names from the prose before consulting any evidence. Keep the
# delimiter after each match available to the next qualifier.
host_token="(${tick}${probe_host_chars}+${tick}|${probe_host_chars}+)"
host_prep="(on|at|for)"
host_verb="(has|have|lacks|is)"
unnamed_host="((this|the)${space}host|(this|my)${space}machine|here|locally)"
host_intro="(host|${host_prep}(${space}host)?)${space}${host_token}"
host_phrase="((${host_prep}${space})?${unnamed_host}|${host_intro}|${host_token}${space}${host_verb})"
host_match="${wb_tok_l}${host_phrase}${wb_tok_r}"
host_location="(${host_intro}|(${host_prep}${space})?${unnamed_host})"
joined_locations="^${space}${host_location}(${space}(and|or)${space}${host_location})+"

parse_claim_hosts() {
  local tail="$1" phrase consumed token name steps
  named_hosts=()
  for ((steps = ${#tail}; steps > 0; steps--)); do
    [[ "${tail}" =~ ${host_match} ]] || break
    phrase="${BASH_REMATCH[2]}"
    consumed="${BASH_REMATCH[1]}${phrase}"
    tail="${tail#*"${consumed}"}"
    if [[ "${phrase}" =~ ^(${host_prep}${space})?(${unnamed_host}|host${space}${host_verb})$ ]]; then continue; fi
    if [[ "${phrase}" =~ ^${host_token}${space}${host_verb}$ ]]; then
      token="${BASH_REMATCH[1]}"
      [[ "${token}" =~ ^${tool_word}$ ]] && continue
    else
      token="${phrase##*[[:space:]]}"
    fi
    # A period outside backticks at the end of a sentence is punctuation.
    token="${token%.}"
    name="${token#"${tick}"}"
    name="${name%"${tick}"}"
    case "${name}" in '' | host | here | locally) continue ;; esac
    if [[ "${phrase}" =~ ^(${host_prep}${space})?host${space} || "${token}" == "${tick}"* || "${name}" == *[0-9.-]* ]]; then
      named_hosts+=("${name}")
    fi
  done
}

claim_tools() {
  local segment="$1" spec family name compiler_alias backend_alias families=' '
  tools=()
  for spec in "${toolchain_map[@]}"; do
    IFS=';' read -r family name compiler_alias _ <<<"${spec}"
    if [[ "${segment}" =~ ${wb_tok_l}\`*(${name}${compiler_alias:+|${compiler_alias}})\`*${wb_tok_r} ]]; then
      families+="${family} "
      tools+=("${name}")
    fi
  done
  for spec in "${toolchain_map[@]}"; do
    IFS=';' read -r family name _ backend_alias <<<"${spec}"
    if [[ -n "${backend_alias}" && "${families}" != *" ${family} "* &&
      "${segment}" =~ ${wb_tok_l}\`*(${backend_alias})\`*${wb_tok_r} ]]; then
      tools+=("${name}")
    fi
  done
}

emit_missing_probe() {
  local idx="$1" line="$2"
  echo "::error file=PR_BODY,line=${idx},title=Missing toolchain probe::no 'toolchain-probe v1' block" >&2
  echo "check-toolchain-claims: line ${idx}: missing-toolchain claim without 'toolchain-probe v1' block in PR body:" >&2
  echo "  ${line}" >&2
  echo "  Hint: run scripts/dev/toolchain-probe.sh on the build host and paste its fenced output into the PR description." >&2
}

emit_claim_errors() {
  local idx="$1" line="$2" missing="$3" contradicted="$4" block="$5" rc=0
  local location="host=${block_host[block]}, header line ${header_line[block]}"
  if [ -n "${missing}" ]; then
    echo "::error file=PR_BODY,line=${idx},title=Missing toolchain probe results::missing probe results for claimed toolchains: ${missing} (${location})" >&2
    echo "check-toolchain-claims: line ${idx}: missing probe results for claimed toolchains: ${missing} (${location})" >&2
    echo "  ${line}" >&2
    echo "  Hint: paste complete output from scripts/dev/toolchain-probe.sh into the PR description covering every claimed toolchain." >&2
    rc=1
  fi
  if [ -n "${contradicted}" ]; then
    echo "::error file=PR_BODY,line=${idx},title=Contradicted toolchain claim::line ${idx} claims '${contradicted}' is missing/not compiled, but probe reports it found on host: ${location}" >&2
    echo "check-toolchain-claims: line ${idx}: PR body claims '${contradicted}' is missing/not compiled, but probe reports it found on host: ${location}" >&2
    echo "  ${line}" >&2
    rc=1
  fi
  return "${rc}"
}

# An explicitly named host cannot discard a contradictory observation. For
# an unnamed host, keep the first closest block unless a complete one agrees.
check_evidence() {
  local scope="$1" bound_host="$2" block i tool missing contradicted count agreed=0
  local best_count=$((${#toolchain_map[@]} + 1)) best_block=0 best_missing='' best_contradicted=''
  for ((block = 1; block <= block_count; block++)); do
    if [[ -n "${bound_host}" && "${block_host[block]}" != "${bound_host}" ]]; then continue; fi
    missing='' contradicted='' count=0
    for ((i = 0; i < ${#tools[@]}; i++)); do
      tool="${tools[i]}"
      if [[ "${probed_tools[block]}" != *" ${tool} "* ]]; then
        missing+="${missing:+, }${tool}"
        count=$((count + 1))
      elif [[ "${scope}" == host && "${found_tools[block]}" == *" ${tool} "* ]]; then
        contradicted+="${contradicted:+, }${tool}"
      fi
    done
    if [[ -n "${bound_host}" && -n "${contradicted}" ]]; then
      emit_claim_errors "${idx}" "${line}" "${missing}" "${contradicted}" "${block}"
      return 1
    fi
    if [[ -z "${missing}${contradicted}" ]]; then
      if [[ -z "${bound_host}" ]]; then return 0; fi
      agreed=1
    fi
    if ((count < best_count)); then
      best_count=${count} best_block=${block} best_missing="${missing}" best_contradicted="${contradicted}"
    fi
  done
  if ((best_block == 0)); then
    echo "check-toolchain-claims: line ${idx}: no toolchain-probe block for host '${bound_host}'" >&2
    return 1
  fi
  if ((agreed)); then return 0; fi
  if ((best_count > 0)); then best_contradicted=''; fi
  emit_claim_errors "${idx}" "${line}" "${best_missing}" "${best_contradicted}" "${best_block}"
}

# Active compilation takes a following object ("did not build HIP").
# Passive compilation takes the tool group immediately before its verb.
resolve_predicate_tools() {
  local subject="$1" action="$2" list="$3" qualifier="$4"
  local object="${action}${list}"
  local host_note="^${space}(${compiler_note})[[:space:].]*$"
  if [[ "${action}" =~ ^(${compile})$ ]]; then
    object="${list}"
    if [[ "${subject}" =~ ${singular_compile_subject} || "${subject}" =~ ${compile_subject} ]]; then
      object="${BASH_REMATCH[2]}${list}"
    fi
  elif [[ "${qualifier}" =~ ^${space}${host_location} ]]; then
    qualifier="${qualifier#"${BASH_REMATCH[0]}"}"
    if [[ "${qualifier}" =~ ${host_note} ]]; then object+=" ${BASH_REMATCH[1]}"; fi
  fi
  claim_tools "${object}"
}

check_segment() {
  local subject="$1" action="$2" list="$3" qualifier="$4"
  local segment="${subject}${action}${list}${qualifier}" context="${subject} ${qualifier}"
  local block i scope=host rc=0
  local -a tools=() named_hosts=()
  resolve_predicate_tools "${subject}" "${action}" "${list}" "${qualifier}"
  [[ -n "${tools[*]-}" ]] || return 0
  parse_claim_hosts "${segment}"
  if [[ -n "${named_hosts[*]-}" ]]; then
    for ((i = 0; i < ${#named_hosts[@]}; i++)); do
      check_evidence host "${named_hosts[i]}" || rc=1
    done
    return "${rc}"
  fi

  if [[ "${action}" =~ ^${without_tool}$ && ! "${context}" =~ ${p_host} ]]; then return 0; fi
  if [[ "${qualifier}" =~ ${p_host} ]]; then
    scope=host
  elif [[ "${subject}" =~ ${nonhost_subject} || "${qualifier}" =~ ${p_nonhost} ]]; then
    scope=nonhost
  elif [[ "${context}" =~ ${p_nonhost} && ! "${subject}" =~ ${subject_local} &&
    ! "${subject}" =~ ${explicit_host_subject} ]]; then
    scope=nonhost
  elif [[ "${action}" =~ ^(${compile})$ && ! "${context}" =~ ${p_host} ]]; then
    scope=nonhost
  fi
  if ((block_count == 0)); then
    emit_missing_probe "${idx}" "${line}"
    return 1
  fi
  if [[ "${scope}" == host ]]; then
    for ((block = 2; block <= block_count; block++)); do
      if [[ "${block_host[block]}" != "${block_host[1]}" ]]; then
        echo "check-toolchain-claims: line ${idx}: name the probed host for '${segment}'" >&2
        return 1
      fi
    done
  fi
  check_evidence "${scope}" ''
}

# Consume just this predicate's objects and trailing qualifier. Leave the
# next predicate's subject intact, including a compiler starting a status.
check_clause() {
  local tail="$1" subject action match rest before_next list qualifier rc=0
  local remaining=${#tail}
  while ((remaining > 0)) && [[ "${tail}" =~ ${predicate} ]]; do
    match="${BASH_REMATCH[0]}"
    subject="${tail%%"${match}"*}${BASH_REMATCH[1]}"
    action="${BASH_REMATCH[3]}"
    rest="${tail#"${subject}${action}"}"
    before_next="${rest}"
    if [[ "${rest}" =~ ${predicate} ]]; then before_next="${rest%%"${BASH_REMATCH[0]}"*}"; fi
    list='' qualifier=''
    if [[ "${action}" =~ ^(${compile})$ && "${before_next}" =~ ${compile_object} ]]; then
      list="${BASH_REMATCH[1]}"
      before_next="${before_next#"${list}"}"
    fi
    if [[ "${before_next}" =~ ${tool_list} ]]; then
      match="${BASH_REMATCH[1]}"
      list+="${match}"
      before_next="${before_next#"${match}"}"
    fi
    if [[ "${before_next}" =~ ${qualifier_start} ]]; then
      qualifier="${before_next}"
      if [[ "${qualifier}" =~ ${joined_locations} ]]; then
        qualifier="${BASH_REMATCH[0]}"
      elif [[ "${qualifier}" =~ ${next_subject} ]]; then
        qualifier="${qualifier%%"${BASH_REMATCH[0]}"*}"
      fi
    fi
    check_segment "${subject}" "${action}" "${list}" "${qualifier}" || rc=1
    tail="${rest#"${list}${qualifier}"}"
    remaining=${#tail}
  done
  return "${rc}"
}

strip_scoped_no() {
  local rem="$1" i
  for ((i = 0; i < 32; i++)); do
    if [[ ! "${rem}" =~ ${p_no_scope} ]]; then
      stripped_clause="${rem}"
      return 0
    fi
    rem="${rem/"${BASH_REMATCH[0]}"/ , }"
  done
  if [[ "${rem}" =~ ${p_no_scope} ]]; then
    stripped_clause="$1"
  else
    stripped_clause="${rem}"
  fi
}

check_claim() {
  local idx="$1" line="$2" tail="$2" clause stripped_clause='' delimiter n rc=0
  [[ "${line}" =~ ${blank} ]] && return 0
  for ((n = 1; n <= max_clauses_per_line; n++)); do
    clause="${tail}" delimiter=''
    if [[ "${tail}" =~ ${clause_break} ]]; then
      delimiter="${BASH_REMATCH[0]}"
      clause="${tail%%"${delimiter}"*}"
    fi
    strip_scoped_no "${clause}"
    check_clause "${stripped_clause}" || rc=1
    if [[ -z "${delimiter}" ]]; then return "${rc}"; fi
    tail="${tail#"${clause}${delimiter}"}"
  done
  echo "check-toolchain-claims: line ${idx}: exceeds ${max_clauses_per_line} clauses per line" >&2
  return 1
}

# Check claims after collecting evidence from the entire description.
shopt -s nocasematch
fail=0
for ((idx = 1; idx <= ${#lines[@]}; idx++)); do
  if [ "${exempt_lines[idx]:-0}" -eq 1 ]; then
    continue
  fi
  check_claim "${idx}" "${lines[idx]}" || fail=1
done

if [ "${fail}" -ne 0 ]; then
  exit 1
fi

echo "check-toolchain-claims: PASS — toolchain claims verified against probe."
exit 0
