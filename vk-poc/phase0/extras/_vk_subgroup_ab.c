/* _vk_subgroup_ab.c — subgroupAdd(int64) vs butterfly A/B host (scratch).
 * Pattern lifted from ../vk-poc Phase 0/1 probes: dynlink vulkan-1.dll,
 * VK_NO_PROTOTYPES, Vulkan-Headers on the include path.
 *
 * Per device:
 *   - print subgroupSize / supportedOperations / extended-types extension
 *   - dispatch mode 0 (subgroupAdd(int64)) and mode 1 (butterfly), count lanes
 *   - every lane must equal the CPU golden (all subgroups sum the same
 *     SubgroupInvocationID-derived values)
 *
 * Usage: _vk_subgroup_ab.exe --device-id 0x2c02 | --device N
 * Exit 0 = all pass, 1 = mismatch, 2 = fatal.
 */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static HMODULE g_vk;
static PFN_vkGetInstanceProcAddr g_gipa;
static VkInstance g_instance;
static VkPhysicalDevice g_phys;
static VkDevice g_device;
static uint32_t g_compute_family;

static PFN_vkCreateInstance pfn_vkCreateInstance;
static PFN_vkEnumeratePhysicalDevices pfn_vkEnumeratePhysicalDevices;
static PFN_vkGetPhysicalDeviceProperties2 pfn_vkGetPhysicalDeviceProperties2;
static PFN_vkGetPhysicalDeviceQueueFamilyProperties pfn_vkGetPhysicalDeviceQueueFamilyProperties;
static PFN_vkGetPhysicalDeviceMemoryProperties pfn_vkGetPhysicalDeviceMemoryProperties;
static PFN_vkEnumerateDeviceExtensionProperties pfn_vkEnumerateDeviceExtensionProperties;
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
static PFN_vkCreateDescriptorPool pfn_vkCreateDescriptorPool;
static PFN_vkAllocateDescriptorSets pfn_vkAllocateDescriptorSets;
static PFN_vkUpdateDescriptorSets pfn_vkUpdateDescriptorSets;
static PFN_vkCreatePipelineLayout pfn_vkCreatePipelineLayout;
static PFN_vkCreateShaderModule pfn_vkCreateShaderModule;
static PFN_vkCreateComputePipelines pfn_vkCreateComputePipelines;
static PFN_vkCreateCommandPool pfn_vkCreateCommandPool;
static PFN_vkAllocateCommandBuffers pfn_vkAllocateCommandBuffers;
static PFN_vkBeginCommandBuffer pfn_vkBeginCommandBuffer;
static PFN_vkEndCommandBuffer pfn_vkEndCommandBuffer;
static PFN_vkCmdBindPipeline pfn_vkCmdBindPipeline;
static PFN_vkCmdBindDescriptorSets pfn_vkCmdBindDescriptorSets;
static PFN_vkCmdPushConstants pfn_vkCmdPushConstants;
static PFN_vkCmdDispatch pfn_vkCmdDispatch;
static PFN_vkCreateFence pfn_vkCreateFence;
static PFN_vkResetFences pfn_vkResetFences;
static PFN_vkQueueSubmit pfn_vkQueueSubmit;
static PFN_vkWaitForFences pfn_vkWaitForFences;
static PFN_vkDestroyFence pfn_vkDestroyFence;
static PFN_vkDestroyDevice pfn_vkDestroyDevice;

