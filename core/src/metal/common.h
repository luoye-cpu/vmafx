/**
 *  Copyright 2026 Lusoris
 *  SPDX-License-Identifier: BSD-2-Clause-Patent
 */

#ifndef LIBVMAF_METAL_COMMON_H_
#define LIBVMAF_METAL_COMMON_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Created by ADR-0361 / T8-1 (mirrors the HIP creation, ADR-0212);
 * common.mm, picture_metal.mm, picture_import.mm, kernel_template.mm and
 * dispatch_strategy.c are the live Metal runtime (Objective-C++ with ARC,
 * ADR-0420), and feature/metal/<feature>_metal.mm are the feature hosts.
 */

typedef struct VmafMetalContext VmafMetalContext;

int vmaf_metal_context_new(VmafMetalContext **ctx, int device_index);
void vmaf_metal_context_destroy(VmafMetalContext *ctx);
int vmaf_metal_device_count(void);

/*
 * Internal accessors for the bridge-retained Metal handles stashed on
 * the context. Returns `void *` (an opaque id<MTLDevice> /
 * id<MTLCommandQueue> reference under the hood) so consumers compiling
 * as pure-C can pass it through to Obj-C++ TUs that bridge-cast back
 * to the real Metal type. The pointer's lifetime is tied to the
 * context — callers must NOT release it; the context owns the +1
 * retain and drops it in `vmaf_metal_context_destroy`. Returns NULL
 * for a NULL context.
 *
 * Opaque handle + accessor (the two functions below), never
 * struct-layout coupling.
 */
void *vmaf_metal_context_device_handle(VmafMetalContext *ctx);
void *vmaf_metal_context_queue_handle(VmafMetalContext *ctx);

#ifdef __cplusplus
}
#endif

#endif /* LIBVMAF_METAL_COMMON_H_ */
