/* vk_adm.c — VMAFx VK PoC Phase 1: standalone integer-ADM host (Windows).
 *
 * Drives the patched integer_adm.comp (4 stages x 4 scales) with the same
 * dynlink pattern as ../phase0/src/vk_probe.c: LoadLibraryA("vulkan-1.dll"),
 * vkGetInstanceProcAddr, VK_NO_PROTOTYPES, Vulkan-Headers on the include path.
 *
 * Contract anchor: the CPU integer ADM of VMAFx master
 * af5ec5c9435b396ae13f0fffc9b29d040786cea8 (unmodified —
 * core/src/feature/integer_adm.c + integer_adm_kernels.h), which the gate
 * verified bit-identical to the Netflix-style CPU reference the removed
 * backend was written against. Every conclude expression below is a
 * line-by-line port of that CPU float math (file: integer_adm_kernels.h
 * unless stated):
 *   - dwt_quant_step        (:67-86)   double log10/pow chain
 *   - adm_csf_den_scale     (integer_adm.c:490-513 + :635-690) scale-0
 *   - adm_csf_den_s123      (integer_adm.c:515-538 + :714-776) s1..3
 *   - adm_cm                (:946-1084, esp. :1051-1057, :1071-1076) scale-0 CM
 *   - i4_adm_cm             (:1116-1269, esp. :1174-1182, :1228-1243) s1..3 CM
 *   - integer_compute_adm   (integer_adm.c:902-947, :909 numden_limit)
 * Known snapshot-host bugs fixed here (README.md, fix 6):
 *   - numden_limit uses the BASE frame w*h (not the last scale's half dims)
 *   - s>0 CM conclude uses CPU's float division `(float)(accum / final_shift_f)`
 *     instead of the snapshot's double division
 *   - powf_add uses CPU's double product `(float)(area * (double)nw)` not
 *     `(float)area * (float)nw`
 *
 * Usage:
 *   vk_adm.exe --ref R.yuv --dis D.yuv --w 576 --h 324 --frames 48
 *              [--device-id 0x2c02 | --device N] [--spv path]
 *              --out out.txt [--dump-accum accum.txt]
 */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

/* ------------------------------------------------------------------ */
/* CPU contract constants (VMAFx master core/src/feature/integer_adm.h). */
/* ------------------------------------------------------------------ */

#define ADM_BORDER_FACTOR 0.1
#define DEFAULT_ADM_ENHN_GAIN_LIMIT 100.0
#define DEFAULT_ADM_NORM_VIEW_DIST 3.0
#define DEFAULT_ADM_REF_DISPLAY_HEIGHT 1080
#define DEFAULT_ADM_CSF_SCALE 1.0
#define DEFAULT_ADM_CSF_DIAG_SCALE 1.0
#define DEFAULT_ADM_NOISE_WEIGHT 0.03125
#define ONE_BY_15 8738            /* integer_adm.h:210 */
#define ADM_NUM_SCALES 4
#define ADM_NUM_BANDS 3
#define ADM_DIV_LOOKUP_SIZE 65537

static const float dwt_basis_amp[6][4] = {
    {0.62171f, 0.67234f, 0.72709f, 0.67234f}, {0.34537f, 0.41317f, 0.49428f, 0.41317f},
    {0.18004f, 0.22727f, 0.28688f, 0.22727f}, {0.091401f, 0.11792f, 0.15214f, 0.11792f},
    {0.045943f, 0.059758f, 0.077727f, 0.059758f}, {0.023013f, 0.030018f, 0.039156f, 0.030018f},
};
static const struct DwtModelParams {
    float a, k, f0;
    float g[4];
} dwt_model_Y = {0.495f, 0.466f, 0.401f, {1.501f, 1.0f, 0.534f, 1.0f}};

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* CPU dwt_quant_step (integer_adm_kernels.h:67-86): the log10/pow chain
 * runs in double; only the named locals are float. Keep the exact
 * conversion points. */
static float dwt_quant_step_cpu(int lambda, int theta, double nvd, int rdh)
{
    float r = (float)(nvd * (double)rdh * M_PI / 180.0);
    float temp = (float)log10(pow(2.0, (double)(lambda + 1)) * (double)dwt_model_Y.f0 *
                              (double)dwt_model_Y.g[theta] / (double)r);
    float kt = dwt_model_Y.k * temp * temp; /* float chain (k*temp)*temp */
    double q = 2.0 * (double)dwt_model_Y.a * pow(10.0, (double)kt) /
               (double)dwt_basis_amp[lambda][theta];
    return (float)q;
}

/* ------------------------------------------------------------------ */
/* Push constants — must mirror `Params` in integer_adm.comp.          */
/* ------------------------------------------------------------------ */

typedef struct {
    uint32_t cur_w;
    uint32_t cur_h;
    uint32_t half_w;
    uint32_t half_h;
    uint32_t buf_stride;
    uint32_t in_stride;
    uint32_t v_shift;
    uint32_t v_add;
    uint32_t h_shift;
    uint32_t i_rfactor_h;
    uint32_t i_rfactor_v;
    uint32_t i_rfactor_d;
    uint32_t num_workgroups_x;
    int32_t gain_hi;
    int32_t gain_lo;
    int32_t active_left;
    int32_t active_top;
    int32_t active_right;
    int32_t active_bottom;
    uint32_t csf_shift_sq;
    uint32_t csf_shift_cub;
    uint32_t csf_shift_accum;
    uint32_t cm_shift_inner;
    uint32_t cm_shift_sub_h;
    uint32_t cm_shift_sub_d;
    uint32_t cm_shift_xsq_h;
    uint32_t cm_shift_xsq_d;
    uint32_t cm_shift_xcub_h;
    uint32_t cm_shift_xcub_d;
} AdmPushConsts; /* 28 x 4 = 112 bytes */

typedef struct {
    int32_t gain_hi;
    int32_t gain_lo;
} GainQ31;

/* snapshot integer_adm_vulkan.c:315-320 (gain limit is 100.0 by default) */
static GainQ31 gain_to_q31(double gain_limit)
{
    int64_t q = (int64_t)llround(gain_limit * (double)(1LL << 31));
    GainQ31 g = {(int32_t)(q >> 16), (int32_t)(q & 0xFFFF)};
    return g;
}

/* snapshot integer_adm_vulkan.c:841-871; verified against CPU
 * adm_cm/i4_adm_cm/adm_csf_den_* shift derivations. */
