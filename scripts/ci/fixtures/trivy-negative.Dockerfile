# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Planted defect for the Trivy image gate: 22.04 is still covered by the
# vulnerability database, and this two-year-old digest carries known
# HIGH/CRITICAL operating-system advisories. The docker-image workflow
# builds this image, requires `trivy image` to reject it, and only then scans
# the real image. Never use this as a runtime base.
FROM ubuntu:22.04@sha256:0bced47fffa3361afa981854fcabcd4577cd43cebbb808cea2b1f33a3dd7f508
# Non-root keeps this image's deliberate vulnerability separate from config lint.
USER 65534:65534
