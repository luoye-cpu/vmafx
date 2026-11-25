# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/ci/ffmpeg-patches-surface-check.sh (Issue #2435, ADR-2166)."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no stubs in the hook env, as in test_clang_format_scope.py

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ci import extract_tree_blobs  # noqa: E402
from scripts.lib.safe_subprocess import TextCommandResult  # noqa: E402
from scripts.lib.safe_subprocess import run as run_command  # noqa: E402

SCRIPT = ROOT / "scripts" / "ci" / "ffmpeg-patches-surface-check.sh"
EXTRACTOR = ROOT / "scripts" / "ci" / "extract_tree_blobs.py"
RC_GATE_ERROR = 2
SMALL_ENTRY_BOUND = 3
HOOK_ID = "ffmpeg-patches-surface-docs"
SURFACE_ONLY_DONE_LINE = "--surface-only: tree checks passed"
GIT_TIMEOUT_S = 60
SCRIPT_TIMEOUT_S = 300
PROBE_SYMBOL = "vmaf_fixture_surface_probe"
PROBE_HEADER_REL = "core/include/libvmaf/fixture_probe.h"
PROBE_PATCH_REL = "ffmpeg-patches/0001-pass-autotune-cli-glue.patch"
PROBE_HEADER_WITH_SYM = (
    "#ifndef VMAF_FIXTURE_PROBE_H_\n"
    "#define VMAF_FIXTURE_PROBE_H_\n"
    f"int {PROBE_SYMBOL}(void);\n"
    "#endif\n"
)
PROBE_HEADER_WITHOUT_SYM = "#ifndef VMAF_FIXTURE_PROBE_H_\n#define VMAF_FIXTURE_PROBE_H_\n#endif\n"