static void compute_csf_cm_shifts(unsigned hw, unsigned hh, int scale, int active_w, int active_h,
                                  AdmPushConsts *pc)
{
    if (scale == 0) {
        pc->csf_shift_sq = 0;
        pc->csf_shift_cub = 0;
        int area = active_w * active_h;
        int sa = (int)ceil(log2((double)area) - 20.0);
        pc->csf_shift_accum = sa > 0 ? (uint32_t)sa : 0u;
    } else {
        pc->csf_shift_sq = (scale == 2) ? 30u : 31u;
        pc->csf_shift_cub = (uint32_t)ceil(log2((double)active_w));
        pc->csf_shift_accum = (uint32_t)ceil(log2((double)active_h));
    }
    pc->cm_shift_inner = (uint32_t)ceil(log2((double)hh));
    if (scale == 0) {
        pc->cm_shift_sub_h = 10u;
        pc->cm_shift_sub_d = 12u;
        pc->cm_shift_xsq_h = 29u;
        pc->cm_shift_xsq_d = 30u;
        pc->cm_shift_xcub_h = (uint32_t)(ceil(log2((double)hw)) - 4.0);
        pc->cm_shift_xcub_d = (uint32_t)(ceil(log2((double)hw)) - 3.0);
    } else {
        pc->cm_shift_sub_h = 15u;
        pc->cm_shift_sub_d = 15u;
        pc->cm_shift_xsq_h = 30u;
        pc->cm_shift_xsq_d = 30u;
        pc->cm_shift_xcub_h = (uint32_t)ceil(log2((double)hw));
        pc->cm_shift_xcub_d = (uint32_t)ceil(log2((double)hw));
    }
}

/* ------------------------------------------------------------------ */
/* CPU-exact conclude functions.                                       */
/* ------------------------------------------------------------------ */

/* CPU adm_csf_den_scale (1302-1402) / adm_csf_den_s123 (1404-1507).
 * `accum[b]` = per-band uint64 total of the per-row shifted row sums
 * (the shader applies the per-row rounding shift; the host only sums). */
static void conclude_csf_den_host(const uint64_t accum[3], int hh, int hw, int scale, float *result)
{
    const double nvd = DEFAULT_ADM_NORM_VIEW_DIST;
    const int rdh = DEFAULT_ADM_REF_DISPLAY_HEIGHT;
    const double nw = DEFAULT_ADM_NOISE_WEIGHT;

    int left = (int)((double)hw * ADM_BORDER_FACTOR - 0.5);
    int top = (int)((double)hh * ADM_BORDER_FACTOR - 0.5);
    int right = hw - left;
    int bottom = hh - top;

    float factor1 = 1.0f / dwt_quant_step_cpu(scale, 1, nvd, rdh);
    float factor2 = 1.0f / dwt_quant_step_cpu(scale, 2, nvd, rdh);
    const float rfactor[3] = {factor1, factor1, factor2};

    /* CPU: powf((bottom - top) * (right - left) * adm_noise_weight, 1/3) —
     * int*int*double product in double, converted to float at the call. */
    float powf_add =
        powf((float)((double)((bottom - top) * (right - left)) * nw), 1.0f / 3.0f);

    float den_scale = 0.0f;
    for (int b = 0; b < 3; b++) {
        double csf;
        if (scale == 0) {
            int area = (bottom - top) * (right - left);
            int32_t shift_accum = (int32_t)ceil(log2((double)area) - 20.0);
            if (shift_accum < 0)
                shift_accum = 0;
            (void)shift_accum; /* per-row shift already applied in the shader */
            double shift_csf = pow(2.0, 18.0 - (double)shift_accum);
            csf = (double)(accum[b] / shift_csf) * pow((double)rfactor[b], 3.0);
        } else {
            static const uint32_t accum_convert[3] = {32, 27, 23};
            uint32_t shift_cub = (uint32_t)ceil(log2((double)(right - left)));
            uint32_t shift_accum = (uint32_t)ceil(log2((double)(bottom - top)));
            double shift_csf =
                pow(2.0, (double)(accum_convert[scale - 1] - shift_accum - shift_cub));
            csf = (double)(accum[b] / shift_csf) * pow((double)rfactor[b], 3.0);
        }
        den_scale += powf((float)csf, 1.0f / 3.0f) + powf_add;
    }
    /* CPU returns ((h + v) + d); the loop above accumulates the same order. */
    *result = den_scale;
}

/* CPU adm_cm (1509-1920, esp. 1899-1901) / i4_adm_cm (1921-2358, esp.
 * 1974-1982 + 2344-2357). */
static void conclude_cm_host(const int64_t accum[3], int hh, int hw, int scale, float *result)
{
    const double nw = DEFAULT_ADM_NOISE_WEIGHT;

    int left = (int)((double)hw * ADM_BORDER_FACTOR - 0.5);
    int top = (int)((double)hh * ADM_BORDER_FACTOR - 0.5);
    int right = hw - left;
    int bottom = hh - top;
    float powf_add =
        powf((float)((double)((bottom - top) * (right - left)) * nw), 1.0f / 3.0f);

    float num_scale = 0.0f;
    if (scale == 0) {
        uint32_t shift_inner = (uint32_t)ceil(log2((double)hh));
        uint32_t xhcub = (uint32_t)(ceil(log2((double)hw)) - 4.0);
        uint32_t xdcub = (uint32_t)(ceil(log2((double)hw)) - 3.0);
        /* CPU: (float)(accum / pow(2, (52 - xhcub - inner))) — double division. */
        float f_h = (float)((double)accum[0] /
                            pow(2.0, (double)(52u - xhcub - shift_inner)));
        float f_v = (float)((double)accum[1] /
                            pow(2.0, (double)(52u - xhcub - shift_inner)));
        float f_d = (float)((double)accum[2] /
                            pow(2.0, (double)(57u - xdcub - shift_inner)));
        /* CPU: return (num_scale_h + num_scale_v + num_scale_d) —
         * left-associated ((h + v) + d); float addition is not
         * associative, so the grouping must match exactly. */
        num_scale = ((powf(f_h, 1.0f / 3.0f) + powf_add) +
                     (powf(f_v, 1.0f / 3.0f) + powf_add)) +
                    (powf(f_d, 1.0f / 3.0f) + powf_add);
    } else {
        static const uint32_t cm_final_shift[3] = {45u, 39u, 36u};
        uint32_t shift_cub = (uint32_t)ceil(log2((double)hw));
        uint32_t shift_inner = (uint32_t)ceil(log2((double)hh));
        /* CPU: float final_shift[3] = { pow(2, (45 - cub - inner)), ... }
         * then (float)(accum / final_shift[scale-1]) — int64->float conversion
         * followed by a FLOAT division. All three bands use final_shift[scale-1]. */
        float fs = (float)pow(2.0, (double)(cm_final_shift[scale - 1] - shift_cub - shift_inner));
        float f_h = (float)(accum[0] / fs);
        float f_v = (float)(accum[1] / fs);
        float f_d = (float)(accum[2] / fs);
        /* CPU: left-associated ((h + v) + d) — see scale-0 branch note. */
        num_scale = ((powf(f_h, 1.0f / 3.0f) + powf_add) +
                     (powf(f_v, 1.0f / 3.0f) + powf_add)) +
                    (powf(f_d, 1.0f / 3.0f) + powf_add);
    }
    *result = num_scale;
}