#define V(name) \
    pfn_##name = (PFN_##name)(uintptr_t)g_gipa(g_instance, #name); \
    if (!pfn_##name) die("entry point missing: " #name)

static void die(const char *msg)
{
    fprintf(stderr, "FATAL: %s\n", msg);
    exit(2);
}

/* with VK_NO_PROTOTYPES the call sites resolve to the pointers */
#define vkCreateInstance pfn_vkCreateInstance
#define vkEnumeratePhysicalDevices pfn_vkEnumeratePhysicalDevices
#define vkGetPhysicalDeviceProperties2 pfn_vkGetPhysicalDeviceProperties2
#define vkGetPhysicalDeviceQueueFamilyProperties pfn_vkGetPhysicalDeviceQueueFamilyProperties
#define vkGetPhysicalDeviceMemoryProperties pfn_vkGetPhysicalDeviceMemoryProperties
#define vkEnumerateDeviceExtensionProperties pfn_vkEnumerateDeviceExtensionProperties
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
#define vkCreateDescriptorPool pfn_vkCreateDescriptorPool
#define vkAllocateDescriptorSets pfn_vkAllocateDescriptorSets
#define vkUpdateDescriptorSets pfn_vkUpdateDescriptorSets
#define vkCreatePipelineLayout pfn_vkCreatePipelineLayout
#define vkCreateShaderModule pfn_vkCreateShaderModule
#define vkCreateComputePipelines pfn_vkCreateComputePipelines
#define vkCreateCommandPool pfn_vkCreateCommandPool
#define vkAllocateCommandBuffers pfn_vkAllocateCommandBuffers
#define vkBeginCommandBuffer pfn_vkBeginCommandBuffer
#define vkEndCommandBuffer pfn_vkEndCommandBuffer
#define vkCmdBindPipeline pfn_vkCmdBindPipeline
#define vkCmdBindDescriptorSets pfn_vkCmdBindDescriptorSets
#define vkCmdPushConstants pfn_vkCmdPushConstants
#define vkCmdDispatch pfn_vkCmdDispatch
#define vkCreateFence pfn_vkCreateFence
#define vkResetFences pfn_vkResetFences
#define vkQueueSubmit pfn_vkQueueSubmit
#define vkWaitForFences pfn_vkWaitForFences
#define vkDestroyFence pfn_vkDestroyFence
#define vkDestroyDevice pfn_vkDestroyDevice

typedef struct {
    VkBuffer buf; VkDeviceMemory mem; void *mapped; VkDeviceSize size;
} VBuf;

static VkPhysicalDeviceMemoryProperties g_memprops;

static void vbuf_create(VkDeviceSize size, VBuf *b)
{
    VkBufferCreateInfo bci; memset(&bci, 0, sizeof(bci));
    bci.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
    bci.size = size;
    bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    if (vkCreateBuffer(g_device, &bci, NULL, &b->buf) != VK_SUCCESS) die("vkCreateBuffer");
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
    if (mtype == 0xffffffff) die("no host-visible coherent memory type");
    VkMemoryAllocateInfo mai; memset(&mai, 0, sizeof(mai));
    mai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    mai.allocationSize = mr.size; mai.memoryTypeIndex = mtype;
    if (vkAllocateMemory(g_device, &mai, NULL, &b->mem) != VK_SUCCESS) die("vkAllocateMemory");
    if (vkBindBufferMemory(g_device, b->buf, b->mem, 0) != VK_SUCCESS) die("vkBindBufferMemory");
    if (vkMapMemory(g_device, b->mem, 0, mr.size, 0, &b->mapped) != VK_SUCCESS) die("vkMapMemory");
    b->size = mr.size;
    memset(b->mapped, 0, (size_t)mr.size);
}

static void vbuf_destroy2(VBuf *b)
{
    if (b->mem) vkFreeMemory(g_device, b->mem, NULL);
    if (b->buf) vkDestroyBuffer(g_device, b->buf, NULL);
    memset(b, 0, sizeof(*b));
}

/* CPU golden: identical arithmetic to lane_val() in the shader. Unsigned
 * wrapping is defined in both languages; the int64 bit pattern is preserved. */
static uint64_t lane_val_bits(uint32_t l)
{
    uint64_t a = 0x9E3779B97F4A7C15ULL * ((uint64_t)l + 1ULL);
    uint64_t b = 0xD1B54A32D192EB03ULL * ((uint64_t)l * 3ULL + 7ULL);
    return a + b;
}

static uint64_t golden_sum(uint32_t subgroup_size)
{
    uint64_t acc = 0;
    for (uint32_t l = 0; l < subgroup_size; l++) acc += lane_val_bits(l);
    return acc;
}

static int run_mode(uint32_t mode, uint32_t count, uint32_t salt, uint64_t golden,
                    VkPipelineLayout layout, VkPipeline pipe, VkDescriptorSet set,
                    VkQueue queue, VkCommandBuffer cmd, VkFence fence,
                    VBuf *out0, VBuf *out1)
{
    VkCommandBufferBeginInfo bbi; memset(&bbi, 0, sizeof(bbi));
    bbi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    vkBeginCommandBuffer(cmd, &bbi);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
    uint32_t push[4] = {3u, count, mode, salt};
    vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, 16, push);
    vkCmdDispatch(cmd, (count + 63u) / 64u, 1, 1);
    vkEndCommandBuffer(cmd);
    vkResetFences(g_device, 1, &fence);
    VkSubmitInfo si; memset(&si, 0, sizeof(si));
    si.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
    if (vkQueueSubmit(queue, 1, &si, fence) != VK_SUCCESS) die("submit");
    VkResult r = vkWaitForFences(g_device, 1, &fence, VK_TRUE, 30ull * 1000ull * 1000ull * 1000ull);
    if (r != VK_SUCCESS) { fprintf(stderr, "mode %u: fence wait failed (%d)\n", mode, (int)r); return -1; }

    uint32_t exp_lo = (uint32_t)(golden & 0xFFFFFFFFULL) ^ salt;
    uint32_t exp_hi = (uint32_t)(golden >> 32) ^ salt;
    uint32_t bad = 0, first_bad = 0;
    const uint32_t *o0 = (const uint32_t *)out0->mapped, *o1 = (const uint32_t *)out1->mapped;
    for (uint32_t i = 0; i < count; i++) {
        if (o0[i] != exp_lo || o1[i] != exp_hi) {
            if (!bad) first_bad = i;
            bad++;
        }
    }
    if (bad == 0) {
        printf("  mode %u (%s): PASS  all %u lanes == golden %016llx\n", mode,
               mode == 0u ? "subgroupAdd(int64)" : "butterfly shuffleXor", count,
               (unsigned long long)golden);
        return 0;
    }
    printf("  mode %u (%s): FAIL  %u/%u lanes wrong; first bad lane %u: got %08x,%08x expected %08x,%08x\n",
           mode, mode == 0u ? "subgroupAdd(int64)" : "butterfly shuffleXor", bad, count,
           first_bad, o0[first_bad], o1[first_bad], exp_lo, exp_hi);
    return 1;
}

