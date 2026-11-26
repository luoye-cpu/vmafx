# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Shared helper functions for tools/external-bench competitor wrappers.
#
# shellcheck shell=bash

# shellcheck disable=SC2034
parse_wrapper_args() {
  local has_engine="$1"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref)
        ref="$2"
        shift 2
        ;;
      --dis)
        dis="$2"
        shift 2
        ;;
      --width)
        width="$2"
        shift 2
        ;;
      --height)
        height="$2"
        shift 2
        ;;
      --out)
        out="$2"
        shift 2
        ;;
      --engine)
        if [[ "$has_engine" -ne 1 ]]; then
          echo "unknown arg: $1" >&2
          usage
          exit 2
        fi
        engine="$2"
        shift 2
        ;;
      --fps)
        fps="$2"
        shift 2
        ;;
      --pixfmt)
        pixfmt="$2"
        shift 2
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        echo "unknown arg: $1" >&2
        usage
        exit 2
        ;;
    esac
  done
}

validate_required_args() {
  local v
  for v in "$@"; do
    if [[ -z "${!v:-}" ]]; then
      echo "missing required --$v" >&2
      usage
      exit 2
    fi
  done
}

resolve_model_dir() {
  if [[ -n "${EXTERNAL_BENCH_MODEL_DIR:-}" ]]; then
    printf '%s\n' "$EXTERNAL_BENCH_MODEL_DIR"
    return 0
  fi
  local lib_dir repo_root
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "$lib_dir/../../.." && pwd)"
  printf '%s/model/tiny\n' "$repo_root"
}

check_model_exists() {
  local model="$1"
  if [[ ! -f "$model" ]]; then
    echo "model not found: $model (set EXTERNAL_BENCH_MODEL_DIR or check repo state)" >&2
    exit 3
  fi
}

require_external_binary() {
  local var_name="$1"
  local hint="$2"
  local bin="${!var_name:-}"

  if [[ -z "$bin" ]]; then
    cat >&2 <<EOF
${var_name} is not set.

${hint}
EOF
    exit 3
  fi
  if [[ ! -x "$bin" ]]; then
    echo "${var_name}=${bin} is not executable" >&2
    exit 3
  fi
}

normalise_output() {
  local src="$1"
  local dst="$2"
  local competitor="$3"
  local metric="${4:-vmaf}"
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  python3 "$lib_dir/normalise_output.py" "$src" "$dst" "$competitor" "$metric"
}