/* ------------------------------------------------------------------ */
/* Vulkan dynlink scaffolding (pattern from ../phase0/src/vk_probe.c). */
/* ------------------------------------------------------------------ */

static HMODULE g_vk;
static PFN_vkGetInstanceProcAddr g_gipa;
static VkInstance g_instance;
static VkPhysicalDevice g_phys;
static VkDevice g_device;
static uint32_t g_compute_family;

/* All entry points are file-scope globals resolved through
 * vkGetInstanceProcAddr (device-level functions are instance-resolvable). */
static PFN_vkCreateInstance pfn_vkCreateInstance;
static PFN_vkEnumeratePhysicalDevices pfn_vkEnumeratePhysicalDevices;
static PFN_vkGetPhysicalDeviceProperties pfn_vkGetPhysicalDeviceProperties;
static PFN_vkGetPhysicalDeviceFeatures2 pfn_vkGetPhysicalDeviceFeatures2;
static PFN_vkGetPhysicalDeviceQueueFamilyProperties pfn_vkGetPhysicalDeviceQueueFamilyProperties;
static PFN_vkGetPhysicalDeviceMemoryProperties pfn_vkGetPhysicalDeviceMemoryProperties;
static PFN_vkCreateDevice pfn_vkCreateDevice;
static PFN_vkGetDeviceQueue pfn_vkGetDeviceQueue;
static PFN_vkCreateBuffer pfn_vkCreateBuffer;
static PFN_vkGetBufferMemoryRequirements pfn_vkGetBufferMemoryRequirements;
static PFN_vkAllocateMemory pfn_vkAllocateMemory;
static PFN_vkBindBufferMemory pfn_vkBindBufferMemory;
static PFN_vkMapMemory pfn_vkMapMemory;
static PFN_vkFreeMemory pfn_vkFreeMemory;
static PFN_vkDestroyBuffer pfn_vkDestroyBuffer;
static PFN_vkCreateDescriptorSetLayout pfn_vkCreateDescriptorSetLayout;
static PFN_vkDestroyDescriptorSetLayout pfn_vkDestroyDescriptorSetLayout;
static PFN_vkCreateDescriptorPool pfn_vkCreateDescriptorPool;
static PFN_vkDestroyDescriptorPool pfn_vkDestroyDescriptorPool;
static PFN_vkAllocateDescriptorSets pfn_vkAllocateDescriptorSets;
static PFN_vkUpdateDescriptorSets pfn_vkUpdateDescriptorSets;
static PFN_vkCreatePipelineLayout pfn_vkCreatePipelineLayout;
static PFN_vkDestroyPipelineLayout pfn_vkDestroyPipelineLayout;
static PFN_vkCreateShaderModule pfn_vkCreateShaderModule;
static PFN_vkDestroyShaderModule pfn_vkDestroyShaderModule;
static PFN_vkCreateComputePipelines pfn_vkCreateComputePipelines;
static PFN_vkDestroyPipeline pfn_vkDestroyPipeline;
static PFN_vkCreateCommandPool pfn_vkCreateCommandPool;
static PFN_vkDestroyCommandPool pfn_vkDestroyCommandPool;
static PFN_vkAllocateCommandBuffers pfn_vkAllocateCommandBuffers;
static PFN_vkBeginCommandBuffer pfn_vkBeginCommandBuffer;
static PFN_vkEndCommandBuffer pfn_vkEndCommandBuffer;
static PFN_vkCmdBindPipeline pfn_vkCmdBindPipeline;
static PFN_vkCmdBindDescriptorSets pfn_vkCmdBindDescriptorSets;
static PFN_vkCmdPushConstants pfn_vkCmdPushConstants;
static PFN_vkCmdDispatch pfn_vkCmdDispatch;
static PFN_vkCmdPipelineBarrier pfn_vkCmdPipelineBarrier;
static PFN_vkCreateFence pfn_vkCreateFence;
static PFN_vkDestroyFence pfn_vkDestroyFence;
static PFN_vkResetFences pfn_vkResetFences;
static PFN_vkQueueSubmit pfn_vkQueueSubmit;
static PFN_vkWaitForFences pfn_vkWaitForFences;
static PFN_vkDeviceWaitIdle pfn_vkDeviceWaitIdle;
static PFN_vkDestroyDevice pfn_vkDestroyDevice;

static void die(const char *msg, long code);

