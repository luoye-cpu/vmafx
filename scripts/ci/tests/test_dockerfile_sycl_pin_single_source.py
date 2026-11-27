#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""The root Dockerfile's optional SYCL layer installs at DOCKERFILE_SYCL_ONEAPI_VERSION.

The layer used to repeat the 2025.3 release in two inline package names, the
last oneAPI pin build-config.env did not own (audit C1-002, #3090). It now
copies and sources build-config.env inside its RUN and installs at
"${DOCKERFILE_SYCL_ONEAPI_VERSION}" -- the dev/Containerfile Level Zero
pattern, no ARG mirror to rot. The release itself stays frozen at 2025.3 on
purpose: the layer pairs the compiler with the legacy
`intel-oneapi-runtime-libs` metapackage and nothing in CI builds the image,
so it deliberately does not follow ONEAPI_VERSION.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "workflow_versions", ROOT / "scripts/ci/check-workflow-versions.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DockerfileSyclPinGate(Protocol):
    def check_dockerfile_sycl_pin(self, root: Path, want: str | None) -> list[str]: ...

    def load_config(self, root: Path) -> dict[str, str]: ...


GATE = cast(DockerfileSyclPinGate, MODULE)
WANT = "2025.3"

DOCKERFILE = """FROM ubuntu:26.04
ARG ENABLE_SYCL=false

# ---------- Intel oneAPI (SYCL, optional) ----------
# hadolint ignore=DL3008,DL3009
COPY build-config.env /tmp/build-config.env
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \\
    if [ "$ENABLE_SYCL" = "true" ]; then \\
        . /tmp/build-config.env && \\
        apt-get update && apt-get install -y --no-install-recommends \\
            "intel-oneapi-compiler-dpcpp-cpp-${DOCKERFILE_SYCL_ONEAPI_VERSION}" \\
            "intel-oneapi-runtime-libs-${DOCKERFILE_SYCL_ONEAPI_VERSION}" ; \\
    fi && \\
    rm -f /tmp/build-config.env

# ---------- nv-codec-headers ----------
WORKDIR /tmp/nv-codec
"""


class DockerfileSyclPinSingleSource(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.write(DOCKERFILE)

    def write(self, text: str) -> None:
        (self.repo / "Dockerfile").write_text(text, encoding="utf-8")

    def test_sourced_pin_passes(self) -> None:
        self.assertEqual(GATE.check_dockerfile_sycl_pin(self.repo, WANT), [])

    def test_repository_dockerfile_matches_config(self) -> None:
        want = GATE.load_config(ROOT).get("DOCKERFILE_SYCL_ONEAPI_VERSION")
        self.assertEqual(GATE.check_dockerfile_sycl_pin(ROOT, want), [])

    def test_missing_config_value_fails(self) -> None:
        problems = GATE.check_dockerfile_sycl_pin(self.repo, None)
        self.assertEqual(len(problems), 1)
        self.assertIn("must define DOCKERFILE_SYCL_ONEAPI_VERSION", problems[0])

    def test_literal_pin_fails(self) -> None:
        self.write(
            DOCKERFILE.replace(
                '"intel-oneapi-compiler-dpcpp-cpp-${DOCKERFILE_SYCL_ONEAPI_VERSION}"',
                "intel-oneapi-compiler-dpcpp-cpp-2025.3",
            ).replace(
                '"intel-oneapi-runtime-libs-${DOCKERFILE_SYCL_ONEAPI_VERSION}"',
                "intel-oneapi-runtime-libs-2025.3",
            )
        )
        problems = GATE.check_dockerfile_sycl_pin(self.repo, WANT)
        self.assertEqual(len(problems), 3, problems)
        literals = [p for p in problems if "pins an intel-oneapi compiler/runtime literally" in p]
        self.assertEqual(len(literals), 2, problems)

    def test_unsourced_layer_fails(self) -> None:
        self.write(DOCKERFILE.replace(". /tmp/build-config.env && \\\n        ", ""))
        problems = GATE.check_dockerfile_sycl_pin(self.repo, WANT)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("must COPY build-config.env and source it", problems[0])

    def test_hardcoded_variable_name_fails(self) -> None:
        self.write(DOCKERFILE.replace("${DOCKERFILE_SYCL_ONEAPI_VERSION}", "2025.3"))
        problems = GATE.check_dockerfile_sycl_pin(self.repo, WANT)
        self.assertEqual(len(problems), 3, problems)

    def test_a_dockerfile_without_the_layer_is_not_a_finding(self) -> None:
        # The pin is inert without the layer, and the single-source fixtures
        # carry root Dockerfiles that are not this image.
        self.write("FROM ubuntu:26.04\nARG ENABLE_SYCL=false\n")
        self.assertEqual(GATE.check_dockerfile_sycl_pin(self.repo, WANT), [])


if __name__ == "__main__":
    unittest.main()