int main(int argc, char **argv)
{
    int device_index = -1;
    uint32_t want_device_id = 0x2c02, want_vendor_id = 0x10de;
    uint32_t count = 1024, salt = 0x5A5A0000u;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--device") && i + 1 < argc) device_index = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--device-id") && i + 1 < argc)
            want_device_id = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = (uint32_t)atoi(argv[++i]);
        else { fprintf(stderr, "unknown arg %s\n", argv[i]); return 2; }
    }

    g_vk = LoadLibraryA("vulkan-1.dll");
    if (!g_vk) die("vulkan-1.dll not found");
    g_gipa = (PFN_vkGetInstanceProcAddr)(uintptr_t)GetProcAddress(g_vk, "vkGetInstanceProcAddr");
    if (!g_gipa) die("vkGetInstanceProcAddr missing");

    pfn_vkCreateInstance = (PFN_vkCreateInstance)(uintptr_t)g_gipa(NULL, "vkCreateInstance");
    if (!pfn_vkCreateInstance) die("vkCreateInstance missing");
    VkApplicationInfo app; memset(&app, 0, sizeof(app));
    app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app.pApplicationName = "vmafx-subgroup-ab";
    app.apiVersion = VK_MAKE_API_VERSION(0, 1, 3, 0);
    VkInstanceCreateInfo ici; memset(&ici, 0, sizeof(ici));
    ici.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    ici.pApplicationInfo = &app;
    if (vkCreateInstance(&ici, NULL, &g_instance) != VK_SUCCESS) die("vkCreateInstance 1.3");
    V(vkEnumeratePhysicalDevices); V(vkGetPhysicalDeviceProperties2);
    V(vkGetPhysicalDeviceQueueFamilyProperties); V(vkEnumerateDeviceExtensionProperties);
    V(vkGetPhysicalDeviceMemoryProperties);
    V(vkCreateDevice); V(vkGetDeviceQueue);
    V(vkCreateBuffer); V(vkGetBufferMemoryRequirements); V(vkAllocateMemory);
    V(vkBindBufferMemory); V(vkMapMemory); V(vkFreeMemory); V(vkDestroyBuffer);
    V(vkCreateDescriptorSetLayout); V(vkCreateDescriptorPool); V(vkAllocateDescriptorSets);
    V(vkUpdateDescriptorSets); V(vkCreatePipelineLayout); V(vkCreateShaderModule);
    V(vkCreateComputePipelines); V(vkCreateCommandPool); V(vkAllocateCommandBuffers);
    V(vkBeginCommandBuffer); V(vkEndCommandBuffer); V(vkCmdBindPipeline);
    V(vkCmdBindDescriptorSets); V(vkCmdPushConstants); V(vkCmdDispatch);
    V(vkCreateFence); V(vkResetFences); V(vkQueueSubmit); V(vkWaitForFences);
    V(vkDestroyFence); V(vkDestroyDevice);

    uint32_t np = 0;
    vkEnumeratePhysicalDevices(g_instance, &np, NULL);
    if (!np) die("no physical devices");
    VkPhysicalDevice *devs = malloc(np * sizeof(*devs));
    vkEnumeratePhysicalDevices(g_instance, &np, devs);
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties2 p2; memset(&p2, 0, sizeof(p2));
        p2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
        vkGetPhysicalDeviceProperties2(devs[i], &p2);
        printf("  [%u] %s vendor=0x%04x device=0x%04x api=%d.%d.%d\n", i,
               p2.properties.deviceName, p2.properties.vendorID, p2.properties.deviceID,
               VK_API_VERSION_MAJOR(p2.properties.apiVersion),
               VK_API_VERSION_MINOR(p2.properties.apiVersion),
               VK_API_VERSION_PATCH(p2.properties.apiVersion));
    }
    if (device_index >= 0 && (uint32_t)device_index < np) g_phys = devs[device_index];
    else {
        for (uint32_t i = 0; i < np; i++) {
            VkPhysicalDeviceProperties2 p2; memset(&p2, 0, sizeof(p2));
            p2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
            vkGetPhysicalDeviceProperties2(devs[i], &p2);
            if (p2.properties.vendorID == want_vendor_id && p2.properties.deviceID == want_device_id) {
                g_phys = devs[i]; break;
            }
        }
        if (!g_phys) die("requested device id not found");
    }

    /* properties2 chain: subgroup properties + print */
    VkPhysicalDeviceSubgroupProperties sgp; memset(&sgp, 0, sizeof(sgp));
    sgp.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES;
    VkPhysicalDeviceProperties2 p2; memset(&p2, 0, sizeof(p2));
    p2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
    p2.pNext = &sgp;
    vkGetPhysicalDeviceProperties2(g_phys, &p2);
    printf("selected: %s (vendor 0x%04x device 0x%04x) subgroupSize=%u subgroupOps=0x%x quadOps=0x%x\n",
           p2.properties.deviceName, p2.properties.vendorID, p2.properties.deviceID,
           sgp.subgroupSize, sgp.supportedOperations, sgp.quadOperationsInAllStages);
    if (!(sgp.supportedOperations & VK_SUBGROUP_FEATURE_ARITHMETIC_BIT))
        printf("  NOTE: ARITHMETIC bit not in supportedOperations\n");
    if (!(sgp.supportedOperations & VK_SUBGROUP_FEATURE_SHUFFLE_BIT))
        printf("  NOTE: SHUFFLE bit not in supportedOperations\n");

    /* extended-types extension presence */
    uint32_t next = 0;
    vkEnumerateDeviceExtensionProperties(g_phys, NULL, &next, NULL);
    VkExtensionProperties *exts = malloc(next ? next * sizeof(*exts) : 1);
    vkEnumerateDeviceExtensionProperties(g_phys, NULL, &next, exts);
    int have_ext_types = 0;
    for (uint32_t e = 0; e < next; e++)
        if (!strcmp(exts[e].extensionName, "VK_EXT_shader_subgroup_extended_types"))
            have_ext_types = 1;
    printf("VK_EXT_shader_subgroup_extended_types: %s\n", have_ext_types ? "present" : "not listed");
    if (!have_ext_types && VK_API_VERSION_MINOR(p2.properties.apiVersion) < 2)
        printf("  NOTE: int64 subgroupAdd outside guaranteed core behavior without it\n");
    else if (!have_ext_types)
        printf("  (promoted to core in Vulkan 1.2 — absence from a 1.2+ device's list is expected)\n");

    /* queue */
    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(g_phys, &nq, NULL);
    VkQueueFamilyProperties *qp = calloc(nq ? nq : 1, sizeof(*qp));
    vkGetPhysicalDeviceQueueFamilyProperties(g_phys, &nq, qp);
    for (uint32_t i = 0; i < nq; i++)
        if (qp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { g_compute_family = i; break; }
    if (!(qp[g_compute_family].queueFlags & VK_QUEUE_COMPUTE_BIT)) die("no compute queue");

    /* device: features2 + vulkan11 features (+ extended types) */
    VkPhysicalDeviceShaderSubgroupExtendedTypesFeatures etf;
    memset(&etf, 0, sizeof(etf));
    etf.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_SUBGROUP_EXTENDED_TYPES_FEATURES;
    /* Vulkan 1.1 subgroup ops have no feature bit — properties + capabilities
     * only; the sole related feature is extended types (KHR ext / 1.2 core). */
    VkPhysicalDeviceFeatures2 f2; memset(&f2, 0, sizeof(f2));
    f2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    f2.features.shaderInt64 = VK_TRUE;
    f2.pNext = have_ext_types ? &etf : NULL;
    etf.shaderSubgroupExtendedTypes = have_ext_types ? VK_TRUE : VK_FALSE;

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci; memset(&qci, 0, sizeof(qci));
    qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    qci.queueFamilyIndex = g_compute_family; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci; memset(&dci, 0, sizeof(dci));
    dci.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    dci.pNext = &f2;
    if (vkCreateDevice(g_phys, &dci, NULL, &g_device) != VK_SUCCESS) die("vkCreateDevice");
    VkQueue queue;
    vkGetDeviceQueue(g_device, g_compute_family, 0, &queue);
    vkGetPhysicalDeviceMemoryProperties(g_phys, &g_memprops);

    /* buffers: out0/out1 host-visible; in0/in1 unused by op3 but bound */
    VBuf out0, out1, in0, in1;
    vbuf_create((VkDeviceSize)count * 4, &out0);
    vbuf_create((VkDeviceSize)count * 4, &out1);
    vbuf_create((VkDeviceSize)count * 4, &in0);
    vbuf_create((VkDeviceSize)count * 4, &in1);

    VkDescriptorSetLayoutBinding bindings[4];
    for (int b = 0; b < 4; b++) {
        memset(&bindings[b], 0, sizeof(bindings[b]));
        bindings[b].binding = (uint32_t)b;
        bindings[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[b].descriptorCount = 1;
        bindings[b].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dlci; memset(&dlci, 0, sizeof(dlci));
    dlci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
    dlci.bindingCount = 4; dlci.pBindings = bindings;
    VkDescriptorSetLayout setLayout;
    if (vkCreateDescriptorSetLayout(g_device, &dlci, NULL, &setLayout) != VK_SUCCESS) die("dsl");
    VkDescriptorPoolSize ps; memset(&ps, 0, sizeof(ps));
    ps.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; ps.descriptorCount = 4;
    VkDescriptorPoolCreateInfo pci; memset(&pci, 0, sizeof(pci));
    pci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
    pci.maxSets = 1; pci.poolSizeCount = 1; pci.pPoolSizes = &ps;
    VkDescriptorPool pool;
    if (vkCreateDescriptorPool(g_device, &pci, NULL, &pool) != VK_SUCCESS) die("pool");
    VkDescriptorSet set;
    VkDescriptorSetAllocateInfo sai; memset(&sai, 0, sizeof(sai));
    sai.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
    sai.descriptorPool = pool; sai.descriptorSetCount = 1; sai.pSetLayouts = &setLayout;
    if (vkAllocateDescriptorSets(g_device, &sai, &set) != VK_SUCCESS) die("alloc sets");
    {
        VkDescriptorBufferInfo dbi[4] = {
            {out0.buf, 0, VK_WHOLE_SIZE}, {out1.buf, 0, VK_WHOLE_SIZE},
            {in0.buf, 0, VK_WHOLE_SIZE}, {in1.buf, 0, VK_WHOLE_SIZE},
        };
        VkWriteDescriptorSet writes[4];
        for (int b = 0; b < 4; b++) {
            memset(&writes[b], 0, sizeof(writes[b]));
            writes[b].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[b].dstSet = set; writes[b].dstBinding = (uint32_t)b;
            writes[b].descriptorCount = 1;
            writes[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            writes[b].pBufferInfo = &dbi[b];
        }
        vkUpdateDescriptorSets(g_device, 4, writes, 0, NULL);
    }

    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 16};
    VkPipelineLayoutCreateInfo plci; memset(&plci, 0, sizeof(plci));
    plci.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
    plci.setLayoutCount = 1; plci.pSetLayouts = &setLayout;
    plci.pushConstantRangeCount = 1; plci.pPushConstantRanges = &pcr;
    VkPipelineLayout layout;
    if (vkCreatePipelineLayout(g_device, &plci, NULL, &layout) != VK_SUCCESS) die("layout");

    FILE *sf = fopen("_subgroup_ab.spv", "rb");
    if (!sf) die("cannot open _subgroup_ab.spv");
    fseek(sf, 0, SEEK_END); long sz = ftell(sf); fseek(sf, 0, SEEK_SET);
    uint32_t *code = malloc((size_t)sz);
    if (fread(code, 1, (size_t)sz, sf) != (size_t)sz) die("short spv read");
    fclose(sf);
    VkShaderModuleCreateInfo smci; memset(&smci, 0, sizeof(smci));
    smci.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    smci.codeSize = (size_t)sz; smci.pCode = code;
    VkShaderModule module;
    if (vkCreateShaderModule(g_device, &smci, NULL, &module) != VK_SUCCESS) die("shader module");
    VkPipelineShaderStageCreateInfo stage; memset(&stage, 0, sizeof(stage));
    stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; stage.module = module; stage.pName = "main";
    VkComputePipelineCreateInfo cpci; memset(&cpci, 0, sizeof(cpci));
    cpci.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
    cpci.stage = stage; cpci.layout = layout;
    VkPipeline pipe;
    if (vkCreateComputePipelines(g_device, NULL, 1, &cpci, NULL, &pipe) != VK_SUCCESS)
        die("pipeline create");

    VkCommandPoolCreateInfo cpoi; memset(&cpoi, 0, sizeof(cpoi));
    cpoi.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    cpoi.queueFamilyIndex = g_compute_family;
    VkCommandPool cmdPool;
    if (vkCreateCommandPool(g_device, &cpoi, NULL, &cmdPool) != VK_SUCCESS) die("cmd pool");
    VkCommandBufferAllocateInfo cbai; memset(&cbai, 0, sizeof(cbai));
    cbai.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    cbai.commandPool = cmdPool; cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    if (vkAllocateCommandBuffers(g_device, &cbai, &cmd) != VK_SUCCESS) die("cmd alloc");
    VkFenceCreateInfo fci; memset(&fci, 0, sizeof(fci));
    VkFence fence;
    if (vkCreateFence(g_device, &fci, NULL, &fence) != VK_SUCCESS) die("fence");

    uint64_t golden = golden_sum(sgp.subgroupSize);
    printf("golden (subgroupSize=%u): %016llx\n", sgp.subgroupSize, (unsigned long long)golden);

    int rc = 0;
    rc |= run_mode(0, count, salt, golden, layout, pipe, set, queue, cmd, fence, &out0, &out1);
    rc |= run_mode(1, count, salt, golden, layout, pipe, set, queue, cmd, fence, &out0, &out1);
    printf("verdict: %s\n", rc == 0 ? "BOTH MODES PASS" : (rc < 0 ? "EXECUTION ERROR" : "MISMATCH ABOVE"));

    /* scratch probe: skip per-object teardown (process exit reclaims);
     * only the device handle is destroyed for iGPU hygiene */
    vkDestroyDevice(g_device, NULL);
    free(code); free(qp); free(devs); free(exts);
    return rc > 0 ? 1 : (rc < 0 ? 2 : 0);
}
