/**
 *  Copyright 2026 Lusoris
 *  SPDX-License-Identifier: EUPL-1.2
 *
 *  HIP per-feature kernel scaffolding template — first-consumer
 *  mirror of `core/src/cuda/kernel_template.h` (ADR-0221).
 *
 *  T7-10 first-consumer PR (ADR-0241). The CUDA template captured
 *  the lifecycle every fork-added CUDA feature kernel converged on
 *  (private non-blocking stream + 2-event submit/finished pair +
 *  device accumulator + pinned host readback slot). HIP exposes the
 *  same async-stream + event surface as CUDA (`hipStream_t`,
 *  `hipEvent_t`, `hipMemcpyAsync` ...), so the template's shape ports
 *  one-to-one.
 *
 *  Posture: the struct definitions are stable C surface so consumer
 *  TUs can compile without `<hip/hip_runtime.h>`. The lifecycle
 *  helpers `vmaf_hip_kernel_lifecycle_init/_close`,
 *  `vmaf_hip_kernel_readback_alloc/_free`,
 *  `vmaf_hip_kernel_submit_pre_launch`,
 *  `vmaf_hip_kernel_collect_wait` are declared here and defined in
 *  `core/src/hip/kernel_template.c`; since the T7-10b runtime
 *  PR (ADR-0212) the bodies make real HIP calls. Consumers
 *  call them through the same call-graph the CUDA reference uses.
 *
 *  Why a paired .c instead of inline `static inline` helpers like the
 *  CUDA template? Two reasons:
 *
 *    1. The CUDA helpers are inline because they unwrap straight onto
 *       `cu_state->f->cuStreamCreateWithPriority` etc. — every call
 *       resolves at compile time against the `CudaFunctions` driver
 *       table. The HIP helpers receive their runtime handles as
 *       `uintptr_t` (see the lifecycle struct below), so the bodies
 *       call HIP directly; keeping them non-inline concentrates the
 *       HIP calls in one place.
 *    2. Splitting declaration from definition keeps the HIP calls in
 *       one TU and every consumer on stable signatures, so a body
 *       change does not recompile every consumer TU.
 *
 *  The T7-10b runtime PR (ADR-0212; ADR-0241 for the stubs it replaced)
 *  replaced the -ENOSYS bodies with real HIP calls; the struct shape and
 *  helper signatures stayed verbatim — that's the load-bearing
 *  contract the first-consumer PR pinned.
 *
 *  Reference implementation (CUDA): see
 *  `core/src/cuda/kernel_template.h` and
 *  `core/src/feature/cuda/integer_psnr_cuda.c`.
 *  First HIP consumer:
 *  `core/src/feature/hip/integer_psnr_hip.c`.
 *
 *  Migration guide: `docs/backends/kernel-scaffolding.md`.
 */

#ifndef LIBVMAF_HIP_KERNEL_TEMPLATE_H_
#define LIBVMAF_HIP_KERNEL_TEMPLATE_H_

#include <stddef.h>
#include <stdint.h>

#include "common.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Per-frame async lifecycle the HIP feature kernels share.
 *
 * Mirrors `VmafCudaKernelLifecycle` field-for-field. Handles cross
 * the fork's HIP scaffold ABI as `uintptr_t` so this header stays
 * free of `<hip/hip_runtime.h>` (matches the public `libvmaf_hip.h`
 * convention; see ADR-0212). The runtime converts them back through
 * `vmaf_hip_stream_of` and `vmaf_hip_event_of`, which use a union
 * rather than an integer-to-pointer cast.
 *
 * `str`       — private non-blocking stream for the readback path.
 * `submit`    — event recorded on the picture's stream right after
 *               the kernel launch.
 * `finished`  — event recorded on `str` after the readback completes.
 */
typedef struct VmafHipKernelLifecycle {
    uintptr_t str;
    uintptr_t submit;
    uintptr_t finished;
} VmafHipKernelLifecycle;

/*
 * One device-side accumulator + one pinned host readback slot.
 *
 * Mirrors `VmafCudaKernelReadback`. The runtime fills `device` via
 * `hipMalloc` and `host_pinned` via `hipHostMalloc`; a failed allocation
 * frees whatever it acquired and leaves both NULL.
 */
