# Build + run the Phase 1 integer-ADM gate. Tool-versions used on 2026-11-06:
#   mingw64 gcc 16.2.0, glslang 16.6.0, Windows, NVIDIA driver 621.257
#   (Vulkan loader API 1.4.357).
#
# Prereqs outside this repo:
#   - Vulkan-Headers (MIT) on the include path (the host uses VK_NO_PROTOTYPES +
#     dynamic vulkan-1.dll; no SDK, no volk, no VMA required).
#   - the CPU reference links a mingw-built STATIC libvmaf built from public
#     VMAFx master af5ec5c9435b396ae13f0fffc9b29d040786cea8, exactly:
#       git worktree add --detach vmafx af5ec5c94
#       meson setup vmafx-build vmafx/core -Denable_cuda=false -Denable_sycl=false \
#           -Denable_hip=false -Denable_metal=disabled -Denable_tests=false \
#           -Denable_docs=false -Denable_tools=false -Denable_dnn=disabled \
#           -Denable_zimg=false -Ddefault_library=static
#       ninja -C vmafx-build
#     (point LIBVMAF at vmafx-build/src; the archives are linked together
#     with -Wl,--start-group vmafx-build/src/*.a -Wl,--end-group)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)

# 1) compile the compute shader. This reproduces build/integer_adm.spv
#    byte-for-byte from shaders/integer_adm.comp (verified with sha256sum).
glslang -V --target-env vulkan1.3 "$HERE/shaders/integer_adm.comp" \
    -o "$HERE/build/integer_adm.spv"

# 2) CPU reference generator (links the UNMODIFIED public libvmaf API).
gcc -O2 -I "$VMAF_INC" "$HERE/src/cpu_ref.c" -o "$HERE/build/cpu_ref.exe" \
    "$LIBVMAF" -lstdc++ -lm

# 3) Vulkan host (-ffp-contract=off so the host float math matches the CPU).
gcc -O2 -ffp-contract=off -I "$VK_INC" "$HERE/src/vk_adm.c" \
    -o "$HERE/build/vk_adm.exe"

# 4) run both sides on a fixture and compare bit-for-bit.
#    device-id 0x2c02 = RTX 5080, 0x28e0 = RTX 4060 Laptop.
#    Run from the directory holding the fixtures so the recorded paths stay clean.
"$HERE/build/cpu_ref.exe"  --ref "$REF" --dis "$DIS" --w "$W" --h "$H" \
    --frames 48 --fixture "$NAME" --out "$HERE/out/cpu-adm-$NAME.txt"
"$HERE/build/vk_adm.exe"   --ref "$REF" --dis "$DIS" --w "$W" --h "$H" \
    --frames 48 --device-id 0x2c02 --spv "$HERE/build/integer_adm.spv" \
    --out "$HERE/out/vk-adm-$NAME.txt"
python3 -I "$HERE/tools/compare.py" \
    --ref "$HERE/out/cpu-adm-$NAME.txt" --test "$HERE/out/vk-adm-$NAME.txt"
