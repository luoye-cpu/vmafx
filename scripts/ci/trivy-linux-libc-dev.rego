# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Trivy vulnerability ignore policy for linux-libc-dev (issue #2749).
#
# linux-libc-dev installs kernel UAPI headers only; it contains no kernel code
# and containers run on the host kernel. Trivy maps upstream Linux kernel CVEs
# onto this package, so every new kernel CVE batch breaks the image gate even
# though the finding is not reachable from this image. Drop those findings by
# package name instead of listing hundreds of CVE IDs in .trivyignore.yaml.
#
# This policy applies only to vulnerability findings. Misconfiguration, secret
# and license findings still follow .trivyignore.yaml and their own policies.
package trivy

import rego.v1

default ignore = false

ignore if {
	input.Type == "vulnerability"
	input.PkgName == "linux-libc-dev"
}