typedef struct VmafHipKernelReadback {
    void *device;
    void *host_pinned;
    size_t bytes;
} VmafHipKernelReadback;

/*
 * Init: create a non-blocking private stream and two timing-disabled
 * events (`hipStreamCreateWithFlags`, `hipEventCreateWithFlags` x2).
 * A partial failure destroys the handles created so far before
 * returning the mapped error.
 *
 * Caller pre-conditions: `lc` is non-NULL and is zeroed by this
 * helper. `ctx` is accepted for the shared lifecycle signature; this
 * helper does not need it.
 *
 * Returns 0 on success or a negative errno.
 */
int vmaf_hip_kernel_lifecycle_init(VmafHipKernelLifecycle *lc, VmafHipContext *ctx);

/*
 * Allocate a (device, pinned-host) readback pair of `bytes` size.
 * The implementation uses `hipMalloc` + `hipHostMalloc`; a failed
 * allocation frees whatever it acquired and leaves `device` and
 * `host_pinned` NULL.
 * `bytes` must be greater than zero.
 *
 * Returns 0 on success or a negative errno.
 */
int vmaf_hip_kernel_readback_alloc(VmafHipKernelReadback *rb, VmafHipContext *ctx, size_t bytes);

/*
 * Per-frame submit-side helper.
 *
 *   1. Zero the device accumulator on `picture_stream` (or `lc->str`
 *      when no picture stream is supplied) with `hipMemsetAsync`.
 *   2. Wait for the dist-side ready event on `picture_stream` with
 *      `hipStreamWaitEvent` when both handles are non-zero.
 *
 * Mirrors `vmaf_cuda_kernel_submit_pre_launch`. The implementation
 * makes these real HIP calls; an unset lifecycle or readback is
 * refused with `-EINVAL`.
 *
 * Stream + event handles cross as `uintptr_t` for the same
 * header-purity reason as the lifecycle struct.
 */
int vmaf_hip_kernel_submit_pre_launch(VmafHipKernelLifecycle *lc, VmafHipContext *ctx,
                                      VmafHipKernelReadback *rb, uintptr_t picture_stream,
                                      uintptr_t dist_ready_event);

/*
 * collect()-side wait point: drains the private stream so the host
 * pinned buffer is safe to read.
 *
 * Mirrors `vmaf_cuda_kernel_collect_wait`. The implementation calls
 * `hipStreamSynchronize(lc->str)`; an uninitialised lifecycle has
 * nothing to wait on and returns 0.
 */
int vmaf_hip_kernel_collect_wait(VmafHipKernelLifecycle *lc, VmafHipContext *ctx);

/*
 * close()-side teardown: drain and destroy the stream, then destroy
 * both events.
 *
 * Mirrors `vmaf_cuda_kernel_lifecycle_close`. The implementation
 * sequences `hipStreamSynchronize` -> `hipStreamDestroy` ->
 * `hipEventDestroy` x2 and aggregates the first error. Zero handles
 * are skipped, so a partially-initialised lifecycle is safe to close.
 */
int vmaf_hip_kernel_lifecycle_close(VmafHipKernelLifecycle *lc, VmafHipContext *ctx);

/*
 * Free the readback pair with `hipFree` + `hipHostFree`, aggregating
 * the first error. Zero handles are skipped, so a partially-allocated
 * readback is safe to free.
 */
int vmaf_hip_kernel_readback_free(VmafHipKernelReadback *rb, VmafHipContext *ctx);

/*
 * Post-launch submit-side helper: records the `finished` event on
 * the private readback stream (`lc->str`) after the DtoH copy is
 * enqueued. `collect_wait` synchronises on `finished` to confirm the
 * readback is complete before the host reads the pinned buffer.
 *
 * Mirrors `vmaf_cuda_kernel_submit_post_record` from
 * `core/src/cuda/kernel_template.h`. PR #612 adds this helper for
 * `float_psnr_hip`; batch-1 (ADR-0372) also requires it for
 * `integer_psnr_hip`. On merge conflict with
 * PR #612 at merge time, keep one copy and discard the duplicate.
 */
int vmaf_hip_kernel_submit_post_record(VmafHipKernelLifecycle *lc, VmafHipContext *ctx);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* LIBVMAF_HIP_KERNEL_TEMPLATE_H_ */
