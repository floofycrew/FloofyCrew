"""The first-party mods under ``mods/`` stay valid, hashed and applicable (task 5.5; Requirement 1.7, 2.1, 2.6).

Since ``rimuru-branding`` 2.0.0 the first-frame boot part and the theme-reset guard live in
``custom-themes`` (the layer every theme mod depends on); the pack itself stays a pure
``theme`` part. ``loader-app/tests/test_custom_themes.py`` covers the editor mod's routes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from floofy_core.boot_script import boot_mods_from_manifests, build_boot_descriptor
from floofy_core.patches import PatchDescriptor, apply_descriptor
from floofy_core.semver import Range, Version
from floofy_core.themes import validate_theme_dir
from floofy_core.validator import validate_mod

from floofy_testing import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import hash_example_files  # noqa: E402

MODS_DIR = REPO_ROOT / "mods"
MODS = sorted(p for p in MODS_DIR.iterdir() if (p / "floofy.json").is_file())
RIMURU = MODS_DIR / "rimuru-branding"
CUSTOM_THEMES = MODS_DIR / "custom-themes"
SCRATCH_ASSETS = Path(os.environ.get("FLOOFY_SCRATCH_PAYLOAD") or REPO_ROOT / ".scratch" / "payload-0.7.0.5") / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist" / "assets"


@pytest.mark.parametrize("mod_dir", MODS, ids=[p.name for p in MODS])
def test_first_party_mod_validates_clean_with_current_hashes(mod_dir: Path):
    report = validate_mod(mod_dir)
    assert not report.errors, [f"{f.code}: {f.message}" for f in report.errors]
    assert not report.warnings, [f"{f.code}: {f.message}" for f in report.warnings]
    changed, problems = hash_example_files.refresh_manifest(mod_dir, check=True, add_missing=True)
    assert not changed and not problems, problems
    manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    assert manifest["id"] == mod_dir.name and "network" not in manifest or manifest["network"].get("hosts", []) == []


def test_rimuru_theme_part_is_the_pack_the_host_validator_accepts():
    summary = validate_theme_dir(RIMURU / "theme")
    assert summary.slug == "rimuru" and summary.level == 1 and summary.name == "Rimuru"
    assert {"branding/logo.png", "branding/favicon.png", "branding/wordmark.svg", "variables.json", "theme.json", "readme.md"} <= set(summary.files)
    assert summary.dark["--bg"] == "#171C26" and summary.light["--bg"] == "#FBF4EB"
    manifest = json.loads((RIMURU / "theme" / "theme.json").read_text(encoding="utf-8"))
    assert manifest["branding"]["botName"] == "Great Sage"


def test_rimuru_is_a_pure_theme_pack_on_top_of_custom_themes():
    manifest = json.loads((RIMURU / "floofy.json").read_text(encoding="utf-8"))
    assert manifest["version"].startswith("2.") and [p["kind"] for p in manifest["parts"]] == ["theme"], "no boot part, no patch: custom-themes provides both"
    assert not (RIMURU / "spa").exists() and not (RIMURU / "patches").exists()
    depends = manifest["dependsOn"]["custom-themes"]
    custom = json.loads((CUSTOM_THEMES / "floofy.json").read_text(encoding="utf-8"))
    assert Range.parse(depends["range"]).contains(Version.parse(custom["version"])), "the shipped custom-themes satisfies the range"
    assert boot_mods_from_manifests([("rimuru-branding", RIMURU, manifest)]) == []
    assert {p["kind"] for p in custom["parts"]} == {"ui", "python-hook", "spa", "patch"}


def test_custom_themes_boot_and_reset_guard(tmp_path: Path):
    manifest = json.loads((CUSTOM_THEMES / "floofy.json").read_text(encoding="utf-8"))
    boot_mods = boot_mods_from_manifests([("custom-themes", CUSTOM_THEMES, manifest)])
    assert len(boot_mods) == 1 and "when" not in boot_mods[0].boot and "css" not in boot_mods[0].boot, "generic: every custom theme, the sheet is written by the hook"
    descriptor = build_boot_descriptor(boot_mods, host_home=tmp_path)
    assert descriptor is not None and [op.op for op in descriptor.ops] == ["insert-before", "insert-before"] and descriptor.ui_assets == ()
    script = descriptor.ops[1].content
    assert "/apps/floofycrew/ui/boot/custom-themes/themes.css" in script and "document.write(" in script
    guard = PatchDescriptor.load(CUSTOM_THEMES / "patches" / "theme-reset-guard.json")
    assert guard.is_import_map and guard.target_is_glob and guard.find == "mc-custom-theme-"
    assert guard.applies_to is not None and guard.applicability("0.7.0.5") is None and guard.applicability("0.9.0") is not None
    sample = "...e.installed)){U(e);return}f.has(e)||U(Y)}},[k,i,f,u,U]);let mc-custom-theme-"
    result = apply_descriptor(sample, guard, "0.7.0.5")
    assert result.applied and "f.has(e)||void 0}},[" in result.text and "U(Y)" not in result.text
    assert set(apply_descriptor(result.text, guard, "0.7.0.5").codes()) == {"AlreadyApplied"}


@pytest.mark.skipif(not SCRATCH_ASSETS.is_dir(), reason="no payload copy under .scratch")
def test_reset_guard_matches_the_0_7_0_5_chunk_exactly_once():
    guard = PatchDescriptor.load(CUSTOM_THEMES / "patches" / "theme-reset-guard.json")
    chunks = sorted(SCRATCH_ASSETS.glob("useTheme-*.js"))
    assert len(chunks) == 1
    text = chunks[0].read_text(encoding="utf-8")
    result = apply_descriptor(text, guard, "0.7.0.5")
    assert [a.op_index for a in result.applied] == [0] and not result.skipped, result.diagnostics()
    assert len(re.findall(r"\|\|void 0\}\},\[", result.text)) == 1
