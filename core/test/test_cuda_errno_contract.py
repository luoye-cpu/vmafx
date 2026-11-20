#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Keep CUDA errno mapping from overriding explicit VMAFx statuses."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUDA = ROOT / "core" / "src" / "cuda"
FFMPEG_FILTER = ROOT / "ffmpeg-patches" / "src" / "vf_vmafx.c"


def compact(path: Path) -> str:
    """Collapse whitespace so source contracts do not depend on formatting."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


class CudaErrnoContract(unittest.TestCase):
    """CUDA failures keep error status semantics at the FFmpeg boundary."""

    def test_unsupported_imports_do_not_publish_cuda_errno(self) -> None:
        gl = compact(CUDA / "import_gl.c")
        vulkan = compact(CUDA / "import_vulkan.c")
        self.assertIn(
            "VMAFX_E_INVALID : VMAFX_E_NOTSUP, 0, VMAFX_SUBJECT_PLANE",
            gl,
        )
        self.assertIn("VMAFX_E_NOTSUP, 0, VMAFX_SUBJECT_FENCE", vulkan)
        self.assertIn("VMAFX_E_NOTSUP, 0, VMAFX_SUBJECT_PLANE", vulkan)

    def test_invalid_fence_status_is_not_replaced_by_cuda_errno(self) -> None:
        fence = compact(CUDA / "import_fence.c")
        destroy = fence.split("VmafxStatus vmafx_cuda_fence_destroy", maxsplit=1)[1]
        self.assertIn("VMAFX_E_INVALID, 0, VMAFX_SUBJECT_FENCE", destroy)

    def test_async_oom_uses_errno_and_ffmpeg_maps_to_enomem(self) -> None:
        frame = compact(CUDA / "import_frame.c")
        adapter = compact(FFMPEG_FILTER)
        self.assertIn(
            "cf->dev->state.f->cuMemAllocAsync(&cf->owned, plan->total, cf->dev->state.str)",
            frame,
        )
        self.assertIn(
            "VMAFX_E_NOMEM : VMAFX_E_DEVICE, vmaf_cuda_result_to_errno((int)res)",
            frame,
        )
        self.assertIn('"%d)", plan->total, (int)res)', frame)
        self.assertIn("if (err < 0) return err;", adapter)
        self.assertIn("case VMAFX_E_NOMEM: return AVERROR(ENOMEM);", adapter)

    def test_event_query_uses_errno_mapping(self) -> None:
        fence = compact(CUDA / "import_fence.c")
        self.assertIn(
            "res == CUDA_ERROR_NOT_READY ? 0 : vmaf_cuda_result_to_errno((int)res)",
            fence,
        )


if __name__ == "__main__":
    unittest.main()
