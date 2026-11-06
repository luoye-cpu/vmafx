# Phase 0 extras — sources and logs behind the local² rows on #2466

The `_`-prefixed files here are the probe sources and run logs for the rows
marked `local²` in the verdict table posted on
[VMAFx/vmafx#2466 (comment 6689054122)](https://github.com/VMAFx/vmafx/issues/2466#issuecomment-6689054122),
staged byte-identical from the local scratch directory they were produced in
(2026-11-06, Windows, one driver version per device).

| file | content | #2466 row it evidences |
|---|---|---|
| `_vk_subgroup_ab.c` | subgroupAdd(int64) A/B host: dynlink `vulkan-1.dll`, dispatches mode 0 (`subgroupAdd`) and mode 1 (butterfly `subgroupShuffleXor`), checks all 1024 lanes of every subgroup against the CPU golden | `subgroupAdd(int64_t)` A/B rows (all three devices) |
| `_subgroup_ab.comp` | the A/B compute shader: values depend only on `gl_SubgroupInvocationID`, so every subgroup sums to the same golden | `subgroupAdd(int64_t)` A/B rows |
| `_subgroup_ab_5080.txt` | RTX 5080 run: both modes PASS, golden `becb427010238f60`, 1024/1024 lanes | subgroupAdd row, RTX 5080 |
| `_subgroup_ab_4060.txt` | RTX 4060 Laptop run: both modes PASS, same golden | subgroupAdd row, RTX 4060 Laptop |
| `_subgroup_ab_intel.txt` | Intel RaptorLake-S iGPU run: both modes PASS (subgroupSize 32, subgroupOps 0x6ff) | subgroupAdd row, Intel RaptorLake-S iGPU |
| `_vk_adm_f64probe.c` | phase1 `vk_adm.c` edited to request `shaderFloat64=TRUE` at `vkCreateDevice` (scratch copy; stderr trace line added) | `vkCreateDevice` requesting `shaderFloat64=TRUE` row (Intel iGPU) |
| `_f64probe3.stderr.txt`, `_f64probe3.stderr2.txt` | the two SIGSEGV runs: only the `[trace] pre-vkCreateDevice: shaderFloat64 requested` line survives; no `VkResult`, no `FATAL`, buffered stdout rows lost | `vkCreateDevice` row — SIGSEGV inside `vkCreateDevice`, 2/2 runs |
| `_f64probe.txt` | stdout of an earlier `shaderFloat64`-request run: header only, frame rows lost with the process | `vkCreateDevice` row |
| `_f64probe3_ctrl.txt` + `_f64probe3_ctrl.stderr.txt` | control on the same host without the feature request: `FATAL: fence wait (120s) (-4)` at frame 0, stdout header only | integer ADM row, Intel iGPU (`VK_ERROR_DEVICE_LOST`, tree defect — reconfirmed) |
| `_intel-adm.txt` | stdout of the integer-ADM run (phase1 host as-is) on the Intel iGPU: header only — device lost at the first fence wait, no frame scored | integer ADM row, Intel iGPU (tree defect) |

Not staged: `_subgroup_ab.spv` (the host loads it from its working directory;
rebuild it from `_subgroup_ab.comp` with glslang — the phase0 README records
the tool and invocation) and all `*.exe` build artifacts.
