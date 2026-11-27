#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Copyright 2026 Lusoris
#
# scripts/ci/build-oneapi-libvmaf.sh — build libvmaf with oneAPI SYCL,
# stage release artifacts, extract runtime files, and scan licences.
#
# Shared build script called by builder-oneapi2026 in Dockerfile.production-gpu
# and builder-oneapi2026 in Dockerfile.node (HISS-19, ADR-3371).

set -euo pipefail

build_dir="${1:-build}"
repo_root="${2:-$(pwd)}"
dist_dir="${3:-/dist}"
stage_dir="${4:-/stage}"
licence_inputs_dir="${5:-/licence-inputs}"
oneapi_root="${ONEAPI_ROOT:-/opt/intel/oneapi}"
jobs="${VMAF_BUILD_JOBS:-4}"

export ONEAPI_ROOT="${oneapi_root}"
export PATH="${oneapi_root}/compiler/latest/bin:${PATH}"

if ! command -v patchelf >/dev/null; then
  echo "ERROR: patchelf is required to clear the staged CLI's build-tree RUNPATH; the builder stages install it" >&2
  exit 1
fi

# --native-file carries the shared release hardening profile
# (docs/principles.md section 2.2, issue 2748), the same file every release
# build passes to meson.
CC=icx CXX=icpx meson setup "${build_dir}" core \
  --buildtype=release \
  --strip \
  --native-file core/native/release-hardening.ini \
  -Db_lto=false \
  -Denable_cuda=false \
  -Denable_sycl=true \
  -Denable_hip=false \
  -Denable_dnn=disabled \
  -Denable_docs=false \
  -Denable_tests=false

nice -n 10 ninja -C "${build_dir}" -j"${jobs}"

mkdir -p "${dist_dir}/lib" "${dist_dir}/bin" "${dist_dir}/model" "${licence_inputs_dir}"
find "${build_dir}/src" -maxdepth 1 \( -name 'libvmaf.so*' -o -name 'libvmafx.so*' \) -not -type d -exec cp -a {} "${dist_dir}/lib/" \;
cp -d /usr/lib/*-linux-gnu*/libzimg.so* "${dist_dir}/lib/"
cp "${build_dir}/tools/vmaf" "${dist_dir}/bin/"
# Drop the CLI's build-tree RUNPATH $ORIGIN/../src, which resolves nothing
# under /usr/local/bin; the image resolves the library chain through
# LD_LIBRARY_PATH (issue 2748).
patchelf --remove-rpath "${dist_dir}/bin/vmaf"
cp -r model/. "${dist_dir}/model/"

python3 tools/rc1-tester/image/prepare_build.py intel-runtime \
  tools/rc1-tester/image/sycl-runtime.json "${oneapi_root}" "${stage_dir}"

python3 tools/rc1-tester/image/licensing.py scan-build \
  --build "${build_dir}" --repo "${repo_root}" \
  --out "${licence_inputs_dir}/vmafx-sources.json"
