"""Module patches through the import map (Requirement 4.4; design spike 1.4) on the 0.7.0.5 fixture dist."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from floofy_core.deploy import DeployManifest, sha256_file
from floofy_core.patcher import Patcher, PlannedPatch
from floofy_core.patches import PatchDescriptor, PatchDescriptorError, apply_descriptor, fingerprint_report, find_import_map
from floofy_core.payloads import make_payload
from floofy_core.spa_patches import PATCHED_DIR, is_entry_chunk, patched_url, rebase_imports, resolve_chunk_target, sidecar_for

from floofy_testing import REPO_ROOT

FIXTURE = REPO_ROOT / "floofy-core" / "tests" / "fixtures" / "dist-0.7.0.5"
SEED = "TunnelQrCard-C7TY_tEZ.js"
PRELOADED = "useIsMobile-CVmE8oC-.js"


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def make_fixture_payload(root: Path, name: str = "payload"):
    record = json.loads((FIXTURE / "FIXTURE.json").read_text(encoding="utf-8"))
    package_dir = root / name / "kiro_crew"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    (package_dir / "BUILD_VERSION").write_text(record["sourceVersion"], encoding="utf-8")
    shutil.copytree(FIXTURE, package_dir / "static" / "dist", ignore=shutil.ignore_patterns("FIXTURE.json"))
    # give one preloaded stub real content so a preload-dropping remap can be exercised
    (package_dir / "static" / "dist" / "assets" / PRELOADED).write_text('import{a}from"./vendor-react-CDpZySCs.js";export const isMobile=()=>false;\n', encoding="utf-8")
    return make_payload(kind="fixture", root=root / name, package_dir=package_dir, source="fixture", current=True, edition="internal", discriminator=name)


def qr_descriptor(**overrides) -> PatchDescriptor:
    document = {
        "schema": 1,
        "target": "kiro_crew/static/dist/assets/TunnelQrCard-*.js",
        "mode": "import-map",
        "find": "Phone access code",
        "appliesTo": ">=0.7.0 <0.9.0",
        "fromBuild": "0.7.0.1",
        "ops": [
            {"op": "replace", "fingerprint": r'"aria-label":`Phone access code`', "regex": True, "content": '"aria-label":`Phone access code (floofy)`', "marker": "Phone access code (floofy)"},
        ],
    }
    document.update(overrides)
    return PatchDescriptor.from_dict(document, source="qr")


def test_rebase_imports_covers_static_dynamic_and_reexport_forms():
    text = 'import{o as e}from"./a-11111111.js";import"./side-22222222.js";export{x}from"./b-33333333.js";const p=import("./lazy-44444444.js");import * as ns from \'./c-55555555.js\';const s="./not-an-import.js";'
    rebased = rebase_imports(text)
    assert 'from"/assets/a-11111111.js"' in rebased
    assert 'import"/assets/side-22222222.js"' in rebased
    assert 'export{x}from"/assets/b-33333333.js"' in rebased
    assert 'import("/assets/lazy-44444444.js")' in rebased
    assert "from '/assets/c-55555555.js'" in rebased
    assert 'const s="./not-an-import.js"' in rebased, "a plain string is not a specifier"
    assert rebase_imports(rebased) == rebased, "idempotent"


def test_resolve_chunk_target_needs_exactly_one_match(tmp_path: Path):
    payload = make_fixture_payload(tmp_path)
    resolved, skip = resolve_chunk_target(payload, "kiro_crew/static/dist/assets/TunnelQrCard-*.js")
    assert resolved is not None and resolved.name == SEED and skip is None
    missing, skip = resolve_chunk_target(payload, "kiro_crew/static/dist/assets/Nowhere-*.js")
    assert missing is None and skip is not None and skip.code == "FingerprintMiss"
    many, skip = resolve_chunk_target(payload, "kiro_crew/static/dist/assets/Tunnel*.js")
    assert many is None and skip is not None and skip.code == "FingerprintAmbiguous" and "4 files" in skip.message
    literal, skip = resolve_chunk_target(payload, f"kiro_crew/static/dist/assets/{SEED}")
    assert literal is not None and skip is None


def test_is_entry_chunk_reads_script_src_only():
    shell = (FIXTURE / "index.html").read_text(encoding="utf-8")
    assert is_entry_chunk(shell, "main-qB7mk2ul.js")
    assert not is_entry_chunk(shell, PRELOADED), "a modulepreload hint is not a <script src>"
    assert not is_entry_chunk(shell, SEED)


def test_schema_accepts_mode_and_find_and_rejects_unknown_mode():
    descriptor = qr_descriptor()
    assert descriptor.is_import_map and descriptor.target_is_glob and descriptor.find == "Phone access code"
    with pytest.raises(PatchDescriptorError):
        qr_descriptor(mode="blob-url")
    with pytest.raises(PatchDescriptorError):
        qr_descriptor(find="")


def test_find_gates_apply_and_the_report():
    descriptor = qr_descriptor(find="not in this module")
    text = 'x "aria-label":`Phone access code` y'
    result = apply_descriptor(text, descriptor, "0.7.0.5")
    assert not result.applied and result.codes() == ["FingerprintMiss"] and "find literal" in result.skipped[0].message
    report = fingerprint_report(text, [descriptor], "0.7.0.5")
    assert report.missed and report.details[report.missed[0]] == "find-miss"
    assert apply_descriptor(text, qr_descriptor(), "0.7.0.5").applied


def test_import_map_patch_writes_a_copy_and_remaps_the_shell_idempotently(tmp_path: Path):
    payload = make_fixture_payload(tmp_path)
    home = tmp_path / "home"
    ui_root = home / "apps" / "floofycrew" / "ui"
    before = snapshot(payload.root)
    original_chunk = (payload.assets_dir / SEED).read_bytes()
    preload = PatchDescriptor.from_dict(
        {"schema": 1, "target": f"kiro_crew/static/dist/assets/{PRELOADED}", "mode": "import-map", "ops": [{"op": "replace", "fingerprint": "=>false", "content": "=>window.__floofyMobile===true", "marker": "__floofyMobile"}]},
        source="preload",
    )
    patcher = Patcher(home / "floofy", [payload], host_home=home, endpoints=[], triggers_installed=True)
    plan = [PlannedPatch("qr-mod", "0", qr_descriptor()), PlannedPatch("mobile-mod", "0", preload)]
    report = patcher.apply(plan, verify=False)
    result = report.payloads[0]
    assert result.ok, result.errors
    assert not [s for s in result.skipped if s["code"] not in ("AlreadyApplied",)], result.skipped

    copy = ui_root / PATCHED_DIR / SEED
    assert copy.is_file() and str(copy) in result.written
    text = copy.read_text(encoding="utf-8")
    assert text.startswith("/* floofy-patched: qr-mod#0 on TunnelQrCard-C7TY_tEZ.js (host 0.7.0.5) */\n")
    assert "Phone access code (floofy)" in text and 'from"/assets/rolldown-runtime-C0FnF6B9.js"' in text and 'from"./' not in text
    assert (payload.assets_dir / SEED).read_bytes() == original_chunk, "the original chunk is never modified"
    sidecar = json.loads(sidecar_for(copy).read_text(encoding="utf-8"))
    assert sidecar["mod"] == "qr-mod" and sidecar["patched"] == patched_url(SEED) and sidecar["ops"][0]["regex"] is True and sidecar["applied"] == [0]
    index = json.loads((ui_root / PATCHED_DIR / "index.json").read_text(encoding="utf-8"))
    assert {e["chunk"] for e in index["patches"]} == {SEED, PRELOADED}

    shell = payload.index_html.read_text(encoding="utf-8")
    _match, import_map = find_import_map(shell)
    assert import_map["imports"][f"/assets/{SEED}"] == patched_url(SEED)
    assert import_map["imports"][f"/assets/{PRELOADED}"] == patched_url(PRELOADED)
    assert import_map["imports"]["react"] == "/vendor/react.mjs", "the host's own keys are kept"
    assert f'href="/assets/{PRELOADED}"' not in shell, "the remapped chunk's modulepreload hint is dropped"
    assert f"/assets/{SEED}" not in shell.replace(patched_url(SEED), "").replace(f'"/assets/{SEED}"', ""), "the lazy chunk was never preloaded"
    manifest = DeployManifest.load(patcher.manifest_path(payload))
    assert {Path(a.path).name for a in manifest.added} == {SEED, SEED + ".floofy.json", PRELOADED, PRELOADED + ".floofy.json"}
    assert manifest.find_file(str(payload.index_html)) is not None and "qr-mod" in manifest.find_file(str(payload.index_html)).mod
    assert any("index.html#importMap" in a for a in result.applied)

    # idempotent: a second apply changes no byte and writes nothing new
    after_first = snapshot(payload.root) | {f"ui/{k}": v for k, v in snapshot(ui_root).items() if not k.endswith("index.json")}
    second = patcher.apply(plan, verify=False).payloads[0]
    assert second.ok and second.written == [] and second.restored == []
    after_second = snapshot(payload.root) | {f"ui/{k}": v for k, v in snapshot(ui_root).items() if not k.endswith("index.json")}
    assert after_first == after_second

    # dropping a mod from the set retires its copy; restore --all returns everything
    third = patcher.apply([PlannedPatch("qr-mod", "0", qr_descriptor())], verify=False).payloads[0]
    assert third.ok and not (ui_root / PATCHED_DIR / PRELOADED).exists() and copy.exists()
    _m, imports = find_import_map(payload.index_html.read_text(encoding="utf-8"))
    assert f"/assets/{PRELOADED}" not in imports["imports"] and f'href="/assets/{PRELOADED}"' in payload.index_html.read_text(encoding="utf-8")
    restore = patcher.restore()
    assert snapshot(payload.root) == before, "byte-identical after restore"
    assert not copy.exists() and not sidecar_for(copy).exists() and not (ui_root / PATCHED_DIR / "index.json").exists()
    assert not patcher.manifest_path(payload).exists() and restore.payloads[payload.id]["restored"]


def test_lost_manifest_restore_all_still_sweeps_the_ui_copies(tmp_path: Path):
    payload = make_fixture_payload(tmp_path)
    home = tmp_path / "home"
    ui_root = home / "apps" / "floofycrew" / "ui"
    before = snapshot(payload.root)
    patcher = Patcher(home / "floofy", [payload], host_home=home, endpoints=[], triggers_installed=True)
    assert patcher.apply([PlannedPatch("qr-mod", "0", qr_descriptor())], verify=False).ok
    patcher.manifest_path(payload).unlink()
    patcher.restore()
    assert snapshot(payload.root) == before
    assert not list((ui_root / PATCHED_DIR).glob("*")) if (ui_root / PATCHED_DIR).exists() else True


def test_restore_of_one_payload_keeps_a_copy_another_payload_still_maps_to(tmp_path: Path):
    first = make_fixture_payload(tmp_path, "first")
    second = make_fixture_payload(tmp_path, "second")
    assert first.id != second.id
    home = tmp_path / "home"
    ui_root = home / "apps" / "floofycrew" / "ui"
    patcher = Patcher(home / "floofy", [first, second], host_home=home, endpoints=[], triggers_installed=True)
    assert patcher.apply([PlannedPatch("qr-mod", "0", qr_descriptor())], verify=False).ok
    copy = ui_root / PATCHED_DIR / SEED
    assert copy.is_file()
    patcher.restore([first.id])
    assert copy.is_file(), "the second payload's import map still points at it"
    assert f"/assets/{SEED}" not in first.index_html.read_text(encoding="utf-8") or patched_url(SEED) not in first.index_html.read_text(encoding="utf-8")
    assert patched_url(SEED) in second.index_html.read_text(encoding="utf-8")
    patcher.restore([second.id])
    assert not copy.exists()


def test_entry_chunk_falls_back_to_the_alias_graph(tmp_path: Path):
    payload = make_fixture_payload(tmp_path)
    home = tmp_path / "home"
    ui_root = home / "apps" / "floofycrew" / "ui"
    main = payload.assets_dir / "main-qB7mk2ul.js"
    main.write_text('import"./TunnelPage-BmQOR6XD.js";const entry="main";\n', encoding="utf-8")
    descriptor = PatchDescriptor.from_dict(
        {"schema": 1, "target": "kiro_crew/static/dist/assets/main-*.js", "mode": "import-map", "ops": [{"op": "replace", "fingerprint": 'const entry="main"', "content": 'const entry="main-floofy"', "marker": "main-floofy"}]},
        source="entry",
    )
    patcher = Patcher(home / "floofy", [payload], host_home=home, endpoints=[], triggers_installed=True)
    result = patcher.apply([PlannedPatch("entry-mod", "0", descriptor)], verify=False).payloads[0]
    assert result.ok, result.errors
    assert any("alias-graph fallback" in a for a in result.applied)
    assert not (ui_root / PATCHED_DIR / "main-qB7mk2ul.js").exists()
    assert result.alias_graph is not None and any(p.name.startswith("main-qB7mk2ul-") and p.name.endswith("-floofy.js") for p in payload.assets_dir.iterdir())
    shell = payload.index_html.read_text(encoding="utf-8")
    assert "main-qB7mk2ul-" in shell and "-floofy.js" in shell
    _m, imports = find_import_map(shell)
    assert "/assets/main-qB7mk2ul.js" not in imports["imports"]
    assert "main-floofy" in main.read_text(encoding="utf-8")
    patcher.restore()
    assert not any(p.name.endswith("-floofy.js") for p in payload.assets_dir.iterdir())


def test_gc_drops_orphaned_ui_copies(tmp_path: Path):
    payload = make_fixture_payload(tmp_path)
    home = tmp_path / "home"
    ui_root = home / "apps" / "floofycrew" / "ui"
    patcher = Patcher(home / "floofy", [payload], host_home=home, endpoints=[], triggers_installed=True)
    assert patcher.apply([PlannedPatch("qr-mod", "0", qr_descriptor())], verify=False).ok
    orphan = ui_root / PATCHED_DIR / "Orphan-DEADBEEF.js"
    orphan.write_text("// left behind\n", encoding="utf-8")
    report = patcher.gc()
    assert str(orphan) in report.removed and (ui_root / PATCHED_DIR / SEED).is_file()
    assert (ui_root / PATCHED_DIR / "index.json").is_file()
