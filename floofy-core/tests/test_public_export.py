"""Tests for scripts/export_public.py (Requirement 10.2): the filtered public export.

Like test_de_amazon.py, the test data never spells an internal identifier
literally — planted tokens are taken from the scanner's ``DENYLIST`` at runtime,
so this file stays clean under the very scan it exercises.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_no_internal_identifiers as checker  # noqa: E402
import export_public as exporter  # noqa: E402

HOSTNAME_TOKEN = checker.DENYLIST[0]  # a hostname fragment
ADAPTER_DIR = "editions/" + "tool" + "box"  # split so this file stays scanner-clean


@pytest.fixture(scope="module")
def export_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real export of the current tree, shared by the module's tests."""
    target = tmp_path_factory.mktemp("public-export") / "export"
    report = exporter.export(target)
    assert report["target"] == str(target)
    return target


def test_the_public_set_is_copied_and_nothing_else(export_tree: Path) -> None:
    for rel in ("floofy-core", "loader-app", "spa-host", "registry-tools", "docs",
                "editions/public", "packaging/public", "scripts", "branding", "mods",
                "README.md", "CHANGELOG.md", "pyproject.toml", "uv.lock",
                ".github/workflows/release.yml"):
        assert (export_tree / rel).exists(), f"public-set path missing from the export: {rel}"
    for rel in (ADAPTER_DIR, "packaging/internal", "forge", "research", ".kiro",
                "tools", "Config", "packageInfo", "FloofyCrew.code-workspace",
                ".github/workflows/ci.yml"):
        assert not (export_tree / rel).exists(), f"internal path leaked into the export: {rel}"


def test_excluded_mods_are_absent_and_readme_rows_dropped(export_tree: Path) -> None:
    for mod in exporter.EXCLUDED_MODS:
        assert not (export_tree / "mods" / mod).exists(), f"excluded mod exported: {mod}"
    readme = (export_tree / "mods" / "README.md").read_text(encoding="utf-8")
    for mod in exporter.EXCLUDED_MODS:
        assert mod not in readme, f"mods/README.md still names the excluded mod {mod}"
    assert "custom-themes" in readme and "settings-demo" in readme


def test_internal_only_regions_are_stripped(export_tree: Path) -> None:
    """The README's internal-edition install block and marked table rows are for
    the internal audience only; the export must carry neither them nor any marker."""
    readme = (export_tree / "README.md").read_text(encoding="utf-8")
    assert "git clone <the FloofyCrew package>" not in readme, "the internal install block confuses public readers"
    assert ADAPTER_DIR + "/" not in readme, "the internal adapter's table row is marked internal-only"
    source = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "git clone <the FloofyCrew package>" in source, "the source README keeps the internal block"
    marker_stem = exporter.INTERNAL_ONLY_START.rsplit(":", 1)[0]  # built at runtime: a literal here would trip the stripper on this very file
    for path in export_tree.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            assert marker_stem not in text, f"marker survived the export: {path}"


def test_unbalanced_internal_only_markers_fail_the_export(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "doc.md").write_text(f"public\n<!-- {exporter.INTERNAL_ONLY_START} -->\ninternal\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="never closed"):
        exporter.strip_internal_only_regions(scratch)
    (scratch / "doc.md").write_text(f"public\n<!-- {exporter.INTERNAL_ONLY_END} -->\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="without a start"):
        exporter.strip_internal_only_regions(scratch)


def test_no_caches_or_git_metadata_in_the_export(export_tree: Path) -> None:
    leftovers = [p for p in export_tree.rglob("*") if p.name in exporter.SKIP_NAMES]
    assert leftovers == [], f"skip-set names copied: {[str(p) for p in leftovers[:5]]}"


def test_the_real_tree_exports_clean(export_tree: Path) -> None:
    """What CI runs: the full-tree scan of the export passes with the committed allowlist."""
    exporter.scan_export(export_tree)  # raises ExportError on any hit


def test_a_planted_leak_fails_the_scan(export_tree: Path) -> None:
    planted = export_tree / "docs" / "planted-leak-test.md"
    planted.write_text(f"an internal hostname: {HOSTNAME_TOKEN}\n", encoding="utf-8")
    try:
        with pytest.raises(exporter.ExportError, match="planted-leak-test"):
            exporter.scan_export(export_tree)
    finally:
        planted.unlink()


def test_export_refuses_a_target_with_an_internal_remote(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin",
                    f"ssh://{HOSTNAME_TOKEN.strip('.')}/pkg/Whatever"], check=True)
    with pytest.raises(exporter.ExportError, match="internal remote"):
        exporter.export(clone)


def test_export_refuses_a_non_empty_target_without_force(tmp_path: Path) -> None:
    target = tmp_path / "dirty"
    target.mkdir()
    (target / "existing.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not empty"):
        exporter.export(target)
    exporter.export(target, force=True)  # --force replaces the contents
    assert not (target / "existing.txt").exists() and (target / "README.md").exists()


def test_export_refuses_a_target_inside_the_repository(tmp_path: Path) -> None:
    with pytest.raises(exporter.ExportError, match="outside the repository"):
        exporter.export(REPO_ROOT / "build" / "public-export")


def test_cli_check_passes_on_the_current_tree() -> None:
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "export_public.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public export is clean" in result.stdout
