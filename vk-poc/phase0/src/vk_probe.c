/* vk_probe.c — VMAFx VK PoC Phase 0: standalone device probe (Windows).
 *
 * No Vulkan SDK install: vulkan-1.dll is loaded dynamically and entry points
 * are resolved via vkGetInstanceProcAddr (the CUDA probe used the same dynlink
 * pattern for nvcuda.dll). Needs only the Khronos Vulkan-Headers (MIT) on the
 * include path; they are not vendored in this branch.
 *
 * Checks (one compute shader, op selected by push constant):
 *   op0  u32 x u32 -> u64 full product (umulExtended) vs host golden
 *   op1  u64 add-reduce chain with explicit carries vs host golden
 *   op2  fp32 contraction: a*b+c vs precise(a*b+c) vs host FMA / mul+add refs
 *   op3  dispatch sentinel: every invocation writes a distinct value
 *
 * Usage: vk_probe.exe [--device N] [--count N] [--spv path] [--out dir]
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

static HMODULE g_vk;
static PFN_vkGetInstanceProcAddr g_gipa;
static VkInstance g_instance;
static VkPhysicalDevice g_phys;
static VkDevice g_device;
static uint32_t g_compute_family;

#define LI(var, name) PFN_##name var = (PFN_##name)(uintptr_t)g_gipa(NULL, #name)
#define LD(var, name) PFN_##name var = (PFN_##name)(uintptr_t)g_gipa(g_instance, #name)

static void die(const char* msg, VkResult r)
{
    fprintf(stderr, "FATAL: %s (VkResult %d)\n", msg, (int)r);
    exit(2);
}

typedef struct { uint32_t lo, hi; } u64pair;

static u64pair host_umul32(uint32_t a, uint32_t b)
{
    uint64_t p = (uint64_t)a * (uint64_t)b;
    u64pair r = { (uint32_t)(p & 0xffffffffu), (uint32_t)(p >> 32) };
    return r;
}

static inline float u2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static inline uint32_t f2u(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

int main(int argc, char** argv)
{
    const char* spv_path = "build/probe.spv";
    const char* out_path = "reports/probe-report.txt";
    int device_index = -1;
    uint32_t count = 4096;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--device") && i + 1 < argc) device_index = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--spv") && i + 1 < argc) spv_path = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
    }

    g_vk = LoadLibraryA("vulkan-1.dll");
    if (!g_vk) { fprintf(stderr, "vulkan-1.dll not found\n"); return 2; }
    g_gipa = (PFN_vkGetInstanceProcAddr)(uintptr_t)GetProcAddress(g_vk, "vkGetInstanceProcAddr");
    if (!g_gipa) { fprintf(stderr, "vkGetInstanceProcAddr missing\n"); return 2; }

    LI(pfnEnumInstanceVersion, vkEnumerateInstanceVersion);
    uint32_t loader_api = 0;
    if (pfnEnumInstanceVersion) pfnEnumInstanceVersion(&loader_api);
    printf("loader API %u.%u.%u\n", loader_api >> 22, (loader_api >> 12) & 0x3ff, loader_api & 0xfff);

    LI(pfnCreateInstance, vkCreateInstance);
    VkApplicationInfo app; memset(&app, 0, sizeof(app));
    app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app.pApplicationName = "vmafx-vk-probe";
    app.applicationVersion = 1;
    VkInstanceCreateInfo ici; memset(&ici, 0, sizeof(ici));
    ici.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    ici.pApplicationInfo = &app;
    uint32_t want[] = { VK_MAKE_API_VERSION(0,1,3,0), VK_MAKE_API_VERSION(0,1,1,0), VK_MAKE_API_VERSION(0,1,0,0) };
    VkResult r = VK_ERROR_INCOMPATIBLE_DRIVER;
    for (int i = 0; i < 3; i++) {
        app.apiVersion = want[i];
        r = pfnCreateInstance(&ici, NULL, &g_instance);
        if (r == VK_SUCCESS) break;
    }
    if (r != VK_SUCCESS) die("vkCreateInstance", r);

    LD(pfnEnumPhys, vkEnumeratePhysicalDevices);
    LD(pfnGetProps, vkGetPhysicalDeviceProperties);
    LD(pfnGetFeatures2, vkGetPhysicalDeviceFeatures2);
    LD(pfnGetQueueProps, vkGetPhysicalDeviceQueueFamilyProperties);
    LD(pfnGetMemProps, vkGetPhysicalDeviceMemoryProperties);
    LD(pfnCreateDevice, vkCreateDevice);
    LD(pfnGetDeviceQueue, vkGetDeviceQueue);
    LD(pfnCreateBuffer, vkCreateBuffer);
    LD(pfnGetBufferMemReq, vkGetBufferMemoryRequirements);
    LD(pfnAllocateMemory, vkAllocateMemory);
    LD(pfnBindBufferMemory, vkBindBufferMemory);
    LD(pfnMapMemory, vkMapMemory);
    LD(pfnUnmapMemory, vkUnmapMemory);
    LD(pfnFreeMemory, vkFreeMemory);
    LD(pfnDestroyBuffer, vkDestroyBuffer);
    LD(pfnCreateDescSetLayout, vkCreateDescriptorSetLayout);
    LD(pfnCreateDescPool, vkCreateDescriptorPool);
    LD(pfnAllocDescSets, vkAllocateDescriptorSets);
    LD(pfnUpdateDescSets, vkUpdateDescriptorSets);
    LD(pfnCreatePipeLayout, vkCreatePipelineLayout);
    LD(pfnCreateShaderModule, vkCreateShaderModule);
    LD(pfnCreateComputePipelines, vkCreateComputePipelines);
    LD(pfnCreateCmdPool, vkCreateCommandPool);
    LD(pfnAllocCmdBuffers, vkAllocateCommandBuffers);
    LD(pfnBeginCmd, vkBeginCommandBuffer);
    LD(pfnEndCmd, vkEndCommandBuffer);
    LD(pfnCmdBindPipeline, vkCmdBindPipeline);
    LD(pfnCmdBindDescSets, vkCmdBindDescriptorSets);
    LD(pfnCmdPushConstants, vkCmdPushConstants);
    LD(pfnCmdDispatch, vkCmdDispatch);
    LD(pfnCreateFence, vkCreateFence);
    LD(pfnResetFences, vkResetFences);
    LD(pfnSubmit, vkQueueSubmit);
    LD(pfnWaitFences, vkWaitForFences);

    uint32_t np = 0;
    pfnEnumPhys(g_instance, &np, NULL);
    if (!np) { fprintf(stderr, "no Vulkan physical devices\n"); return 4; }
    VkPhysicalDevice* devs = malloc(np * sizeof(*devs));
    pfnEnumPhys(g_instance, &np, devs);
    printf("physical devices: %u\n", np);
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties p; pfnGetProps(devs[i], &p);
        printf("  [%u] %s vendor=0x%04x device=0x%04x type=%d api=%u.%u.%u driver=%u.%u.%u\n",
               i, p.deviceName, p.vendorID, p.deviceID, (int)p.deviceType,
               p.apiVersion >> 22, (p.apiVersion >> 12) & 0x3ff, p.apiVersion & 0xfff,
               p.driverVersion >> 22, (p.driverVersion >> 14) & 0x3ff, p.driverVersion & 0x3fff);
    }
    if (device_index >= 0 && (uint32_t)device_index < np) g_phys = devs[device_index];
    else {
        for (uint32_t i = 0; i < np; i++) {
            VkPhysicalDeviceProperties p; pfnGetProps(devs[i], &p);
            if (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU && p.vendorID == 0x10de) { g_phys = devs[i]; break; }
        }
        if (!g_phys) g_phys = devs[0];
    }
    VkPhysicalDeviceProperties props; pfnGetProps(g_phys, &props);
    printf("selected: %s (vendor 0x%04x device 0x%04x, type %d)\n",
           props.deviceName, props.vendorID, props.deviceID, (int)props.deviceType);

    VkPhysicalDeviceFeatures2 f2; memset(&f2, 0, sizeof(f2));
    f2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    pfnGetFeatures2(g_phys, &f2);
    printf("features: shaderFloat64=%d shaderInt64=%d\n",
           (int)f2.features.shaderFloat64, (int)f2.features.shaderInt64);

    uint32_t nq = 0; pfnGetQueueProps(g_phys, &nq, NULL);
    VkQueueFamilyProperties* qp = calloc(nq ? nq : 1, sizeof(*qp));
    pfnGetQueueProps(g_phys, &nq, qp);
    for (uint32_t i = 0; i < nq; i++)
        if (qp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { g_compute_family = i; break; }
    if (nq == 0 || !(qp[g_compute_family].queueFlags & VK_QUEUE_COMPUTE_BIT))
        { fprintf(stderr, "no compute queue\n"); return 4; }

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci; memset(&qci, 0, sizeof(qci));
    qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    qci.queueFamilyIndex = g_compute_family; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci; memset(&dci, 0, sizeof(dci));
    dci.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    if (pfnCreateDevice(g_phys, &dci, NULL, &g_device) != VK_SUCCESS) die("vkCreateDevice", r);
    printf("device created (compute family %u)\n", g_compute_family);
    free(qp); free(devs);

    VkQueue queue; pfnGetDeviceQueue(g_device, g_compute_family, 0, &queue);

    /* ---- resources: 4 storage buffers, host-visible ---- */
    enum { B_IN0, B_IN1, B_OUT0, B_OUT1, NBUF };
    VkBuffer bufs[NBUF]; VkDeviceMemory mems[NBUF]; uint32_t* mapped[NBUF];
    VkPhysicalDeviceMemoryProperties memprops; pfnGetMemProps(g_phys, &memprops);
    for (int b = 0; b < NBUF; b++) {
        VkBufferCreateInfo bci; memset(&bci, 0, sizeof(bci));
        bci.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
        bci.size = count * sizeof(uint32_t);
        bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
        bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        if (pfnCreateBuffer(g_device, &bci, NULL, &bufs[b]) != VK_SUCCESS) die("vkCreateBuffer", r);
        VkMemoryRequirements mr; pfnGetBufferMemReq(g_device, bufs[b], &mr);
        uint32_t mtype = 0xffffffff;
        for (uint32_t m = 0; m < memprops.memoryTypeCount; m++) {
            if ((mr.memoryTypeBits & (1u << m)) &&
                (memprops.memoryTypes[m].propertyFlags & (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))
                == (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) { mtype = m; break; }
        }
        if (mtype == 0xffffffff) { fprintf(stderr, "no host-visible coherent memory type\n"); return 4; }
        VkMemoryAllocateInfo mai; memset(&mai, 0, sizeof(mai));
        mai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
        mai.allocationSize = mr.size; mai.memoryTypeIndex = mtype;
        if (pfnAllocateMemory(g_device, &mai, NULL, &mems[b]) != VK_SUCCESS) die("vkAllocateMemory", r);
        pfnBindBufferMemory(g_device, bufs[b], mems[b], 0);
        pfnMapMemory(g_device, mems[b], 0, mr.size, 0, (void**)&mapped[b]);
    }

    /* ---- descriptors ---- */
    VkDescriptorSetLayoutBinding bindings[NBUF];
    for (int b = 0; b < NBUF; b++) {
        memset(&bindings[b], 0, sizeof(bindings[b]));
        bindings[b].binding = (uint32_t)b;
        bindings[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[b].descriptorCount = 1;
        bindings[b].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dlci; memset(&dlci, 0, sizeof(dlci));
    dlci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
    dlci.bindingCount = NBUF; dlci.pBindings = bindings;
    VkDescriptorSetLayout setLayout;
    if (pfnCreateDescSetLayout(g_device, &dlci, NULL, &setLayout) != VK_SUCCESS) die("dsl", r);
    VkDescriptorPoolSize ps; memset(&ps, 0, sizeof(ps));
    ps.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; ps.descriptorCount = NBUF;
    VkDescriptorPoolCreateInfo pci; memset(&pci, 0, sizeof(pci));
    pci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
    pci.maxSets = 1; pci.poolSizeCount = 1; pci.pPoolSizes = &ps;
    VkDescriptorPool pool;
    if (pfnCreateDescPool(g_device, &pci, NULL, &pool) != VK_SUCCESS) die("pool", r);
    VkDescriptorSetAllocateInfo sai; memset(&sai, 0, sizeof(sai));
    sai.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
    sai.descriptorPool = pool; sai.descriptorSetCount = 1; sai.pSetLayouts = &setLayout;
    VkDescriptorSet set;
    if (pfnAllocDescSets(g_device, &sai, &set) != VK_SUCCESS) die("alloc sets", r);
    VkWriteDescriptorSet writes[NBUF];
    VkDescriptorBufferInfo binfos[NBUF];
    for (int b = 0; b < NBUF; b++) {
        binfos[b].buffer = bufs[b]; binfos[b].offset = 0; binfos[b].range = count * sizeof(uint32_t);
        memset(&writes[b], 0, sizeof(writes[b]));
        writes[b].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[b].dstSet = set; writes[b].dstBinding = (uint32_t)b;
        writes[b].descriptorCount = 1; writes[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[b].pBufferInfo = &binfos[b];
    }
    pfnUpdateDescSets(g_device, 4, writes, 0, NULL);

    /* ---- pipeline ---- */
    VkPushConstantRange pcr = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 16 };
    VkPipelineLayoutCreateInfo plci; memset(&plci, 0, sizeof(plci));
    plci.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
    plci.setLayoutCount = 1; plci.pSetLayouts = &setLayout;
    plci.pushConstantRangeCount = 1; plci.pPushConstantRanges = &pcr;
    VkPipelineLayout pipeLayout;
    if (pfnCreatePipeLayout(g_device, &plci, NULL, &pipeLayout) != VK_SUCCESS) die("pipe layout", r);

    FILE* sf = fopen(spv_path, "rb");
    if (!sf) { fprintf(stderr, "cannot open %s\n", spv_path); return 2; }
    fseek(sf, 0, SEEK_END); long sz = ftell(sf); fseek(sf, 0, SEEK_SET);
    uint32_t* code = malloc((size_t)sz);
    if (fread(code, 1, (size_t)sz, sf) != (size_t)sz) { fprintf(stderr, "short spv read\n"); return 2; }
    fclose(sf);
    VkShaderModuleCreateInfo smci; memset(&smci, 0, sizeof(smci));
    smci.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    smci.codeSize = (size_t)sz; smci.pCode = code;
    VkShaderModule module;
    if (pfnCreateShaderModule(g_device, &smci, NULL, &module) != VK_SUCCESS) die("shader module", r);

    VkPipelineShaderStageCreateInfo stage; memset(&stage, 0, sizeof(stage));
    stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; stage.module = module; stage.pName = "main";
    VkComputePipelineCreateInfo cpci; memset(&cpci, 0, sizeof(cpci));
    cpci.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
    cpci.stage = stage; cpci.layout = pipeLayout;
    VkPipeline pipeline;
    if (pfnCreateComputePipelines(g_device, NULL, 1, &cpci, NULL, &pipeline) != VK_SUCCESS) die("pipeline", r);

    VkCommandPoolCreateInfo cpoi; memset(&cpoi, 0, sizeof(cpoi));
    cpoi.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    cpoi.queueFamilyIndex = g_compute_family;
    VkCommandPool cmdPool;
    if (pfnCreateCmdPool(g_device, &cpoi, NULL, &cmdPool) != VK_SUCCESS) die("cmd pool", r);
    VkCommandBufferAllocateInfo cbai; memset(&cbai, 0, sizeof(cbai));
    cbai.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    cbai.commandPool = cmdPool; cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    if (pfnAllocCmdBuffers(g_device, &cbai, &cmd) != VK_SUCCESS) die("cmd alloc", r);
    VkFenceCreateInfo fci; memset(&fci, 0, sizeof(fci));
    fci.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    VkFence fence;
    if (pfnCreateFence(g_device, &fci, NULL, &fence) != VK_SUCCESS) die("fence", r);

    /* ---- per-op run + verify ---- */
    struct { uint32_t op; const char* name; } ops[] = {
        {0, "umul32-product"}, {1, "u64-add-reduce"}, {2, "fma-contraction"}, {3, "dispatch-sentinel"},
    };
    uint32_t* in0 = malloc(count * 4); uint32_t* in1 = malloc(count * 4);
    /* deterministic operands with high bit patterns set */
    srand(0x564b5043u); /* "VKPC" */
    for (uint32_t i = 0; i < count; i++) {
        in0[i] = ((uint32_t)rand() << 17) ^ (uint32_t)rand() | 1u;
        in1[i] = ((uint32_t)rand() << 13) ^ (uint32_t)rand() | 1u;
    }

    int all_pass = 1;
    FILE* rep = fopen(out_path, "w");
    if (!rep) { fprintf(stderr, "cannot open %s\n", out_path); return 2; }
    fprintf(rep, "vmafx vk-probe report\ndevice: %s vendor=0x%04x device=0x%04x type=%d\n"
                 "api=%u.%u.%u driver=%u.%u.%u\nfeatures: shaderFloat64=%d shaderInt64=%d\ncount=%u\n",
            props.deviceName, props.vendorID, props.deviceID, (int)props.deviceType,
            props.apiVersion >> 22, (props.apiVersion >> 12) & 0x3ff, props.apiVersion & 0xfff,
            props.driverVersion >> 22, (props.driverVersion >> 14) & 0x3ff, props.driverVersion & 0x3fff,
            (int)f2.features.shaderFloat64, (int)f2.features.shaderInt64, count);
    printf("\n=== probe results ===\n");

    for (size_t oi = 0; oi < sizeof(ops) / sizeof(ops[0]); oi++) {
        uint32_t op = ops[oi].op;
        /* fill inputs */
        for (uint32_t i = 0; i < count; i++) { mapped[B_IN0][i] = in0[i]; mapped[B_IN1][i] = in1[i]; }
        /* record push constant */
        uint32_t pc[4] = { op, count, 0x12345678u ^ (op * 0x9e3779b9u), 0 };
        if (op == 2) {
            /* a*b+c where contraction matters: a, b with a mantissa-heavy product,
               c = 1.0f rounds the product's low bits away in mul+add but FMA keeps
               them; choose a, b from the deterministic stream, c = 0x3f800000 */
            pc[3] = 0x3f800000u;
        }
        VkCommandBufferBeginInfo bbi; memset(&bbi, 0, sizeof(bbi));
        bbi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        pfnBeginCmd(cmd, &bbi);
        pfnCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        pfnCmdBindDescSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeLayout, 0, 1, &set, 0, NULL);
        pfnCmdPushConstants(cmd, pipeLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, 16, pc);
        uint32_t groups = (op == 1u) ? 1u : count / 64u;
        pfnCmdDispatch(cmd, groups, 1, 1);
        pfnEndCmd(cmd);
        pfnResetFences(g_device, 1, &fence);
        VkSubmitInfo si; memset(&si, 0, sizeof(si));
        si.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
        if (pfnSubmit(queue, 1, &si, fence) != VK_SUCCESS) die("submit", r);
        if (pfnWaitFences(g_device, 1, &fence, VK_TRUE, 10ull * 1000ull * 1000ull * 1000ull) != VK_SUCCESS)
            die("fence wait (10s)", r);

        int pass = 1;
        fprintf(rep, "\n== op%u %s ==\n", op, ops[oi].name);
        if (op == 0) {
            for (uint32_t i = 0; i < count; i++) {
                u64pair g = host_umul32(in0[i], in1[i]);
                uint32_t lo = mapped[B_OUT0][i] ^ pc[2], hi = mapped[B_OUT1][i] ^ pc[2];
                if (lo != g.lo || hi != g.hi) {
                    fprintf(rep, "  MISMATCH at %u: a=%08x b=%08x got %08x:%08x want %08x:%08x\n",
                            i, in0[i], in1[i], hi, lo, g.hi, g.lo);
                    pass = 0;
                    break;
                }
            }
            printf("[op0 umul32-product]   %s\n", pass ? "PASS" : "FAIL");
        } else if (op == 1) {
            for (uint32_t i = 0; i < count / 64u; i++) {
                uint32_t base = i * 64u;
                uint32_t lo = in0[base], hi = in1[base];
                for (uint32_t k = 1u; k < 64u; k++) {
                    uint32_t nlo = in0[base + k], nhi = in1[base + k];
                    uint32_t t = lo + nlo;
                    uint32_t carry = (t < lo) ? 1u : 0u;
                    hi = hi + nhi + carry;
                    lo = t;
                }
                uint32_t glo = mapped[B_OUT0][i] ^ pc[2], ghi = mapped[B_OUT1][i] ^ pc[2];
                if (lo != glo || hi != ghi) {
                    fprintf(rep, "  MISMATCH at inv %u: got %08x:%08x want %08x:%08x\n", i, ghi, glo, hi, lo);
                    pass = 0;
                    break;
                }
            }
            printf("[op1 u64-add-reduce]   %s\n", pass ? "PASS" : "FAIL");
        } else if (op == 2) {
            float c = u2f(pc[3]);
            uint32_t sep_hits = 0, fma_hits = 0, edge = 0, other = 0, sep_violation = 0;
            for (uint32_t i = 0; i < count; i++) {
                float a = u2f(in0[i]), b = u2f(in1[i]);
                float ref_add = a * b + c;            /* host: two roundings (built -ffp-contract=off) */
                float ref_fma = fmaf(a, b, c);        /* host: fused */
                uint32_t got_c = mapped[B_OUT0][i] ^ pc[2];
                uint32_t got_s = mapped[B_OUT1][i] ^ pc[2];
                /* NaN payloads, denormal inputs (DAZ) and denormal outputs (FTZ)
                 * are handled differently per implementation; bucket them as
                 * edge behavior instead of failure. */
                int nonfinite = !isfinite(a) || !isfinite(b) || !isfinite(c) ||
                                !isfinite(ref_add) || !isfinite(ref_fma);
                int denormal_in = (a != 0.0f && fabsf(a) < 1.17549435e-38f) ||
                                  (b != 0.0f && fabsf(b) < 1.17549435e-38f) ||
                                  (c != 0.0f && fabsf(c) < 1.17549435e-38f);
                if (nonfinite || denormal_in) { edge++; continue; }
                if (got_c == f2u(ref_fma)) fma_hits++;
                else if (got_c == f2u(ref_add)) sep_hits++;
                else other++;
                if (got_s == f2u(ref_add)) sep_hits++;
                else if (got_s == f2u(ref_fma)) { fma_hits++; sep_violation++; }
                else other++;
                if (other && got_c != f2u(ref_fma) && got_c != f2u(ref_add) && other <= 5)
                    fprintf(rep, "  UNCLASSIFIED case %u: a=%g b=%g c=%g gotC=%08x gotS=%08x add=%08x fma=%08x\n",
                            i, a, b, c, got_c, got_s, f2u(ref_add), f2u(ref_fma));
            }
            fprintf(rep, "  contracted-pattern hits=%u separated-pattern hits=%u edge=%u other=%u precise-violations=%u (of %u)\n",
                    fma_hits, sep_hits, edge, other, sep_violation, count);
            /* separated (precise) must equal mul+add for every finite case:
             * that is NoContraction being honored; other>0 = unclassifiable. */
            pass = (other == 0 && sep_violation == 0);
            printf("[op2 fma-contraction]  %s (contracted=%u separated=%u edge=%u other=%u precise-viol=%u)\n",
                   pass ? "PASS" : "FAIL", fma_hits, sep_hits, edge, other, sep_violation);
        } else {
            for (uint32_t i = 0; i < count; i++) {
                if (mapped[B_OUT0][i] != ((i * 2654435761u) ^ pc[2]) ||
                    mapped[B_OUT1][i] != (0xDEADBEEFu ^ (i + pc[2]))) { pass = 0; break; }
            }
            printf("[op3 dispatch-sentinel] %s\n", pass ? "PASS" : "FAIL");
        }
        fprintf(rep, "  verdict: %s\n", pass ? "PASS" : "FAIL");
        all_pass = all_pass && pass;
    }

    fprintf(rep, "\noverall: %s\n", all_pass ? "PASS" : "FAIL");
    fclose(rep);
    printf("\noverall: %s -> %s\n", all_pass ? "PASS" : "FAIL", out_path);
    return all_pass ? 0 : 1;
}