#define V(name) \
    pfn_##name = (PFN_##name)(uintptr_t)g_gipa(g_instance, #name); \
    if (!pfn_##name) die("entry point missing: " #name, 0)

static void load_vk_instance_entrypoint(void)
{
    /* vkCreateInstance must resolve without an instance handle. */
    pfn_vkCreateInstance = (PFN_vkCreateInstance)(uintptr_t)g_gipa(NULL, "vkCreateInstance");
    if (!pfn_vkCreateInstance) die("entry point missing: vkCreateInstance", 0);
}

static void load_vk_entrypoints(void)
{
    /* everything else needs a valid instance handle */
    V(vkEnumeratePhysicalDevices); V(vkGetPhysicalDeviceProperties);
    V(vkGetPhysicalDeviceFeatures2); V(vkGetPhysicalDeviceQueueFamilyProperties);
    V(vkGetPhysicalDeviceMemoryProperties); V(vkCreateDevice); V(vkGetDeviceQueue);
    V(vkCreateBuffer); V(vkGetBufferMemoryRequirements); V(vkAllocateMemory);
    V(vkBindBufferMemory); V(vkMapMemory); V(vkFreeMemory); V(vkDestroyBuffer);
    V(vkCreateDescriptorSetLayout); V(vkDestroyDescriptorSetLayout);
    V(vkCreateDescriptorPool); V(vkDestroyDescriptorPool);
    V(vkAllocateDescriptorSets); V(vkUpdateDescriptorSets);
    V(vkCreatePipelineLayout); V(vkDestroyPipelineLayout);
    V(vkCreateShaderModule); V(vkDestroyShaderModule);
    V(vkCreateComputePipelines); V(vkDestroyPipeline);
    V(vkCreateCommandPool); V(vkDestroyCommandPool); V(vkAllocateCommandBuffers);
    V(vkBeginCommandBuffer); V(vkEndCommandBuffer);
    V(vkCmdBindPipeline); V(vkCmdBindDescriptorSets); V(vkCmdPushConstants);
    V(vkCmdDispatch); V(vkCmdPipelineBarrier);
    V(vkCreateFence); V(vkDestroyFence); V(vkResetFences);
    V(vkQueueSubmit); V(vkWaitForFences); V(vkDeviceWaitIdle); V(vkDestroyDevice);
}

/* with VK_NO_PROTOTYPES the call sites below resolve to the pointers */
#define vkCreateInstance pfn_vkCreateInstance
#define vkEnumeratePhysicalDevices pfn_vkEnumeratePhysicalDevices
#define vkGetPhysicalDeviceProperties pfn_vkGetPhysicalDeviceProperties
#define vkGetPhysicalDeviceFeatures2 pfn_vkGetPhysicalDeviceFeatures2
#define vkGetPhysicalDeviceQueueFamilyProperties pfn_vkGetPhysicalDeviceQueueFamilyProperties
#define vkGetPhysicalDeviceMemoryProperties pfn_vkGetPhysicalDeviceMemoryProperties
#define vkCreateDevice pfn_vkCreateDevice
#define vkGetDeviceQueue pfn_vkGetDeviceQueue
#define vkCreateBuffer pfn_vkCreateBuffer
#define vkGetBufferMemoryRequirements pfn_vkGetBufferMemoryRequirements
#define vkAllocateMemory pfn_vkAllocateMemory
#define vkBindBufferMemory pfn_vkBindBufferMemory
#define vkMapMemory pfn_vkMapMemory
#define vkFreeMemory pfn_vkFreeMemory
#define vkDestroyBuffer pfn_vkDestroyBuffer
#define vkCreateDescriptorSetLayout pfn_vkCreateDescriptorSetLayout
#define vkDestroyDescriptorSetLayout pfn_vkDestroyDescriptorSetLayout
#define vkCreateDescriptorPool pfn_vkCreateDescriptorPool
#define vkDestroyDescriptorPool pfn_vkDestroyDescriptorPool
#define vkAllocateDescriptorSets pfn_vkAllocateDescriptorSets
#define vkUpdateDescriptorSets pfn_vkUpdateDescriptorSets
#define vkCreatePipelineLayout pfn_vkCreatePipelineLayout
#define vkDestroyPipelineLayout pfn_vkDestroyPipelineLayout
#define vkCreateShaderModule pfn_vkCreateShaderModule
#define vkDestroyShaderModule pfn_vkDestroyShaderModule
#define vkCreateComputePipelines pfn_vkCreateComputePipelines
#define vkDestroyPipeline pfn_vkDestroyPipeline
#define vkCreateCommandPool pfn_vkCreateCommandPool
#define vkDestroyCommandPool pfn_vkDestroyCommandPool
#define vkAllocateCommandBuffers pfn_vkAllocateCommandBuffers
#define vkBeginCommandBuffer pfn_vkBeginCommandBuffer
#define vkEndCommandBuffer pfn_vkEndCommandBuffer
#define vkCmdBindPipeline pfn_vkCmdBindPipeline
#define vkCmdBindDescriptorSets pfn_vkCmdBindDescriptorSets
#define vkCmdPushConstants pfn_vkCmdPushConstants
#define vkCmdDispatch pfn_vkCmdDispatch
#define vkCmdPipelineBarrier pfn_vkCmdPipelineBarrier
#define vkCreateFence pfn_vkCreateFence
#define vkDestroyFence pfn_vkDestroyFence
#define vkResetFences pfn_vkResetFences
#define vkQueueSubmit pfn_vkQueueSubmit
#define vkWaitForFences pfn_vkWaitForFences
#define vkDeviceWaitIdle pfn_vkDeviceWaitIdle
#define vkDestroyDevice pfn_vkDestroyDevice

static void die(const char *msg, long code)
{
    fprintf(stderr, "FATAL: %s (%ld)\n", msg, code);
    exit(2);
}

typedef struct {
    VkBuffer buf;
    VkDeviceMemory mem;
    void *mapped;
    VkDeviceSize size;
} VBuf;

static VkPhysicalDeviceMemoryProperties g_memprops;

static void vbuf_create(VkDeviceSize size, VBuf *b)
{
    VkBufferCreateInfo bci; memset(&bci, 0, sizeof(bci));
    bci.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
    bci.size = size;
    bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    if (vkCreateBuffer(g_device, &bci, NULL, &b->buf) != VK_SUCCESS) die("vkCreateBuffer", 0);
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_device, b->buf, &mr);
    uint32_t mtype = 0xffffffff;
    for (uint32_t m = 0; m < g_memprops.memoryTypeCount; m++) {
        if ((mr.memoryTypeBits & (1u << m)) &&
            (g_memprops.memoryTypes[m].propertyFlags &
             (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
                (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
            mtype = m; break;
        }
    }
    if (mtype == 0xffffffff) die("no host-visible coherent memory type", 0);
    VkMemoryAllocateInfo mai; memset(&mai, 0, sizeof(mai));
    mai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    mai.allocationSize = mr.size; mai.memoryTypeIndex = mtype;
    if (vkAllocateMemory(g_device, &mai, NULL, &b->mem) != VK_SUCCESS) die("vkAllocateMemory", 0);
    if (vkBindBufferMemory(g_device, b->buf, b->mem, 0) != VK_SUCCESS) die("vkBindBufferMemory", 0);
    if (vkMapMemory(g_device, b->mem, 0, mr.size, 0, &b->mapped) != VK_SUCCESS) die("vkMapMemory", 0);
    b->size = mr.size;
    memset(b->mapped, 0, (size_t)mr.size);
}

static void vbuf_destroy(VBuf *b)
{
    if (b->mem) vkFreeMemory(g_device, b->mem, NULL);
    if (b->buf) vkDestroyBuffer(g_device, b->buf, NULL);
    memset(b, 0, sizeof(*b));
}

static const char *basename_of(const char *p)
{
    const char *s = p;
    for (const char *q = p; *q; q++)
        if (*q == '/' || *q == '\\') s = q + 1;
    return s;
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

int main(int argc, char **argv)
{
    const char *ref_path = NULL, *dis_path = NULL, *spv_path = "build/integer_adm.spv";
    const char *out_path = NULL, *dump_path = NULL;
    const char *dump_csf_path = NULL;
    const char *fixture_name = NULL;
    int dump_csf_scale = 1, dump_csf_frame = -1;
    int w = 0, h = 0, frames = 48;
    int device_index = -1;
    uint32_t want_device_id = 0x2c02, want_vendor_id = 0x10de;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ref") && i + 1 < argc) ref_path = argv[++i];
        else if (!strcmp(argv[i], "--dis") && i + 1 < argc) dis_path = argv[++i];
        else if (!strcmp(argv[i], "--w") && i + 1 < argc) w = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--h") && i + 1 < argc) h = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--device") && i + 1 < argc) device_index = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--device-id") && i + 1 < argc)
            want_device_id = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--spv") && i + 1 < argc) spv_path = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
        else if (!strcmp(argv[i], "--dump-accum") && i + 1 < argc) dump_path = argv[++i];
        else if (!strcmp(argv[i], "--dump-csf-f") && i + 1 < argc) dump_csf_path = argv[++i];
        else if (!strcmp(argv[i], "--dump-csf-scale") && i + 1 < argc) dump_csf_scale = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--dump-csf-frame") && i + 1 < argc) dump_csf_frame = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--fixture") && i + 1 < argc) fixture_name = argv[++i];
        else { fprintf(stderr, "unknown arg %s\n", argv[i]); return 2; }
    }
    if (!ref_path || !dis_path || !w || !h || !out_path) {
        fprintf(stderr, "usage: vk_adm --ref R --dis D --w W --h H --frames N "
                        "--out F [--fixture NAME] [--device-id ID] [--device N] "
                        "[--spv S] [--dump-accum F]\n");
        return 2;
    }

    /* ---- per-scale geometry ---- */
    unsigned scale_w[ADM_NUM_SCALES], scale_h[ADM_NUM_SCALES];
    unsigned scale_hw[ADM_NUM_SCALES], scale_hh[ADM_NUM_SCALES];
    {
        unsigned cw = (unsigned)w, ch = (unsigned)h;
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            scale_w[s] = cw; scale_h[s] = ch;
            scale_hw[s] = (cw + 1) / 2; scale_hh[s] = (ch + 1) / 2;
            cw = scale_hw[s]; ch = scale_hh[s];
        }
    }
    unsigned half_w0 = scale_hw[0], half_h0 = scale_hh[0];
    unsigned buf_stride = (half_w0 + 3u) & ~3u;

    /* ---- per-scale integer rfactors (CPU adm_csf/adm_cm/i4_adm_csf) ---- */
    uint32_t i_rfactor[ADM_NUM_SCALES][3];
    {
        const double nvd = DEFAULT_ADM_NORM_VIEW_DIST;
        const int rdh = DEFAULT_ADM_REF_DISPLAY_HEIGHT;
        const double csf_scale = DEFAULT_ADM_CSF_SCALE, diag_scale = DEFAULT_ADM_CSF_DIAG_SCALE;
        int hardcode = (fabs(nvd * rdh - DEFAULT_ADM_NORM_VIEW_DIST * DEFAULT_ADM_REF_DISPLAY_HEIGHT) < 1.0e-8);
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            float f1 = csf_scale / dwt_quant_step_cpu(s, 1, nvd, rdh);
            float f2 = diag_scale / dwt_quant_step_cpu(s, 2, nvd, rdh);
            if (s == 0 && hardcode) {
                /* CPU tabulates {36453,36453,49417} for default
                 * geometry + WATSON97 (adm_csf_fixed_point.h:221-223,
                 * gate :195-202; narrowing integer_adm_kernels.h:148). */
                i_rfactor[0][0] = 36453; i_rfactor[0][1] = 36453; i_rfactor[0][2] = 49417;
            } else if (s == 0) {
                i_rfactor[0][0] = (uint32_t)((double)f1 * pow(2.0, 21.0));
                i_rfactor[0][1] = (uint32_t)((double)f1 * pow(2.0, 21.0));
                i_rfactor[0][2] = (uint32_t)((double)f2 * pow(2.0, 23.0));
            } else {
                i_rfactor[s][0] = (uint32_t)((double)f1 * pow(2.0, 32.0));
                i_rfactor[s][1] = (uint32_t)((double)f1 * pow(2.0, 32.0));
                i_rfactor[s][2] = (uint32_t)((double)f2 * pow(2.0, 32.0));
            }
        }
    }
    GainQ31 gq = gain_to_q31(DEFAULT_ADM_ENHN_GAIN_LIMIT);

    /* ---- Vulkan init ---- */
    g_vk = LoadLibraryA("vulkan-1.dll");
    if (!g_vk) die("vulkan-1.dll not found", 0);
    g_gipa = (PFN_vkGetInstanceProcAddr)(uintptr_t)GetProcAddress(g_vk, "vkGetInstanceProcAddr");
    if (!g_gipa) die("vkGetInstanceProcAddr missing", 0);

    load_vk_instance_entrypoint();
    VkApplicationInfo app; memset(&app, 0, sizeof(app));
    app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app.pApplicationName = "vmafx-vk-adm-poc";
    app.applicationVersion = 1;
    VkInstanceCreateInfo ici; memset(&ici, 0, sizeof(ici));
    ici.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    ici.pApplicationInfo = &app;
    uint32_t want_api[] = {VK_MAKE_API_VERSION(0, 1, 3, 0), VK_MAKE_API_VERSION(0, 1, 1, 0),
                           VK_MAKE_API_VERSION(0, 1, 0, 0)};
    VkResult r = VK_ERROR_INCOMPATIBLE_DRIVER;
    for (int i = 0; i < 3; i++) {
        app.apiVersion = want_api[i];
        r = vkCreateInstance(&ici, NULL, &g_instance);
        if (r == VK_SUCCESS) break;
    }
    if (r != VK_SUCCESS) die("vkCreateInstance", (long)r);

    load_vk_entrypoints();

    uint32_t np = 0;
    vkEnumeratePhysicalDevices(g_instance, &np, NULL);
    if (!np) die("no Vulkan physical devices", 0);
    VkPhysicalDevice *devs = malloc(np * sizeof(*devs));
    vkEnumeratePhysicalDevices(g_instance, &np, devs);
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(devs[i], &p);
        printf("  [%u] %s vendor=0x%04x device=0x%04x type=%d\n", i, p.deviceName,
               p.vendorID, p.deviceID, (int)p.deviceType);
    }
    if (device_index >= 0 && (uint32_t)device_index < np) g_phys = devs[device_index];
    else {
        for (uint32_t i = 0; i < np; i++) {
            VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(devs[i], &p);
            if (p.vendorID == want_vendor_id && p.deviceID == want_device_id) { g_phys = devs[i]; break; }
        }
        if (!g_phys) die("requested device id not found", (long)want_device_id);
    }
    VkPhysicalDeviceProperties props; vkGetPhysicalDeviceProperties(g_phys, &props);
    VkPhysicalDeviceFeatures2 f2; memset(&f2, 0, sizeof(f2));
    f2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    vkGetPhysicalDeviceFeatures2(g_phys, &f2);
    printf("selected: %s (vendor 0x%04x device 0x%04x) shaderInt64=%d\n", props.deviceName,
           props.vendorID, props.deviceID, (int)f2.features.shaderInt64);
    if (!f2.features.shaderInt64) die("shaderInt64 not supported", 0);

    uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(g_phys, &nq, NULL);
    VkQueueFamilyProperties *qp = calloc(nq ? nq : 1, sizeof(*qp));
    vkGetPhysicalDeviceQueueFamilyProperties(g_phys, &nq, qp);
    for (uint32_t i = 0; i < nq; i++)
        if (qp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { g_compute_family = i; break; }
    if (!(qp[g_compute_family].queueFlags & VK_QUEUE_COMPUTE_BIT)) die("no compute queue", 0);

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci; memset(&qci, 0, sizeof(qci));
    qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    qci.queueFamilyIndex = g_compute_family; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    VkPhysicalDeviceFeatures enabled; memset(&enabled, 0, sizeof(enabled));
    enabled.shaderInt64 = VK_TRUE;
    VkDeviceCreateInfo dci; memset(&dci, 0, sizeof(dci));
    dci.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    dci.enabledExtensionCount = 0;
    dci.pEnabledFeatures = &enabled;
    if (vkCreateDevice(g_phys, &dci, NULL, &g_device) != VK_SUCCESS) die("vkCreateDevice", 0);
    VkQueue queue; vkGetDeviceQueue(g_device, g_compute_family, 0, &queue);

    /* ---- buffers ---- */
    vkGetPhysicalDeviceMemoryProperties(g_phys, &g_memprops);
    size_t src_row_bytes = (size_t)(((unsigned)w + 3u) >> 2) * 4u;
    VBuf src_ref, src_dis, dwt_tmp_ref, dwt_tmp_dis, ref_band, dis_band, csf_f, div_lookup;
    VBuf accum[ADM_NUM_SCALES];
    vbuf_create((VkDeviceSize)src_row_bytes * (size_t)h, &src_ref);
    vbuf_create((VkDeviceSize)src_row_bytes * (size_t)h, &src_dis);
    vbuf_create((VkDeviceSize)w * 2 * half_h0 * 4, &dwt_tmp_ref);
    vbuf_create((VkDeviceSize)w * 2 * half_h0 * 4, &dwt_tmp_dis);
    vbuf_create((VkDeviceSize)4 * buf_stride * half_h0 * 4, &ref_band);
    vbuf_create((VkDeviceSize)4 * buf_stride * half_h0 * 4, &dis_band);
    vbuf_create((VkDeviceSize)3 * buf_stride * half_h0 * 4, &csf_f);
    vbuf_create((VkDeviceSize)ADM_DIV_LOOKUP_SIZE * 4, &div_lookup);
    {
        int32_t *lut = (int32_t *)div_lookup.mapped;
        memset(lut, 0, ADM_DIV_LOOKUP_SIZE * 4);
        const int32_t Q = 1073741824; /* 2^30, CPU integer_adm.h:12 */
        for (int i = 1; i <= 32768; i++) {
            int32_t recip = (int32_t)(Q / i);
            lut[32768 + i] = recip;
            lut[32768 - i] = 0 - recip;
        }
    }
    unsigned wg_count[ADM_NUM_SCALES];
    for (int s = 0; s < ADM_NUM_SCALES; s++) {
        int top = (int)((double)scale_hh[s] * ADM_BORDER_FACTOR - 0.5);
        if (top < 0) top = 0;
        int bottom = (int)scale_hh[s] - top;
        unsigned num_rows = (unsigned)(bottom - top);
        if (num_rows == 0) num_rows = 1;
        wg_count[s] = 3u * num_rows;
        vbuf_create((VkDeviceSize)wg_count[s] * 6 * 8, &accum[s]);
    }

    /* ---- descriptors ---- */
    VkDescriptorSetLayoutBinding bindings[9];
    for (int b = 0; b < 9; b++) {
        memset(&bindings[b], 0, sizeof(bindings[b]));
        bindings[b].binding = (uint32_t)b;
        bindings[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[b].descriptorCount = 1;
        bindings[b].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dlci; memset(&dlci, 0, sizeof(dlci));
    dlci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
    dlci.bindingCount = 9; dlci.pBindings = bindings;
    VkDescriptorSetLayout setLayout;
    if (vkCreateDescriptorSetLayout(g_device, &dlci, NULL, &setLayout) != VK_SUCCESS)
        die("dsl", 0);
    VkDescriptorPoolSize ps; memset(&ps, 0, sizeof(ps));
    ps.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; ps.descriptorCount = 9 * ADM_NUM_SCALES;
    VkDescriptorPoolCreateInfo pci; memset(&pci, 0, sizeof(pci));
    pci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
    pci.maxSets = ADM_NUM_SCALES; pci.poolSizeCount = 1; pci.pPoolSizes = &ps;
    VkDescriptorPool pool;
    if (vkCreateDescriptorPool(g_device, &pci, NULL, &pool) != VK_SUCCESS) die("pool", 0);
    VkDescriptorSet sets[ADM_NUM_SCALES];
    {
        VkDescriptorSetLayout *layouts = malloc(ADM_NUM_SCALES * sizeof(*layouts));
        for (int s = 0; s < ADM_NUM_SCALES; s++) layouts[s] = setLayout;
        VkDescriptorSetAllocateInfo sai; memset(&sai, 0, sizeof(sai));
        sai.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
        sai.descriptorPool = pool; sai.descriptorSetCount = ADM_NUM_SCALES; sai.pSetLayouts = layouts;
        if (vkAllocateDescriptorSets(g_device, &sai, sets) != VK_SUCCESS) die("alloc sets", 0);
        free(layouts);
    }
    for (int s = 0; s < ADM_NUM_SCALES; s++) {
        VkDescriptorBufferInfo dbi[9] = {
            {src_ref.buf, 0, VK_WHOLE_SIZE},       /* 0 ref_src */
            {dwt_tmp_ref.buf, 0, VK_WHOLE_SIZE},   /* 1 dwt_tmp_ref */
            {dwt_tmp_dis.buf, 0, VK_WHOLE_SIZE},   /* 2 dwt_tmp_dis */
            {ref_band.buf, 0, VK_WHOLE_SIZE},      /* 3 ref_band */
            {dis_band.buf, 0, VK_WHOLE_SIZE},      /* 4 dis_band */
            {csf_f.buf, 0, VK_WHOLE_SIZE},         /* 5 csf_f */
            {div_lookup.buf, 0, VK_WHOLE_SIZE},    /* 6 div_lookup */
            {accum[s].buf, 0, VK_WHOLE_SIZE},      /* 7 accum_out (per scale) */
            {src_dis.buf, 0, VK_WHOLE_SIZE},       /* 8 dis_src */
        };
        VkWriteDescriptorSet writes[9];
        for (int b = 0; b < 9; b++) {
            memset(&writes[b], 0, sizeof(writes[b]));
            writes[b].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[b].dstSet = sets[s]; writes[b].dstBinding = (uint32_t)b;
            writes[b].descriptorCount = 1;
            writes[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            writes[b].pBufferInfo = &dbi[b];
        }
        vkUpdateDescriptorSets(g_device, 9, writes, 0, NULL);
    }

    /* ---- pipeline layout + 16 pipelines ---- */
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, (uint32_t)sizeof(AdmPushConsts)};
    VkPipelineLayoutCreateInfo plci; memset(&plci, 0, sizeof(plci));
    plci.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
    plci.setLayoutCount = 1; plci.pSetLayouts = &setLayout;
    plci.pushConstantRangeCount = 1; plci.pPushConstantRanges = &pcr;
    VkPipelineLayout pipeLayout;
    if (vkCreatePipelineLayout(g_device, &plci, NULL, &pipeLayout) != VK_SUCCESS)
        die("pipe layout", 0);

    FILE *sf = fopen(spv_path, "rb");
    if (!sf) { fprintf(stderr, "cannot open %s\n", spv_path); return 2; }
    fseek(sf, 0, SEEK_END); long sz = ftell(sf); fseek(sf, 0, SEEK_SET);
    uint32_t *code = malloc((size_t)sz);
    if (fread(code, 1, (size_t)sz, sf) != (size_t)sz) die("short spv read", 0);
    fclose(sf);
    VkShaderModuleCreateInfo smci; memset(&smci, 0, sizeof(smci));
    smci.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    smci.codeSize = (size_t)sz; smci.pCode = code;
    VkShaderModule module;
    if (vkCreateShaderModule(g_device, &smci, NULL, &module) != VK_SUCCESS) die("shader module", 0);

    VkPipeline pipelines[4][ADM_NUM_SCALES];
    for (int st = 0; st < 4; st++) {
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            struct { int32_t width, height, bpc, scale, stage; } spec = {
                w, h, 8, s, st};
            VkSpecializationMapEntry map[5];
            for (int e = 0; e < 5; e++) {
                map[e].constantID = (uint32_t)e;
                map[e].offset = (uint32_t)(e * 4);
                map[e].size = 4;
            }
            VkSpecializationInfo si; memset(&si, 0, sizeof(si));
            si.mapEntryCount = 5; si.pMapEntries = map;
            si.dataSize = sizeof(spec); si.pData = &spec;
            VkPipelineShaderStageCreateInfo stage; memset(&stage, 0, sizeof(stage));
            stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
            stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; stage.module = module;
            stage.pName = "main"; stage.pSpecializationInfo = &si;
            VkComputePipelineCreateInfo cpci; memset(&cpci, 0, sizeof(cpci));
            cpci.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
            cpci.stage = stage; cpci.layout = pipeLayout;
            if (vkCreateComputePipelines(g_device, NULL, 1, &cpci, NULL,
                                         &pipelines[st][s]) != VK_SUCCESS)
                die("pipeline create", 0);
        }
    }

    VkCommandPoolCreateInfo cpoi; memset(&cpoi, 0, sizeof(cpoi));
    cpoi.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    cpoi.queueFamilyIndex = g_compute_family;
    VkCommandPool cmdPool;
    if (vkCreateCommandPool(g_device, &cpoi, NULL, &cmdPool) != VK_SUCCESS) die("cmd pool", 0);
    VkCommandBufferAllocateInfo cbai; memset(&cbai, 0, sizeof(cbai));
    cbai.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    cbai.commandPool = cmdPool; cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    if (vkAllocateCommandBuffers(g_device, &cbai, &cmd) != VK_SUCCESS) die("cmd alloc", 0);
    VkFenceCreateInfo fci; memset(&fci, 0, sizeof(fci));
    VkFence fence;
    if (vkCreateFence(g_device, &fci, NULL, &fence) != VK_SUCCESS) die("fence", 0);

    /* ---- fixture ---- */
    size_t frame_bytes = (size_t)w * h + 2u * (size_t)(w / 2) * (h / 2); /* yuv420p8 */
    uint8_t *frame_buf = malloc(frame_bytes);
    FILE *fr = fopen(ref_path, "rb");
    FILE *fd = fopen(dis_path, "rb");
    if (!fr || !fd) die("cannot open fixture", 0);

    FILE *out = fopen(out_path, "w");
    if (!out) die("cannot open out", 0);
    FILE *dump = NULL;
    if (dump_path) dump = fopen(dump_path, "w");
    fprintf(out, "# tool=vk-adm device=\"%s\" vendor=0x%04x deviceid=0x%04x fixture=%s frames=%d w=%d h=%d\n",
            props.deviceName, props.vendorID, props.deviceID,
            fixture_name ? fixture_name : basename_of(ref_path), frames, w, h);
    fprintf(out, "# fields: frame s0num s0den s1num s1den s2num s2den s3num s3den num den adm2\n");

    double num_total_check = 0.0; /* unused placeholder to keep -Wall quiet */

    for (int fidx = 0; fidx < frames; fidx++) {
        if (fread(frame_buf, 1, frame_bytes, fr) != frame_bytes) die("short ref read", fidx);
        /* upload Y planes with the padded word stride the shader expects */
        uint8_t *refm = (uint8_t *)src_ref.mapped, *dism = (uint8_t *)src_dis.mapped;
        const uint8_t *refy = frame_buf;
        memset(refm, 0, (size_t)src_row_bytes * (size_t)h);
        for (int y = 0; y < h; y++)
            memcpy(refm + (size_t)y * src_row_bytes, refy + (size_t)y * w, (size_t)w);
        if (fread(frame_buf, 1, frame_bytes, fd) != frame_bytes) die("short dis read", fidx);
        memset(dism, 0, (size_t)src_row_bytes * (size_t)h);
        for (int y = 0; y < h; y++)
            memcpy(dism + (size_t)y * src_row_bytes, frame_buf + (size_t)y * w, (size_t)w);
        for (int s = 0; s < ADM_NUM_SCALES; s++)
            memset(accum[s].mapped, 0, (size_t)wg_count[s] * 6 * 8);

        VkCommandBufferBeginInfo bbi; memset(&bbi, 0, sizeof(bbi));
        bbi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        vkBeginCommandBuffer(cmd, &bbi);
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            unsigned cw = scale_w[s], ch = scale_h[s];
            unsigned hw = scale_hw[s], hh = scale_hh[s];
            uint32_t v_shift, v_add, h_shift;
            if (s == 0) { v_shift = 8; v_add = 128; h_shift = 16; }
            else if (s == 1) { v_shift = 0; v_add = 0; h_shift = 15; }
            else if (s == 2) { v_shift = 16; v_add = 32768; h_shift = 16; }
            else { v_shift = 16; v_add = 32768; h_shift = 15; }
            int left = (int)((double)hw * ADM_BORDER_FACTOR - 0.5);
            int top = (int)((double)hh * ADM_BORDER_FACTOR - 0.5);
            if (left < 0) left = 0;
            if (top < 0) top = 0;
            int right = (int)hw - left;
            int bottom = (int)hh - top;
            int active_w = right - left, active_h = bottom - top;
            unsigned in_stride = (s == 0) ? (unsigned)w : buf_stride;

            AdmPushConsts pc; memset(&pc, 0, sizeof(pc));
            pc.cur_w = cw; pc.cur_h = ch; pc.half_w = hw; pc.half_h = hh;
            pc.buf_stride = buf_stride; pc.in_stride = in_stride;
            pc.v_shift = v_shift; pc.v_add = v_add; pc.h_shift = h_shift;
            pc.i_rfactor_h = i_rfactor[s][0]; pc.i_rfactor_v = i_rfactor[s][1];
            pc.i_rfactor_d = i_rfactor[s][2];
            pc.num_workgroups_x = 1;
            pc.gain_hi = gq.gain_hi; pc.gain_lo = gq.gain_lo;
            pc.active_left = left; pc.active_top = top;
            pc.active_right = right; pc.active_bottom = bottom;
            compute_csf_cm_shifts(hw, hh, s, active_w, active_h, &pc);

            VkMemoryBarrier mb; memset(&mb, 0, sizeof(mb));
            mb.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
            mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
            mb.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;

            /* stage 0: DWT vertical, z=2 (ref+dis fused) */
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelines[0][s]);
            vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeLayout, 0, 1,
                                    &sets[s], 0, NULL);
            vkCmdPushConstants(cmd, pipeLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc), &pc);
            vkCmdDispatch(cmd, (cw + 15u) / 16u, (hh + 15u) / 16u, 2);
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
            /* stage 1: DWT horizontal, z=2 */
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelines[1][s]);
            vkCmdDispatch(cmd, (hw + 15u) / 16u, (hh + 15u) / 16u, 2);
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
            /* stage 2: decouple + CSF, writes csf_f */
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelines[2][s]);
            vkCmdDispatch(cmd, (hw + 15u) / 16u, (hh + 15u) / 16u, 1);
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
            /* stage 3: CSF den + CM fused, one WG per (band, active row) */
            unsigned num_rows = (unsigned)(active_h > 0 ? active_h : 1);
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelines[3][s]);
            vkCmdDispatch(cmd, 3u * num_rows, 1u, 1u);
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
        }
        vkEndCommandBuffer(cmd);
        vkResetFences(g_device, 1, &fence);
        VkSubmitInfo si; memset(&si, 0, sizeof(si));
        si.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
        if (vkQueueSubmit(queue, 1, &si, fence) != VK_SUCCESS) die("submit", fidx);
        r = vkWaitForFences(g_device, 1, &fence, VK_TRUE, 120ull * 1000ull * 1000ull * 1000ull);
        if (r != VK_SUCCESS) die("fence wait (120s)", (long)r);

        /* ---- host reduction of per-WG slots + CPU-exact conclude ---- */
        int64_t csf_tot[ADM_NUM_SCALES][3], cm_tot[ADM_NUM_SCALES][3];
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            const int64_t *acc = (const int64_t *)accum[s].mapped;
            for (int b = 0; b < 3; b++) {
                int64_t c = 0, m = 0;
                for (unsigned g = 0; g < wg_count[s]; g++) {
                    c += acc[g * 6 + b];
                    m += acc[g * 6 + 3 + b];
                }
                csf_tot[s][b] = c; cm_tot[s][b] = m;
            }
        }
        if (dump) {
            for (int s = 0; s < ADM_NUM_SCALES; s++)
                fprintf(dump, "frame=%d scale=%d csf=%lld,%lld,%lld cm=%lld,%lld,%lld\n", fidx, s,
                        (long long)csf_tot[s][0], (long long)csf_tot[s][1],
                        (long long)csf_tot[s][2], (long long)cm_tot[s][0],
                        (long long)cm_tot[s][1], (long long)cm_tot[s][2]);
            fflush(dump);
        }
        if (dump_csf_path && fidx == dump_csf_frame) {
            FILE *df = fopen(dump_csf_path, "w");
            if (df) {
                const int32_t *cf = (const int32_t *)csf_f.mapped;
                unsigned hh = scale_hh[dump_csf_scale], hw = scale_hw[dump_csf_scale];
                for (int band = 0; band < 3; band++) {
                    fprintf(df, "band %d\n", band);
                    for (unsigned y = 0; y < hh; y++) {
                        for (unsigned x = 0; x < hw; x++)
                            fprintf(df, "%d ",
                                    cf[band * (buf_stride * hh) + y * buf_stride + x]);
                        fprintf(df, "\n");
                    }
                }
                fclose(df);
            }
        }

        float num_scale[ADM_NUM_SCALES], den_scale[ADM_NUM_SCALES];
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            uint64_t cu[3];
            for (int b = 0; b < 3; b++) cu[b] = (uint64_t)csf_tot[s][b];
            conclude_csf_den_host(cu, (int)scale_hh[s], (int)scale_hw[s], s, &den_scale[s]);
            conclude_cm_host(cm_tot[s], (int)scale_hh[s], (int)scale_hw[s], s, &num_scale[s]);
        }

        /* CPU integer_compute_adm:2878-3022: float per-scale values summed
         * into doubles in scale order; numden_limit uses the BASE frame. */
        double num = 0.0, den = 0.0;
        for (int s = 0; s < ADM_NUM_SCALES; s++) {
            num += num_scale[s];
            den += den_scale[s];
        }
        double numden_limit = 1e-10 * (double)((long long)w * h) / (1920.0 * 1080.0);
        if (num < numden_limit) num = 0.0;
        if (den < numden_limit) den = 0.0;
        double adm2 = (den == 0.0) ? 1.0 : num / den;

        fprintf(out,
                "frame=%d s0num=%a s0den=%a s1num=%a s1den=%a s2num=%a s2den=%a s3num=%a s3den=%a "
                "num=%a den=%a adm2=%a | s0num=%.9g s0den=%.9g s1num=%.9g s1den=%.9g s2num=%.9g "
                "s2den=%.9g s3num=%.9g s3den=%.9g num=%.9g den=%.9g adm2=%.9g\n",
                fidx, (double)num_scale[0], (double)den_scale[0], (double)num_scale[1],
                (double)den_scale[1], (double)num_scale[2], (double)den_scale[2],
                (double)num_scale[3], (double)den_scale[3], num, den, adm2,
                num_scale[0], den_scale[0], num_scale[1], den_scale[1], num_scale[2],
                den_scale[2], num_scale[3], den_scale[3], num, den, adm2);
        fflush(out);
        num_total_check += 0.0;
    }

    fprintf(out, "# done\n");
    fclose(out);
    if (dump) fclose(dump);
    fclose(fr); fclose(fd);
    printf("wrote %s%s%s\n", out_path, dump ? " + " : "", dump ? dump_path : "");

    vkDeviceWaitIdle(g_device);
    for (int s = 0; s < ADM_NUM_SCALES; s++) vbuf_destroy(&accum[s]);
    vbuf_destroy(&div_lookup); vbuf_destroy(&csf_f); vbuf_destroy(&dis_band);
    vbuf_destroy(&ref_band); vbuf_destroy(&dwt_tmp_dis); vbuf_destroy(&dwt_tmp_ref);
    vbuf_destroy(&src_dis); vbuf_destroy(&src_ref);
    for (int st = 0; st < 4; st++)
        for (int s = 0; s < ADM_NUM_SCALES; s++) vkDestroyPipeline(g_device, pipelines[st][s], NULL);
    vkDestroyShaderModule(g_device, module, NULL);
    vkDestroyPipelineLayout(g_device, pipeLayout, NULL);
    vkDestroyDescriptorPool(g_device, pool, NULL);
    vkDestroyDescriptorSetLayout(g_device, setLayout, NULL);
    vkDestroyCommandPool(g_device, cmdPool, NULL);
    vkDestroyFence(g_device, fence, NULL);
    vkDestroyDevice(g_device, NULL);
    free(frame_buf); free(code); free(qp); free(devs);
    (void)num_total_check;
    return 0;
}