def _clean_env(**extra: str) -> dict[str, str]:
    """Return the caller environment without GIT_* and with a fixture-only identity."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    env.update(extra)
    return env


def _tool(name: str) -> str:
    """Return the absolute path of a tool, or skip when it is absent."""
    path = shutil.which(name)
    if path is None:
        raise unittest.SkipTest(f"{name} is not on PATH")
    return path


def _git(repo: Path, *args: str) -> None:
    """Run git in a fixture repository with the clean environment."""
    git = _tool("git")
    run_command(
        [git, "-C", str(repo), *args],
        allowed_executables=[git],
        check=True,
        env=_clean_env(),
        capture_output=True,
        timeout_seconds=GIT_TIMEOUT_S,
    )


def _rev_parse(repo: Path, rev: str) -> str:
    """Resolve a git revision in a fixture repository."""
    git = _tool("git")
    res = run_command(
        [git, "-C", str(repo), "rev-parse", rev],
        allowed_executables=[git],
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout_seconds=GIT_TIMEOUT_S,
    )
    return res.stdout.strip()


def _run_script(script: Path, cwd: Path, *args: str, **extra: str) -> TextCommandResult:
    """Run the surface check script with bash, the only executable allowed."""
    bash = _tool("bash")
    return run_command(
        [bash, str(script), *args],
        allowed_executables=[bash],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(**extra),
        timeout_seconds=SCRIPT_TIMEOUT_S,
    )


FIXTURE_REPO_FILES = (
    "scripts/__init__.py",
    "scripts/lib/__init__.py",
    "scripts/lib/safe_subprocess.py",
    "scripts/lib/new_scanner_findings.py",
    "scripts/ci/extract_tree_blobs.py",
    "scripts/ci/pr-body-input.sh",
    "scripts/ci/pr-diff-base.sh",
    "Containerfile.vmafx",
    "Dockerfile",
    "Dockerfile.ffmpeg",
    "dev/Containerfile",
    "docker/Dockerfile.node",
    "scripts/test/sycl-dev-container.sh",
    ".github/workflows/ffmpeg-patch-stack.yml",
    "scripts/ci/tidy-integrations-build.sh",
    "README.md",
    "docs/reference/faq.md",
    "docs/adr/0000-template.md",
    "docs/usage/ffmpeg.md",
    "docs/usage/vmaf-tune-ffmpeg.md",
    "docs/usage/vmaf-tune-codec-adapters.md",
    "docs/usage/vmaf-vpl.md",
    "docs/development/sycl-zerocopy-testing.md",
    "docs/ai/inference.md",
    "docs/ai/model-registry.md",
    "docs/ai/roadmap.md",
    "docs/ai/models/fastdvdnet_pre.md",
    "docs/ai/models/learned_filter_v1.md",
    "docs/api/dnn.md",
    "docs/api/models-and-features.md",
)


class _FixtureTree:
    """Build the temporary fixture repository both test classes run the script in."""

    def _prepare_temp_worktree(self, temp_dir: Path, *, origin_master: bool = True) -> Path:
        """Stage a minimal fixture tree with the three surfaces and script."""
        shutil.copytree(ROOT / "ffmpeg-patches", temp_dir / "ffmpeg-patches")
        (temp_dir / "scripts" / "ci").mkdir(parents=True, exist_ok=True)
        shutil.copy(SCRIPT, temp_dir / "scripts" / "ci" / SCRIPT.name)
        for rel in FIXTURE_REPO_FILES:
            src = ROOT / rel
            if src.is_file():
                dst = temp_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)

        _git(temp_dir, "init", "-q", "-b", "master")
        _git(temp_dir, "add", ".")
        _git(temp_dir, "commit", "-q", "-m", "init")
        if origin_master:
            _git(temp_dir, "update-ref", "refs/remotes/origin/master", "HEAD")
        return temp_dir


class FfmpegPatchesSurfaceCheckTests(_FixtureTree, unittest.TestCase):
    """Verify surface invariant checks on series, build configs, and docs."""

    def test_positive_on_clean_tree(self) -> None:
        """On clean worktree, surface checks and public-surface gate pass."""
        res = _run_script(SCRIPT, ROOT)
        self.assertEqual(
            res.returncode,
            0,
            f"Expected exit 0 on clean tree, got {res.returncode}.\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
        )
        self.assertIn("series surface: zero vmaf-named filters/options", res.stdout)
        self.assertIn("fork build configs: zero retired filters/options", res.stdout)
        self.assertIn("docs examples: zero retired filters/options", res.stdout)
        self.assertIn("patch tests: zero retired filters/options", res.stdout)
        self.assertRegex(res.stdout, r"across [1-9][0-9]* scanned doc files")

    def test_planted_negative_series_surface(self) -> None:
        """Planted vmaf_pre in a patch causes series surface check failure."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_patch = tmp / "ffmpeg-patches" / "0004-add-vmafx-pre-filter.patch"
            with target_patch.open("a", encoding="utf-8") as fp:
                fp.write("\n+/* planted defect: vmaf_pre filter */\n")

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code")
            self.assertIn("patch carries retired vmaf filter/option", res.stderr)

    def test_planted_negative_fork_build_config_surface(self) -> None:
        """Planted vmaf_pre in a build config causes config check failure."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_cfg = tmp / "Containerfile.vmafx"
            with target_cfg.open("a", encoding="utf-8") as fp:
                fp.write("\n# planted defect: enable vmaf_pre filter\n")

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code")
            self.assertIn("fork build config references retired filter/option", res.stderr)

    def test_planted_negative_docs_examples_surface(self) -> None:
        """Planted vmaf_pre in docs outside migration guide causes doc check failure."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_doc = tmp / "docs" / "usage" / "vmaf-tune-ffmpeg.md"
            with target_doc.open("a", encoding="utf-8") as fp:
                fp.write("\nPlanted defect: use vmaf_pre in the graph.\n")

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code")
            self.assertIn("retired filter/option in doc", res.stderr)

    def test_planted_doc_outside_old_list_fails(self) -> None:
        """Planted libvmaf_cuda in a doc outside old allow-list fails check."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_doc = tmp / "docs" / "reference" / "faq.md"
            with target_doc.open("a", encoding="utf-8") as fp:
                fp.write("\nPlanted defect: run libvmaf_cuda filter here.\n")

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code")
            self.assertIn("retired filter/option in doc", res.stderr)

    def test_planted_doc_in_adr_passes(self) -> None:
        """Planted libvmaf_cuda in docs/adr is excluded and passes check."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_adr = tmp / "docs" / "adr" / "0000-template.md"
            with target_adr.open("a", encoding="utf-8") as fp:
                fp.write("\nHistorical context: libvmaf_cuda filter was used previously.\n")

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertEqual(
                res.returncode,
                0,
                f"Expected exit 0 for docs/adr, got {res.returncode}.\nstderr:\n{res.stderr}",
            )
            self.assertIn("docs examples: zero retired filters/options", res.stdout)

    def test_planted_negative_patch_test_surface(self) -> None:
        """Planted libvmaf_sycl= filter in a patch test script fails surface check."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_script = tmp / "ffmpeg-patches" / "test" / "check-sycl-feature-routing.sh"
            with target_script.open("a", encoding="utf-8") as fp:
                fp.write(
                    "\n# planted defect:\n# -filter_complex '[0:v][1:v]libvmaf_sycl=threads=1'\n"
                )

            res = _run_script(tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp))
            self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code")
            self.assertIn("patch test carries retired filter/option", res.stderr)


class SurfaceOnlyModeTests(_FixtureTree, unittest.TestCase):
    """Verify the --surface-only mode the pre-commit hook runs."""

    def test_surface_only_passes_without_origin_master(self) -> None:
        """A checkout without origin/master passes the tree checks; the full gate cannot."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            script = tmp / "scripts" / "ci" / SCRIPT.name

            res = _run_script(script, tmp, "--surface-only", REPO_ROOT=str(tmp))
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(SURFACE_ONLY_DONE_LINE, res.stdout)
            self.assertNotIn("tree checks read HEAD_SHA", res.stdout)
            self.assertNotIn("PR body from", res.stdout)

            full = _run_script(script, tmp, REPO_ROOT=str(tmp))
            self.assertEqual(full.returncode, 2, f"stderr:\n{full.stderr}")
            self.assertIn("origin/master not found", full.stderr)

    def test_surface_only_fails_on_planted_doc(self) -> None:
        """A retired filter in a doc fails the tree checks in --surface-only mode too."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            target_doc = tmp / "docs" / "usage" / "vmaf-tune-ffmpeg.md"
            with target_doc.open("a", encoding="utf-8") as fp:
                fp.write("\nPlanted defect: use vmaf_pre in the graph.\n")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--surface-only", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 1, f"stderr:\n{res.stderr}")
            self.assertIn("retired filter/option in doc", res.stderr)
            self.assertNotIn(SURFACE_ONLY_DONE_LINE, res.stdout)

    def test_surface_only_fails_on_planted_patch_test(self) -> None:
        """Planted libvmaf_sycl= in patch test fails in --surface-only mode."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            target_script = tmp / "ffmpeg-patches" / "test" / "check-sycl-feature-routing.sh"
            with target_script.open("a", encoding="utf-8") as fp:
                fp.write(
                    "\n# planted defect:\n# -filter_complex '[0:v][1:v]libvmaf_sycl=threads=1'\n"
                )

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--surface-only", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 1, f"stderr:\n{res.stderr}")
            self.assertIn("patch test carries retired filter/option", res.stderr)
            self.assertNotIn(SURFACE_ONLY_DONE_LINE, res.stdout)

    def test_surface_only_fails_when_zero_docs_scanned(self) -> None:
        """Zero doc files scanned in fixture tree fails surface check."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            shutil.rmtree(tmp / "docs", ignore_errors=True)
            (tmp / "README.md").unlink(missing_ok=True)
            (tmp / "ffmpeg-patches" / "README.md").unlink(missing_ok=True)
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-q", "-m", "remove docs")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--surface-only", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 1, f"stderr:\n{res.stderr}")
            self.assertIn("zero doc files scanned", res.stderr)
            self.assertNotIn(SURFACE_ONLY_DONE_LINE, res.stdout)

    def test_surface_only_fails_when_doc_listing_fails(self) -> None:
        """A git ls-files failure (corrupt index) is named, never read as an empty doc list."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            (tmp / ".git" / "index").write_bytes(b"not an index")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--surface-only", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 1, f"stderr:\n{res.stderr}")
            self.assertIn("[FAIL] cannot list docs", res.stderr)
            self.assertNotIn("zero doc files scanned", res.stderr)
            self.assertNotIn(SURFACE_ONLY_DONE_LINE, res.stdout)

    def test_surface_only_fails_when_find_listing_fails_outside_git(self) -> None:
        """Outside a git tree, a find error on one doc root fails closed, not as a partial list."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            shutil.rmtree(tmp / ".git")
            (tmp / "ffmpeg-patches" / "README.md").unlink()

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--surface-only", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 1, f"stderr:\n{res.stderr}")
            self.assertIn("[FAIL] cannot list docs", res.stderr)
            self.assertNotIn(SURFACE_ONLY_DONE_LINE, res.stdout)

    def test_bash_32_contract_no_mapfile_or_readarray(self) -> None:
        """Script must not use mapfile or readarray because macOS bash 3.2 lacks them."""
        content = SCRIPT.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            code_only = line.split("#", 1)[0]
            for token in ("mapfile", "readarray"):
                self.assertNotIn(
                    token,
                    code_only,
                    f"Line {lineno} violates bash 3.2 compatibility by using '{token}': {line}",
                )

    def test_bash_32_contract_array_loops_survive_empty_arrays(self) -> None:
        """Bash 3.2 under set -u aborts on "${a[@]}" when a is empty, so every loop is guarded."""
        content = SCRIPT.read_text(encoding="utf-8")
        loops = re.findall(r'for \w+ in "\$\{(\w+)\[@\]\}"', content)
        self.assertTrue(loops, "no array loops found; the pattern no longer matches the script")
        for name in loops:
            count_guard = f'"${{#{name}[@]}}" -eq 0'
            # A quoted first element followed by whitespace or ")" is a literal, never a glob.
            literal_list = re.search(rf'\b{name}=\(\s*"[^"]*"(?=[\s)])', content)
            self.assertTrue(
                count_guard in content or literal_list,
                f'loop over "${{{name}[@]}}" has no empty-array guard; use ${{{name}[@]+"${{{name}[@]}}"}}',
            )

    def test_bad_arguments_exit_two(self) -> None:
        """An unknown option or a second argument is a usage error, never a pass."""
        cases = {
            "unknown option": ("--surface",),
            "second argument": ("--surface-only", "--surface-only"),
            "second argument, skip-invariants": ("--skip-invariants", "--skip-invariants"),
            "both flags": ("--surface-only", "--skip-invariants"),
        }
        for label, args in cases.items():
            with self.subTest(label):
                res = _run_script(SCRIPT, ROOT, *args)
                self.assertEqual(res.returncode, 2, f"stderr:\n{res.stderr}")
                self.assertIn("the options are --surface-only and --skip-invariants", res.stderr)
                self.assertNotIn("[PASS]", res.stdout)

    def test_hook_runs_surface_only_on_every_tree_check_input(self) -> None:
        """The hook runs --surface-only, and its files pattern covers every path the checks read."""
        config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
        hooks = [
            hook
            for repo in config.get("repos") or []
            for hook in repo.get("hooks") or []
            if hook.get("id") == HOOK_ID
        ]
        self.assertEqual(len(hooks), 1, f"expected one {HOOK_ID} hook")
        hook = hooks[0]
        self.assertEqual(
            hook["entry"], "bash scripts/ci/ffmpeg-patches-surface-check.sh --surface-only"
        )
        files = re.compile(hook["files"])

        body = SCRIPT.read_text(encoding="utf-8")
        block = re.search(r"^FORK_BUILD_CONFIGS=\(\n(.*?)\n\)", body, re.M | re.S)
        self.assertIsNotNone(block, "configs=( ... ) list not found in the script")
        assert block is not None
        configs = re.findall(r'"([^"]+)"', block.group(1))
        self.assertGreaterEqual(len(configs), 9)
        read_paths = [
            *configs,
            "README.md",
            "docs/usage/ffmpeg.md",
            "ffmpeg-patches/README.md",
            "ffmpeg-patches/0001-example.patch",
            "ffmpeg-patches/test/build-and-run.sh",
            "scripts/ci/ffmpeg-patches-surface-check.sh",
        ]
        for path in read_paths:
            with self.subTest(read=path):
                self.assertIsNotNone(files.search(path), f"{path} does not trigger {HOOK_ID}")
        for path in ("core/src/libvmaf.c", "docs/usage/ffmpeg.txt", "Dockerfile.node"):
            with self.subTest(unread=path):
                self.assertIsNone(files.search(path), f"{path} triggers {HOOK_ID}")


class SkipInvariantsModeTests(_FixtureTree, unittest.TestCase):
    """Verify the --skip-invariants mode the PR-body-input selection test runs (issue #3457)."""

    SKIP_MARKER = "--skip-invariants: the #2435 invariant surface checks are skipped"
    FLAG = "--skip-invariants"

    def test_skip_invariants_still_decides_the_body_stage(self) -> None:
        """Without origin/master the gate still fails closed in the diff stage — tree checks skipped, verdicts unchanged."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            script = tmp / "scripts" / "ci" / SCRIPT.name

            res = _run_script(script, tmp, "--skip-invariants", REPO_ROOT=str(tmp))
            self.assertEqual(res.returncode, 2, f"stderr:\n{res.stderr}")
            self.assertIn("origin/master not found", res.stderr)
            self.assertIn(self.SKIP_MARKER, res.stdout)
            self.assertNotIn("series surface", res.stdout)

    def test_skip_invariants_reaches_the_full_verdict(self) -> None:
        """A clean diff passes with the body stage intact and no invariant line printed."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            script = tmp / "scripts" / "ci" / SCRIPT.name

            res = _run_script(script, tmp, "--skip-invariants", REPO_ROOT=str(tmp))
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("PR body from", res.stdout)
            self.assertIn(self.SKIP_MARKER, res.stdout)
            self.assertNotIn("series surface", res.stdout)
            self.assertNotIn("docs examples", res.stdout)

    def test_skip_invariants_ignores_planted_doc_defects(self) -> None:
        """A planted retired filter in a doc only the tree scans read does not fail this mode."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            target_doc = tmp / "docs" / "usage" / "vmaf-tune-ffmpeg.md"
            with target_doc.open("a", encoding="utf-8") as fp:
                fp.write("\nPlanted defect: use vmaf_pre in the graph.\n")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, "--skip-invariants", REPO_ROOT=str(tmp)
            )
            self.assertEqual(res.returncode, 0, f"stderr:\n{res.stderr}")
            self.assertIn(self.SKIP_MARKER, res.stdout)

    def test_no_automation_passes_skip_invariants(self) -> None:
        """Nothing under the automation surfaces may pass --skip-invariants to the gate.

        The flag exists so the PR-body-input selection test can measure the
        fd-0 decision without the tree scans (issue #3457). A workflow or
        hook passing it would silently stop the #2435 invariant scans in CI
        while the run still logs a pass, so every automation surface that
        could invoke the gate is scanned for the flag — the workflows, the
        composite actions under .github/actions/ (review of PR #3461), the
        pre-commit config and the lefthook config.
        """
        surfaces: list[Path] = [ROOT / ".pre-commit-config.yaml", ROOT / "lefthook.yml"]
        workflows = ROOT / ".github" / "workflows"
        if workflows.is_dir():
            surfaces.extend(sorted(p for p in workflows.iterdir() if p.suffix in (".yml", ".yaml")))
        actions = ROOT / ".github" / "actions"
        if actions.is_dir():
            surfaces.extend(sorted(actions.glob("*/action.y*ml")))
        for path in surfaces:
            if not path.is_file():
                continue
            with self.subTest(surface=str(path.relative_to(ROOT))):
                self.assertNotIn(
                    self.FLAG,
                    path.read_text(encoding="utf-8"),
                    f"{path.relative_to(ROOT)} passes {self.FLAG} to the surface gate; "
                    "the #2435 invariant scans must run in CI",
                )

class HeadRevisionConsumedSetTests(_FixtureTree, unittest.TestCase):
    """Verify section 4 builds the consumed symbol set from HEAD_SHA, not the checkout."""

    def _stage_probe_base(self, td: str) -> tuple[Path, Path, Path, str, str]:
        """Create a base commit whose public header declares a symbol a patch consumes."""
        tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
        header = tmp / PROBE_HEADER_REL
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(PROBE_HEADER_WITH_SYM, encoding="utf-8")
        patch = tmp / PROBE_PATCH_REL
        orig_patch = patch.read_text(encoding="utf-8")
        patch.write_text(f"{orig_patch}\n+    ret = {PROBE_SYMBOL}();\n", encoding="utf-8")
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-q", "-m", "base with probe symbol")
        return tmp, header, patch, orig_patch, _rev_parse(tmp, "HEAD")

    def test_consumed_set_reads_head_sha_not_working_tree(self) -> None:
        """Uncommitted working-tree patch edit cannot hide a symbol still consumed at HEAD_SHA."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, patch, orig_patch, base_sha = self._stage_probe_base(td)
            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "remove probe declaration without patch update")
            head_sha = _rev_parse(tmp, "HEAD")
            patch.write_text(orig_patch, encoding="utf-8")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("::error title=Agent hard rule 11::", res.stdout)
            self.assertIn(PROBE_SYMBOL, res.stdout)

    def test_consumed_set_boundary_head_commit_updates_patch(self) -> None:
        """Removing the header declaration passes when the head commit also edits the patch."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, patch, orig_patch, base_sha = self._stage_probe_base(td)
            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            patch.write_text(orig_patch, encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL, PROBE_PATCH_REL)
            _git(tmp, "commit", "-q", "-m", "remove probe declaration and update patch")
            head_sha = _rev_parse(tmp, "HEAD")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")

    def test_consumed_set_zero_patches_at_head_sha(self) -> None:
        """Zero patches at HEAD: HEAD_SHA set fails sec 0; HEAD_SHA unset hits sec 4 exit 0."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, _patch, _orig_patch, base_sha = self._stage_probe_base(td)
            _git(tmp, "update-ref", "refs/remotes/origin/master", base_sha)
            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            for p in (tmp / "ffmpeg-patches").glob("*.patch"):
                p.unlink()
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-q", "-m", "drop patches at head")
            head_sha = _rev_parse(tmp, "HEAD")
            for src in (ROOT / "ffmpeg-patches").glob("*.patch"):
                shutil.copy(src, tmp / "ffmpeg-patches" / src.name)
            script = tmp / "scripts" / "ci" / SCRIPT.name

            with self.subTest(mode="head_sha_set"):
                res = _run_script(
                    script,
                    tmp,
                    REPO_ROOT=str(tmp),
                    BASE_SHA=base_sha,
                    HEAD_SHA=head_sha,
                    PR_BODY="",
                )
                self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
                self.assertIn("no patches found", res.stderr)
                self.assertNotIn("[PASS]", res.stdout)

            with self.subTest(mode="head_sha_unset"):
                res = _run_script(
                    script,
                    tmp,
                    REPO_ROOT=str(tmp),
                    BASE_SHA=base_sha,
                    PR_BODY="",
                )
                self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
                self.assertIn("missing; nothing to check", res.stderr)

    def test_consumed_set_from_subdirectory_exits_one(self) -> None:
        """Running from a subdirectory still reads patches and header diff from tree root."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, patch, orig_patch, base_sha = self._stage_probe_base(td)
            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "remove probe declaration without patch update")
            head_sha = _rev_parse(tmp, "HEAD")
            patch.write_text(orig_patch, encoding="utf-8")
            subdir = tmp / "core"
            script = (tmp / "scripts" / "ci" / SCRIPT.name).resolve()
            self.assertTrue(subdir.is_dir())
            self.assertTrue(script.is_absolute())

            res = _run_script(
                script,
                subdir,
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("::error title=Agent hard rule 11::", res.stdout)
            self.assertIn(PROBE_SYMBOL, res.stdout)

    def test_consumed_set_negative_missing_head_or_blob_exits_two(self) -> None:
        """With HEAD_SHA set, missing HEAD_SHA or missing patch blob/tree fails in section 0."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, _patch, _orig_patch, base_sha = self._stage_probe_base(td)
            script = tmp / "scripts" / "ci" / SCRIPT.name
            missing_sha = "deadbeef" * 5
            with self.subTest(expected="cannot resolve HEAD_SHA"):
                res = _run_script(
                    script,
                    tmp,
                    REPO_ROOT=str(tmp),
                    BASE_SHA=base_sha,
                    HEAD_SHA=missing_sha,
                    PR_BODY="",
                )
                self.assertEqual(res.returncode, 2, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
                self.assertEqual(len(res.stderr.splitlines()), 1, f"stderr:\n{res.stderr}")
                self.assertIn("ffmpeg-patches-surface-check: cannot resolve HEAD_SHA", res.stderr)
                self.assertIn(missing_sha, res.stderr)

            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "remove probe declaration")
            head_sha = _rev_parse(tmp, "HEAD")
            tree_sha = _rev_parse(tmp, f"{head_sha}:ffmpeg-patches")
            self.assertEqual(tree_sha, _rev_parse(tmp, f"{base_sha}:ffmpeg-patches"))
            blob_sha = _rev_parse(tmp, f"{head_sha}:{PROBE_PATCH_REL}")

            for obj_sha, expected_msg in (
                (blob_sha, "cannot extract surface inputs at"),
                (tree_sha, "cannot list surface inputs at"),
            ):
                with self.subTest(expected=expected_msg):
                    (tmp / ".git" / "objects" / obj_sha[:2] / obj_sha[2:]).unlink()
                    bad = _run_script(
                        script,
                        tmp,
                        REPO_ROOT=str(tmp),
                        BASE_SHA=base_sha,
                        HEAD_SHA=head_sha,
                        PR_BODY="",
                    )
                    self.assertEqual(
                        bad.returncode, 2, f"stdout:\n{bad.stdout}\nstderr:\n{bad.stderr}"
                    )
                    err_lines = bad.stderr.strip().splitlines()
                    self.assertEqual(len(err_lines), 1, f"stderr:\n{bad.stderr}")
                    self.assertTrue(err_lines[0].startswith("ffmpeg-patches-surface-check:"))
                    self.assertIn(expected_msg, err_lines[0])
                    self.assertIn(head_sha, err_lines[0])

    def test_consumed_set_negative_sec4_without_head_sha_exits_two(self) -> None:
        """With HEAD_SHA unset, missing patch blob or tree fails in section 4 (exit 2)."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, _patch, _orig_patch, base_sha = self._stage_probe_base(td)
            _git(tmp, "update-ref", "refs/remotes/origin/master", base_sha)
            header.write_text(PROBE_HEADER_WITHOUT_SYM, encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "drop probe symbol on head branch")
            blob_sha = _rev_parse(tmp, f"HEAD:{PROBE_PATCH_REL}")
            tree_sha = _rev_parse(tmp, "HEAD:ffmpeg-patches")
            script = tmp / "scripts" / "ci" / SCRIPT.name

            for label, missing_oid, sec4_msg in (
                ("blob", blob_sha, "cannot read ffmpeg-patches/"),
                ("tree", tree_sha, "cannot list ffmpeg-patches/ at HEAD"),
            ):
                with self.subTest(deleted=label):
                    (tmp / ".git" / "objects" / missing_oid[:2] / missing_oid[2:]).unlink()
                    res = _run_script(script, tmp, REPO_ROOT=str(tmp), PR_BODY="")
                    self.assertEqual(
                        res.returncode, 2, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
                    )
                    stderr_lines = res.stderr.splitlines()
                    self.assertEqual(len(stderr_lines), 1, f"stderr:\n{res.stderr}")
                    self.assertTrue(stderr_lines[0].startswith("ffmpeg-patches-surface-check:"))
                    self.assertIn(sec4_msg, stderr_lines[0])
                    self.assertIn("at HEAD.", stderr_lines[0])


class HeadRevisionTreeChecksTests(_FixtureTree, unittest.TestCase):
    """Verify section 0 reads the HEAD_SHA tree (not checkout) when HEAD_SHA is set."""

    def test_head_tree_retired_doc_fails_when_checkout_restored(self) -> None:
        """Retired filter at HEAD_SHA fails section 0 even when checkout restores clean doc."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            faq = tmp / "docs" / "reference" / "faq.md"
            base_text = faq.read_text(encoding="utf-8")
            faq.write_text(
                f"{base_text}\nPlanted retired option: libvmaf_tune.\n",
                encoding="utf-8",
            )
            _git(tmp, "add", "docs/reference/faq.md")
            _git(tmp, "commit", "-q", "-m", "add retired filter to faq")
            head_sha = _rev_parse(tmp, "HEAD")
            faq.write_text(base_text, encoding="utf-8")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("tree checks read HEAD_SHA", res.stdout)
            self.assertIn(head_sha, res.stdout)
            self.assertIn(
                "ffmpeg-patches-surface-check: [FAIL] retired filter/option in doc docs/reference/faq.md:",
                res.stderr,
            )
            self.assertIn("libvmaf_tune", res.stderr)

    def test_head_tree_clean_passes_with_uncommitted_checkout_defect(self) -> None:
        """Uncommitted retired filter in checkout does not fail when HEAD_SHA tree is clean."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            readme = tmp / "README.md"
            readme.write_text(
                f"{readme.read_text(encoding='utf-8')}\nHarmless README note.\n",
                encoding="utf-8",
            )
            _git(tmp, "add", "README.md")
            _git(tmp, "commit", "-q", "-m", "harmless readme update")
            head_sha = _rev_parse(tmp, "HEAD")
            faq = tmp / "docs" / "reference" / "faq.md"
            faq.write_text(
                f"{faq.read_text(encoding='utf-8')}\nUncommitted: libvmaf_tune.\n",
                encoding="utf-8",
            )

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(
                "ffmpeg-patches-surface-check: [PASS] docs examples: zero retired filters/options",
                res.stdout,
            )

    def test_head_tree_boundary_deleted_build_config_passes(self) -> None:
        """Deleting an allow-listed fork build config at HEAD_SHA skips it on export and passes."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            _git(tmp, "rm", "-q", "Dockerfile.ffmpeg")
            _git(tmp, "commit", "-q", "-m", "remove Dockerfile.ffmpeg")
            head_sha = _rev_parse(tmp, "HEAD")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("fork build configs: zero retired filters/options", res.stdout)

    def test_head_tree_tmpdir_inside_worktree_uses_find(self) -> None:
        """Temp export dir under TMPDIR inside a git worktree still lists docs via find."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            readme = tmp / "README.md"
            readme.write_text(
                f"{readme.read_text(encoding='utf-8')}\nHarmless note.\n",
                encoding="utf-8",
            )
            _git(tmp, "add", "README.md")
            _git(tmp, "commit", "-q", "-m", "readme note")
            head_sha = _rev_parse(tmp, "HEAD")
            nested_tmpdir = tmp / "nested-tmp"
            nested_tmpdir.mkdir()

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
                TMPDIR=str(nested_tmpdir),
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(
                "ffmpeg-patches-surface-check: [PASS] docs examples: zero retired filters/options",
                res.stdout,
            )


def _run_extractor(*args: str) -> TextCommandResult:
    """Run extract_tree_blobs.py via sys.executable with the clean git environment."""
    return run_command(
        [sys.executable, str(EXTRACTOR), *args],
        allowed_executables=(sys.executable,),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=_clean_env(),
        timeout_seconds=GIT_TIMEOUT_S,
    )


def _git_blob_bytes(repo: Path, spec: str) -> bytes:
    """Return raw blob bytes from git cat-file blob."""
    git = _tool("git")
    res = run_command(
        [git, "-C", str(repo), "cat-file", "blob", spec],
        allowed_executables=(git,),
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=False,
        timeout_seconds=GIT_TIMEOUT_S,
    )
    return res.stdout


def _git_stdin(repo: Path, *args: str, data: str) -> str:
    """Run git in ``repo`` with text stdin and return stripped stdout."""
    git = _tool("git")
    res = run_command(
        [git, "-C", str(repo), *args],
        allowed_executables=(git,),
        check=True,
        env=_clean_env(),
        input_data=data,
        capture_output=True,
        text=True,
        timeout_seconds=GIT_TIMEOUT_S,
    )
    return res.stdout.strip()


class HeadRevisionArchiveAttributeTests(_FixtureTree, unittest.TestCase):
    """Verify head-tree blob extraction ignores archive attributes and fails closed."""

    def test_t1_export_ignore_on_build_config_still_fails(self) -> None:
        """T1: export-ignore on Dockerfile.ffmpeg cannot hide --enable-libvmaf at HEAD_SHA."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            cfg = tmp / "Dockerfile.ffmpeg"
            clean_cfg = cfg.read_text(encoding="utf-8")
            (tmp / ".gitattributes").write_text(
                "Dockerfile.ffmpeg export-ignore\n", encoding="utf-8"
            )
            cfg.write_text(f"{clean_cfg}\nRUN ./configure --enable-libvmaf\n", encoding="utf-8")
            _git(tmp, "add", ".gitattributes", "Dockerfile.ffmpeg")
            _git(tmp, "commit", "-q", "-m", "hide broken Dockerfile.ffmpeg via export-ignore")
            head_sha = _rev_parse(tmp, "HEAD")
            cfg.write_text(clean_cfg, encoding="utf-8")
            (tmp / ".gitattributes").unlink()

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("Dockerfile.ffmpeg", res.stderr)

    def test_t2_export_ignore_on_doc_with_retired_filter_fails(self) -> None:
        """T2: export-ignore on a doc under docs/ cannot hide a retired filter reference."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            faq = tmp / "docs" / "reference" / "faq.md"
            clean_faq = faq.read_text(encoding="utf-8")
            (tmp / ".gitattributes").write_text(
                "docs/reference/faq.md export-ignore\n", encoding="utf-8"
            )
            faq.write_text(
                f"{clean_faq}\nPlanted retired filter: libvmaf_tune.\n", encoding="utf-8"
            )
            _git(tmp, "add", ".gitattributes", "docs/reference/faq.md")
            _git(tmp, "commit", "-q", "-m", "hide retired doc via export-ignore")
            head_sha = _rev_parse(tmp, "HEAD")
            faq.write_text(clean_faq, encoding="utf-8")
            (tmp / ".gitattributes").unlink()

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("docs/reference/faq.md", res.stderr)

    def test_t3_export_subst_keeps_blob_bytes_unchanged_and_passes(self) -> None:
        """T3: export-subst on a doc leaves $Format:%H$ unexpanded and gate passes."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            faq = tmp / "docs" / "reference" / "faq.md"
            (tmp / ".gitattributes").write_text(
                "docs/reference/faq.md export-subst\n", encoding="utf-8"
            )
            faq.write_text(
                f"{faq.read_text(encoding='utf-8')}\nCommit format token: $Format:%H$\n",
                encoding="utf-8",
            )
            _git(tmp, "add", ".gitattributes", "docs/reference/faq.md")
            _git(tmp, "commit", "-q", "-m", "add export-subst placeholder")
            head_sha = _rev_parse(tmp, "HEAD")

            extracted = Path(td) / "extracted"
            ext = _run_extractor(
                "--repo",
                str(tmp),
                "--rev",
                head_sha,
                "--dest",
                str(extracted),
                "--",
                "docs/reference/faq.md",
            )
            self.assertEqual(ext.returncode, 0, ext.stderr)
            expected = _git_blob_bytes(tmp, f"{head_sha}:docs/reference/faq.md")
            self.assertIn(b"$Format:%H$", expected)
            self.assertEqual((extracted / "docs" / "reference" / "faq.md").read_bytes(), expected)

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")

    def test_t4_entry_bound_exact_passes_and_plus_one_fails(self) -> None:
        """T4: exact entry bound passes (keeping 100755 executable); bound + 1 fails cleanly."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "master")
            for idx in range(SMALL_ENTRY_BOUND):
                path = repo / "sub" / f"f{idx}.sh"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"#!/bin/sh\necho {idx}\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "update-index", "--chmod=+x", "sub/f0.sh")
            _git(repo, "commit", "-q", "-m", "exact bound")
            exact_sha = _rev_parse(repo, "HEAD")

            out_ok = Path(td) / "out-ok"
            ok = _run_extractor(
                "--repo",
                str(repo),
                "--rev",
                exact_sha,
                "--dest",
                str(out_ok),
                "--max-entries",
                str(SMALL_ENTRY_BOUND),
                "--",
                "sub",
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stderr, "")
            self.assertTrue(os.access(out_ok / "sub" / "f0.sh", os.X_OK))

            (repo / "sub" / "extra.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            _git(repo, "add", "sub/extra.sh")
            _git(repo, "commit", "-q", "-m", "bound plus one")
            over_sha = _rev_parse(repo, "HEAD")
            bad = _run_extractor(
                "--repo",
                str(repo),
                "--rev",
                over_sha,
                "--dest",
                str(Path(td) / "out-over"),
                "--max-entries",
                str(SMALL_ENTRY_BOUND),
                "--",
                "sub",
            )
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(len(bad.stderr.splitlines()), 1, bad.stderr)

    def test_t5_symlink_entry_under_docs_exits_two(self) -> None:
        """T5: a 120000 symlink entry under docs/ at HEAD_SHA exits 2 with one stderr line."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            base_sha = _rev_parse(tmp, "HEAD")
            oid = _git_stdin(tmp, "hash-object", "-w", "--stdin", data="faq.md")
            _git(tmp, "update-index", "--add", "--cacheinfo", f"120000,{oid},docs/link.md")
            _git(tmp, "commit", "-q", "-m", "add symlink under docs")
            head_sha = _rev_parse(tmp, "HEAD")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(
                res.returncode, RC_GATE_ERROR, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            err_lines = res.stderr.splitlines()
            self.assertEqual(len(err_lines), 1, f"stderr:\n{res.stderr}")
            self.assertTrue(err_lines[0].startswith("ffmpeg-patches-surface-check:"))
            self.assertIn("cannot extract surface inputs at", err_lines[0])

    def test_extractor_rejects_gitlink_bad_paths_batch_errors_and_write_errors(self) -> None:
        """Helper fails closed on gitlinks, unsafe paths, malformed batch output, and write errors."""
        oid = "a" * 40
        for bad_mode in ("120000", "160000"):
            with self.assertRaises(extract_tree_blobs.ExtractError):
                extract_tree_blobs._select_regular_blobs({"docs/x.md": (bad_mode, oid)}, 10)
        for bad_path in ("/abs.md", "docs//x.md", "docs/./x.md", "docs/../x.md", "docs/.git/x"):
            with self.assertRaises(extract_tree_blobs.ExtractError):
                extract_tree_blobs._validate_relpath(bad_path)
        spec = ("docs/x.md", ("docs", "x.md"), "100644", oid)
        for bad_raw in (
            f"{oid} missing\n".encode(),
            f"{oid} tree 2\nhi\n".encode(),
            f"{oid} blob 5\nhi\n".encode(),
            b"truncated",
        ):
            with self.assertRaises(extract_tree_blobs.ExtractError):
                extract_tree_blobs._parse_batch_stream(bad_raw, [spec])
        with tempfile.TemporaryDirectory() as td:
            file_dest = Path(td) / "not-a-dir"
            file_dest.write_text("occupied\n", encoding="utf-8")
            res = _run_extractor(
                "--repo", str(ROOT), "--rev", "HEAD", "--dest", str(file_dest), "--", "README.md"
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertEqual(len(res.stderr.splitlines()), 1, res.stderr)


CAFE_PATCH_REL = "ffmpeg-patches/0001-caf\u00e9.patch"
CAFE_PROBE_SYMBOL = "vmaf_fixture_cafe_probe"
SPACE_PATCH_REL = "ffmpeg-patches/0005-with space.patch"
SPACE_PROBE_SYMBOL = "vmaf_fixture_space_probe"
QUOTE_PATCH_REL = 'ffmpeg-patches/0006-with"quote.patch'
QUOTE_PROBE_SYMBOL = "vmaf_fixture_quote_probe"
NON_ASCII_DOC_REL = "docs/usage/\u00fcbersicht.md"
PATCH_TREE_RECORD_CAP = 1024


class NulDelimitedPathListingTests(_FixtureTree, unittest.TestCase):
    """Verify NUL-delimited path listings across git ls-tree, git diff, and git ls-files."""

    def _stage_cafe_base(self, td: str) -> tuple[Path, Path, Path, str]:
        """Stage a base commit where a non-ASCII patch consumes a public header symbol."""
        tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
        header = tmp / PROBE_HEADER_REL
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(
            f"#ifndef VMAF_CAFE_H_\n#define VMAF_CAFE_H_\nint {CAFE_PROBE_SYMBOL}(void);\n#endif\n",
            encoding="utf-8",
        )
        cafe_patch = tmp / CAFE_PATCH_REL
        cafe_patch.write_text(
            f"--- a/fftools/ffmpeg.c\n+++ b/fftools/ffmpeg.c\n+    ret = {CAFE_PROBE_SYMBOL}();\n",
            encoding="utf-8",
        )
        _git(tmp, "add", ".")
        _git(tmp, "commit", "-q", "-m", "base with non-ascii patch")
        return tmp, header, cafe_patch, _rev_parse(tmp, "HEAD")

    def test_t1_non_ascii_patch_consumed_symbol_change_fails(self) -> None:
        """T1: changing a symbol consumed by an untouched non-ASCII patch exits 1."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, _cafe_patch, base_sha = self._stage_cafe_base(td)
            header.write_text(
                f"#ifndef VMAF_CAFE_H_\n#define VMAF_CAFE_H_\nint {CAFE_PROBE_SYMBOL}(int flags);\n#endif\n",
                encoding="utf-8",
            )
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "change cafe symbol without updating patch")
            head_sha = _rev_parse(tmp, "HEAD")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn("::error title=Agent hard rule 11::", res.stdout)
            self.assertIn(CAFE_PROBE_SYMBOL, res.stdout)

    def test_t2_non_ascii_patch_touched_in_pr_passes(self) -> None:
        """T2: updating both the header symbol and the non-ASCII patch sets patch_touched and passes."""
        with tempfile.TemporaryDirectory() as td:
            tmp, header, cafe_patch, base_sha = self._stage_cafe_base(td)
            header.write_text(
                f"#ifndef VMAF_CAFE_H_\n#define VMAF_CAFE_H_\nint {CAFE_PROBE_SYMBOL}(int flags);\n#endif\n",
                encoding="utf-8",
            )
            cafe_patch.write_text(
                f"--- a/fftools/ffmpeg.c\n+++ b/fftools/ffmpeg.c\n+    ret = {CAFE_PROBE_SYMBOL}(0);\n",
                encoding="utf-8",
            )
            _git(tmp, "add", PROBE_HEADER_REL, CAFE_PATCH_REL)
            _git(tmp, "commit", "-q", "-m", "change cafe symbol and update non-ascii patch")
            head_sha = _rev_parse(tmp, "HEAD")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                BASE_SHA=base_sha,
                HEAD_SHA=head_sha,
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(
                "at least one ffmpeg-patches/*.patch is in the diff. PASS.",
                res.stdout,
            )
            self.assertIn(CAFE_PROBE_SYMBOL, res.stdout)

    def test_t3_non_ascii_doc_with_retired_filter_fails(self) -> None:
        """T3: a tracked non-ASCII doc carrying a retired filter fails and names that doc."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            doc = tmp / NON_ASCII_DOC_REL
            doc.parent.mkdir(parents=True, exist_ok=True)
            doc.write_text(
                "# \u00dcbersicht\n\nPlanted defect: use vmaf_pre in the graph.\n",
                encoding="utf-8",
            )
            _git(tmp, "add", NON_ASCII_DOC_REL)
            _git(tmp, "commit", "-q", "-m", "add non-ascii doc with retired filter")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name,
                tmp,
                REPO_ROOT=str(tmp),
                PR_BODY="",
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(
                f"retired filter/option in doc {NON_ASCII_DOC_REL}:",
                res.stderr,
            )
            self.assertIn("vmaf_pre", res.stderr)

    def test_t4_patch_names_with_space_and_double_quote_reach_selector(self) -> None:
        """T4: patch names containing spaces and double quotes contribute to the consumed set."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td), origin_master=False)
            header = tmp / PROBE_HEADER_REL
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text(
                f"int {SPACE_PROBE_SYMBOL}(void);\nint {QUOTE_PROBE_SYMBOL}(void);\n",
                encoding="utf-8",
            )
            (tmp / SPACE_PATCH_REL).write_text(
                f"+    ret = {SPACE_PROBE_SYMBOL}();\n", encoding="utf-8"
            )
            quote_oid = _git_stdin(
                tmp,
                "hash-object",
                "-w",
                "--stdin",
                data=f"+    ret = {QUOTE_PROBE_SYMBOL}();\n",
            )
            _git(tmp, "add", PROBE_HEADER_REL, SPACE_PATCH_REL)
            _git(
                tmp, "update-index", "--add", "--cacheinfo", f"100644,{quote_oid},{QUOTE_PATCH_REL}"
            )
            _git(tmp, "commit", "-q", "-m", "base with space and quote patch names")
            base_sha = _rev_parse(tmp, "HEAD")
            _git(tmp, "update-ref", "refs/remotes/origin/master", base_sha)

            header.write_text("/* declarations removed */\n", encoding="utf-8")
            _git(tmp, "add", PROBE_HEADER_REL)
            _git(tmp, "commit", "-q", "-m", "remove space and quote probe symbols")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp), PR_BODY=""
            )
            self.assertEqual(res.returncode, 1, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
            self.assertIn(SPACE_PROBE_SYMBOL, res.stdout)
            self.assertIn(QUOTE_PROBE_SYMBOL, res.stdout)

    def test_t5_listing_failure_exits_two_with_single_stderr_line(self) -> None:
        """T5: missing diff base tree (C2) or missing ffmpeg-patches tree (C1) exits 2."""
        with tempfile.TemporaryDirectory() as td:
            tmp, _header, _cafe_patch, base_sha = self._stage_cafe_base(td)
            base_tree = _rev_parse(tmp, "HEAD^{tree}")
            readme = tmp / "README.md"
            readme.write_text(f"{readme.read_text(encoding='utf-8')}\nnote\n", encoding="utf-8")
            _git(tmp, "add", "README.md")
            _git(tmp, "commit", "-q", "-m", "head readme note")
            head_sha = _rev_parse(tmp, "HEAD")
            patches_tree = _rev_parse(tmp, "HEAD:ffmpeg-patches")
            script = tmp / "scripts" / "ci" / SCRIPT.name

            base_obj = tmp / ".git" / "objects" / base_tree[:2] / base_tree[2:]
            saved_base = base_obj.read_bytes()
            with self.subTest(failure="c2_diff_paths"):
                base_obj.unlink()
                res = _run_script(
                    script,
                    tmp,
                    REPO_ROOT=str(tmp),
                    BASE_SHA=base_sha,
                    HEAD_SHA=head_sha,
                    PR_BODY="",
                )
                self.assertEqual(res.returncode, RC_GATE_ERROR, res.stderr)
                self.assertEqual(
                    res.stderr.splitlines(),
                    [
                        f"ffmpeg-patches-surface-check: cannot list changed paths {base_sha}..{head_sha}."
                    ],
                )
            base_obj.write_bytes(saved_base)

            with self.subTest(failure="c1_ls_tree"):
                _git(tmp, "update-ref", "refs/remotes/origin/master", base_sha)
                (tmp / ".git" / "objects" / patches_tree[:2] / patches_tree[2:]).unlink()
                res = _run_script(script, tmp, REPO_ROOT=str(tmp), PR_BODY="")
                self.assertEqual(res.returncode, RC_GATE_ERROR, res.stderr)
                self.assertEqual(
                    res.stderr.splitlines(),
                    ["ffmpeg-patches-surface-check: cannot list ffmpeg-patches/ at HEAD."],
                )

    def test_patch_tree_record_bound_exceeded_exits_two(self) -> None:
        """Exceeding MAX_PATCH_TREE_RECORDS in ffmpeg-patches/ exits 2 with one stderr line."""
        with tempfile.TemporaryDirectory() as td:
            tmp = self._prepare_temp_worktree(Path(td))
            oid = _git_stdin(tmp, "hash-object", "-w", "--stdin", data="x\n")
            entries = "".join(
                f"100644 {oid}\tffmpeg-patches/extra-{idx:04d}.txt\n"
                for idx in range(PATCH_TREE_RECORD_CAP + 1)
            )
            _git_stdin(tmp, "update-index", "--index-info", data=entries)
            _git(tmp, "commit", "-q", "-m", "exceed patch tree record cap")

            res = _run_script(
                tmp / "scripts" / "ci" / SCRIPT.name, tmp, REPO_ROOT=str(tmp), PR_BODY=""
            )
            self.assertEqual(res.returncode, RC_GATE_ERROR, res.stderr)
            err_lines = res.stderr.splitlines()
            self.assertEqual(len(err_lines), 1, res.stderr)
            self.assertIn(f"exceeds {PATCH_TREE_RECORD_CAP} tree entries", err_lines[0])

if __name__ == "__main__":
    unittest.main()
