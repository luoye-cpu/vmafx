#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# scripts/ci/pr-body-gates.sh — run all three rule-enforcement.yml PR-body
# shell gates locally without early stop:
#   - deliverables-check.sh           (ADR-0108)
#   - state-md-touch-check.sh         (ADR-0165)
#   - ffmpeg-patches-surface-check.sh (ADR-0409)
#
# Environment:
#   PR_TITLE          required (non-empty); exit 2 when unset or empty
#   PR_BODY           required (set; empty string allowed); exit 2 when unset
#   BASE_SHA/HEAD_SHA passed through when set
#   PR_BODY_GATES_DIR override gate script directory (for stub tests)

set -euo pipefail

GATES=(deliverables-check.sh state-md-touch-check.sh ffmpeg-patches-surface-check.sh)

usage() {
  echo "Usage: PR_TITLE=<title> PR_BODY=<body> [BASE_SHA=<sha> HEAD_SHA=<sha>] scripts/ci/pr-body-gates.sh" >&2
}

if [ "$#" -ne 0 ]; then
  usage
  exit 2
fi

if [ -z "${PR_TITLE:-}" ]; then
  echo "pr-body-gates: PR_TITLE is unset or empty." >&2
  usage
  exit 2
fi

if [ -z "${PR_BODY+x}" ]; then
  echo "pr-body-gates: PR_BODY is unset." >&2
  usage
  exit 2
fi

export PR_TITLE PR_BODY
if [ -n "${BASE_SHA+x}" ]; then
  export BASE_SHA
fi
if [ -n "${HEAD_SHA+x}" ]; then
  export HEAD_SHA
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
gates_dir="${PR_BODY_GATES_DIR:-${script_dir}}"

pass_count=0
fail_count=0
error_count=0
worst_rc=0

for gate in "${GATES[@]}"; do
  gate_rc=0
  bash "${gates_dir}/${gate}" </dev/null || gate_rc=$?
  printf 'pr-body-gates: %s rc %d\n' "${gate}" "${gate_rc}"
  if [ "${gate_rc}" -eq 0 ]; then
    pass_count=$((pass_count + 1))
  elif [ "${gate_rc}" -eq 1 ]; then
    fail_count=$((fail_count + 1))
    if [ "${worst_rc}" -lt 1 ]; then
      worst_rc=1
    fi
  else
    error_count=$((error_count + 1))
    worst_rc=2
  fi
done

printf 'pr-body-gates: summary pass=%d fail=%d error=%d rc=%d\n' \
  "${pass_count}" "${fail_count}" "${error_count}" "${worst_rc}"
exit "${worst_rc}"
