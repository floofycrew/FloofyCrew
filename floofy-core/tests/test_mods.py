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
    # the host fixed the persisting reset on public 0.8.0.dev20260928053132 (useTheme only falls back visually
    # when a pack's detail is missing): the descriptor stops there instead of missing its fingerprint
    assert str(guard.to_build) == "0.8.0.dev20260928053132"
    for inside in ("0.7.0rc5", "0.7.0", "0.7.0.8", "0.8.0.dev20260924061032"):
        assert guard.applicability(inside) is None, inside
    assert guard.applicability("0.8.0.dev20260928053132") == "host 0.8.0.dev20260928053132 is not below toBuild 0.8.0.dev20260928053132"
    assert guard.applicability("0.8.0rc1") is not None  # insider 0.8.0rc1 carries the same useTheme (Forge repair, round 1)
    assert guard.applicability("0.8.0") is not None
    # public-nightly 0.9.0.dev20260929061324 (Forge repair, round 1): the next minor's nightlies carry the fixed useTheme
    # unchanged — the gate (and the >=0.7.0 <0.9.0 range) keep the guard NotApplicable, never a FingerprintMiss
    assert guard.applicability("0.9.0.dev20260929061324") is not None
    new_site = "let W=Ue(i),G=z&&W!==null&&R.some(e=>e.value===i)&&!f.has(W);(0,h.useEffect)(()=>{Te(G?X:i,c,e),_()},[c,i,e,_,G]) mc-custom-theme-"
    gated = apply_descriptor(new_site, guard, "0.8.0.dev20260928053132")
    assert not gated.applied and set(gated.codes()) == {"NotApplicable"} and gated.text == new_site
    # the remaining reset on the new useTheme (`R.some(e=>e.value===i)||q(X)`) fires only for a pack absent from the
    # installed list — a legitimate reset the guard must not neutralise, so it is not re-anchored as the guard's site
    remaining = "...e.installed)){q(e);return}R.some(e=>e.value===i)||q(X)}},[z,i,R,q]);let mc-custom-theme-"
    gated = apply_descriptor(remaining, guard, "0.9.0.dev20260929061324")
    assert not gated.applied and set(gated.codes()) == {"NotApplicable"} and gated.text == remaining
    # public-nightly 0.9.0.dev20260930061058 (Forge repair, round 1): useTheme-C392CemR.js moved the same effect again
    # (a `custom-` branch ahead of the installed-list check); still the legitimate reset, still gated, never re-anchored
    remaining_0930 = "...e.installed)){U(e);return}I.some(e=>e.value===r)||U(J)}},[L,r,I,U]);let mc-custom-theme-"
    assert guard.applicability("0.9.0.dev20260930061058") is not None
    gated = apply_descriptor(remaining_0930, guard, "0.9.0.dev20260930061058")
    assert not gated.applied and set(gated.codes()) == {"NotApplicable"} and gated.text == remaining_0930
    # internal-beta 0.8.0.6 (Forge repair, round 1): the first internal build past the gate — a four-component
    # version orders after the 0.8.0 pre-releases; useTheme-D1uD8za3.js carries the same query-hook shape and the
    # same legitimate reset (`L.some(e=>e.value===a)||G(J)`), so the guard is gated there too and never re-anchored
    assert guard.applicability("0.8.0.6") == "host 0.8.0.6 is not below toBuild 0.8.0.dev20260928053132"
    remaining_internal = "...e.installed)){G(e);return}L.some(e=>e.value===a)||G(J)}},[R,a,L,G]);let mc-custom-theme-"
    gated = apply_descriptor(remaining_internal, guard, "0.8.0.6")
    assert not gated.applied and set(gated.codes()) == {"NotApplicable"} and gated.text == remaining_internal
    # internal-beta 0.10.0.2 (Forge repair, round 1): useTheme-B40pc58T.js is the same shape again — the visual-only
    # fallback (`installedThemeLoadFailed`) plus the one legitimate reset (`L.some(e=>e.value===o)||G(J)`). Here the
    # `>=0.7.0 <0.9.0` range already excludes the guard (its base 0.10.0 orders numerically past 0.9.0, never lexically
    # between 0.1 and 0.2); either gate yields NotApplicable, never a FingerprintMiss
    assert guard.applicability("0.10.0.2") == "host base 0.10.0 is outside appliesTo >=0.7.0 <0.9.0"
    remaining_0_10 = "...e.installed)){G(e);return}L.some(e=>e.value===o)||G(J)}},[R,o,L,G]);let mc-custom-theme-"
    gated = apply_descriptor(remaining_0_10, guard, "0.10.0.2")
    assert not gated.applied and set(gated.codes()) == {"NotApplicable"} and gated.text == remaining_0_10
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
