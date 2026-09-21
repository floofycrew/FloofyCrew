"""The generated index.html descriptor: loader tag + boot activation (Requirement 4.3; design spikes 1.3/1.4)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from floofy_core.boot_script import (
    BOOT_CSS_ID_PREFIX,
    BOOT_SCRIPT_ID,
    LOADER_TAG_ID,
    BootMod,
    BootScriptError,
    boot_mods_from_manifests,
    boot_template,
    build_boot_descriptor,
    read_theme_defaults,
    render_boot_script,
)
from floofy_core.deploy import sha256_file
from floofy_core.patcher import Patcher, PlannedPatch
from floofy_core.patches import apply_descriptor
from floofy_core.payloads import make_payload

from floofy_testing import REPO_ROOT

FIXTURES = sorted(p for p in (REPO_ROOT / "floofy-core" / "tests" / "fixtures").glob("dist-*") if (p / "FIXTURE.json").is_file())


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def write_boot_mod(root: Path, mod_id: str, *, css: str | None = "[data-theme='custom-x-dark']{--bg:#111}", hook: str = "ctx.element.setAttribute('data-floofy-' + ctx.id, ctx.color);", favicon: bool = True, when: str | None = None, attributes: dict | None = None) -> tuple[str, Path, dict]:
    mod_dir = root / mod_id
    (mod_dir / "spa").mkdir(parents=True)
    (mod_dir / "spa" / "boot.js").write_text(hook, encoding="utf-8")
    boot: dict = {}
    if css is not None:
        (mod_dir / "spa" / "boot.css").write_text(css, encoding="utf-8")
        boot["css"] = "spa/boot.css"
    if favicon:
        (mod_dir / "spa" / "favicon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + mod_id.encode())
        boot["favicon"] = "spa/favicon.png"
        (mod_dir / "spa" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        boot["logo"] = "spa/logo.svg"
        boot["title"] = f"{mod_id} title"
    if when:
        boot["when"] = {"theme": when}
    if attributes:
        boot["attributes"] = attributes
    manifest = {"id": mod_id, "parts": [{"kind": "spa", "side": "spa", "path": "spa/boot.js", "activation": "boot", "boot": boot}, {"kind": "spa", "side": "spa", "path": "spa/main.js"}]}
    (mod_dir / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    return mod_id, mod_dir, manifest


@pytest.fixture(params=FIXTURES, ids=[p.name for p in FIXTURES])
def fixture_payload(request: pytest.FixtureRequest, tmp_path: Path):
    fixture: Path = request.param
    record = json.loads((fixture / "FIXTURE.json").read_text(encoding="utf-8"))
    package_dir = tmp_path / "payload" / "kiro_crew"
    package_dir.mkdir(parents=True)
    version = record["sourceVersion"]
    (package_dir / "__init__.py").write_text(f'__version__ = "{version.rsplit(".", 1)[0] if version.count(".") == 3 else version}"\n', encoding="utf-8")
    if version.count(".") == 3:
        (package_dir / "BUILD_VERSION").write_text(version, encoding="utf-8")
    shutil.copytree(fixture, package_dir / "static" / "dist", ignore=shutil.ignore_patterns("FIXTURE.json"))
    return make_payload(kind="fixture", root=tmp_path / "payload", package_dir=package_dir, source="fixture", current=True, edition=record["edition"])


def test_template_is_es5_inline_safe():
    template = boot_template()
    assert template.startswith("function floofyBoot(config)")
    assert "`" not in template and "</script" not in template.lower()
    assert "mc-color-theme" in template and "data-theme" in template and "MutationObserver" in template


def test_theme_defaults_come_from_the_host_config(tmp_path: Path):
    assert read_theme_defaults(tmp_path) == {"color": "kiro", "mode": "system"}
    (tmp_path / "config.json").write_text(json.dumps({"dashboard": {"theme_color": "custom-rimuru", "theme_mode": "light"}}), encoding="utf-8")
    assert read_theme_defaults(tmp_path) == {"color": "custom-rimuru", "mode": "light"}
    (tmp_path / "config.json").write_text(json.dumps({"dashboard": {"theme_color": "bad value!", "theme_mode": "sideways"}}), encoding="utf-8")
    assert read_theme_defaults(tmp_path) == {"color": "kiro", "mode": "system"}


def test_render_composes_mods_in_order_and_escapes_the_config(tmp_path: Path):
    mods = boot_mods_from_manifests([write_boot_mod(tmp_path, "alpha", when="custom-alpha"), write_boot_mod(tmp_path, "beta", favicon=False, attributes={"data-beta": "<yes>"})])
    assert [m.mod_id for m in mods] == ["alpha", "beta"]
    script, assets = render_boot_script(mods, {"color": "custom-alpha", "mode": "dark"})
    assert script.startswith(f'<script id="{BOOT_SCRIPT_ID}">') and script.count("<script") == 1 and script.count("</script>") == 1
    assert script.index('"id":"alpha"') < script.index('"id":"beta"')
    assert '"favicon":"/apps/floofycrew/ui/boot/alpha/favicon.png"' in script and '"logo":"/apps/floofycrew/ui/boot/alpha/logo.svg"' in script
    assert '"when":{"theme":"custom-alpha"}' in script and '"attributes":{"data-beta":"\\u003cyes>"}' in script, "< is escaped so </script can never appear"
    assert '"hook":function(ctx){' in script and "data-floofy-" in script
    assert [(a.dest, a.mod) for a in assets] == [("boot/alpha/favicon.png", "alpha"), ("boot/alpha/logo.svg", "alpha")]
    assert '"defaults":{"color":"custom-alpha","mode":"dark"}' in script


def test_render_refuses_unsafe_or_oversized_parts(tmp_path: Path):
    _id, mod_dir, manifest = write_boot_mod(tmp_path, "evil", hook="x = '</script><script>alert(1)'")
    with pytest.raises(BootScriptError, match="closing script tag"):
        render_boot_script(boot_mods_from_manifests([("evil", mod_dir, manifest)]), {"color": "kiro", "mode": "system"})
    _id, mod_dir, manifest = write_boot_mod(tmp_path, "big", hook="x" * (32 * 1024 + 1))
    with pytest.raises(BootScriptError, match="cap"):
        render_boot_script(boot_mods_from_manifests([("big", mod_dir, manifest)]), {"color": "kiro", "mode": "system"})
    _id, mod_dir, manifest = write_boot_mod(tmp_path, "escape", favicon=False)
    manifest["parts"][0]["boot"]["favicon"] = "../outside.png"
    with pytest.raises(BootScriptError, match="inside the mod"):
        render_boot_script(boot_mods_from_manifests([("escape", mod_dir, manifest)]), {"color": "kiro", "mode": "system"})
    _id, mod_dir, manifest = write_boot_mod(tmp_path, "badcss", css="body{}</style><script>alert(1)</script>")
    with pytest.raises(BootScriptError, match="closing style tag"):
        build_boot_descriptor(boot_mods_from_manifests([("badcss", mod_dir, manifest)]), host_home=tmp_path)


def test_descriptor_is_none_without_anything_to_inject(tmp_path: Path):
    assert build_boot_descriptor([], host_home=tmp_path, loader_tag=False) is None
    tag_only = build_boot_descriptor([], host_home=tmp_path, loader_tag=True)
    assert tag_only is not None and len(tag_only.ops) == 1 and tag_only.ops[0].marker == f'id="{LOADER_TAG_ID}"'


def test_descriptor_applies_to_every_fixture_shell_idempotently_and_restores_byte_identical(fixture_payload, tmp_path: Path):
    payload = fixture_payload
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.json").write_text(json.dumps({"dashboard": {"theme_color": "custom-alpha", "theme_mode": "dark"}}), encoding="utf-8")
    ui_root = home / "apps" / "floofycrew" / "ui"
    mods = boot_mods_from_manifests([write_boot_mod(tmp_path / "mods", "alpha"), write_boot_mod(tmp_path / "mods", "beta", favicon=False, css="[data-theme='custom-beta-dark']{--bg:#222}")])
    descriptor = build_boot_descriptor(mods, host_home=home)
    assert descriptor is not None and [op.op for op in descriptor.ops] == ["insert-before", "insert-before", "append-head", "append-head"]
    before = snapshot(payload.root)
    original_shell = payload.index_html.read_text(encoding="utf-8")

    # pure application first: placement and order
    result = apply_descriptor(original_shell, descriptor, payload.host_version)
    assert not result.skipped, result.diagnostics()
    shell = result.text
    tag_at = shell.index(f'id="{LOADER_TAG_ID}"')
    main_at = re.search(r'<script type="module"[^>]*src="/assets/main-', shell).start()
    assert tag_at < main_at, "the loader tag precedes the host's module entry"
    boot_at = shell.index(f'<script id="{BOOT_SCRIPT_ID}">')
    first_host_script = original_shell.index("<script")
    assert boot_at == first_host_script, "the boot script sits exactly where the first host <script was"
    assert shell.index(f'id="{BOOT_CSS_ID_PREFIX}alpha"') < shell.index(f'id="{BOOT_CSS_ID_PREFIX}beta"') < shell.index("</head>")
    assert shell.count(f'<script id="{BOOT_SCRIPT_ID}">') == 1
    again = apply_descriptor(shell, descriptor, payload.host_version)
    assert again.text == shell and set(again.codes()) == {"AlreadyApplied"}, "idempotent by marker"

    # through the Patcher: assets land under ui/boot, manifest records them, restore is byte-identical
    patcher = Patcher(home / "floofy", [payload], host_home=home, endpoints=[], triggers_installed=True)
    report = patcher.apply([PlannedPatch("floofycrew", "boot", descriptor)], verify=False)
    assert report.ok, report.payloads[0].errors
    assert (ui_root / "boot" / "alpha" / "favicon.png").is_file() and (ui_root / "boot" / "alpha" / "logo.svg").is_file()
    written_shell = payload.index_html.read_text(encoding="utf-8")
    assert f'id="{LOADER_TAG_ID}"' in written_shell and '"favicon":"/apps/floofycrew/ui/boot/alpha/favicon.png"' in written_shell
    second = patcher.apply([PlannedPatch("floofycrew", "boot", descriptor)], verify=False).payloads[0]
    assert second.ok and second.written == []
    from floofy_core.deploy import DeployManifest

    manifest = DeployManifest.load(patcher.manifest_path(payload))
    assert {Path(a.path).name for a in manifest.added} == {"favicon.png", "logo.svg"}
    patcher.restore()
    assert snapshot(payload.root) == before
    assert not (ui_root / "boot").exists() or not any((ui_root / "boot").rglob("*"))


def test_boot_mod_objects_expose_boot_and_label(tmp_path: Path):
    mod_id, mod_dir, manifest = write_boot_mod(tmp_path, "gamma", favicon=False)
    [mod] = boot_mods_from_manifests([(mod_id, mod_dir, manifest)])
    assert isinstance(mod, BootMod) and mod.label == "gamma#0" and mod.boot["css"] == "spa/boot.css"
    assert boot_mods_from_manifests([("plain", tmp_path, {"parts": [{"kind": "spa", "path": "x.js"}]})]) == []



# --- the template ships inside the package, so the installed zipapp `floofy` has it -----------------------------------


def test_the_package_data_template_is_the_spa_host_boot_module_byte_for_byte():
    """``floofy_core/boot.mjs`` is a copy of ``spa-host/src/boot.mjs`` (the SPA host owns it; regenerate the copy, never edit it)."""
    package_copy = REPO_ROOT / "floofy-core" / "floofy_core" / "boot.mjs"
    source = REPO_ROOT / "spa-host" / "src" / "boot.mjs"
    assert package_copy.is_file() and package_copy.read_bytes() == source.read_bytes()


def test_the_template_is_found_without_any_filesystem_neighbour(monkeypatch: pytest.MonkeyPatch):
    """Found on the test box: ``floofy apply`` from ``~/.local/lib/floofycrew/floofy.pyz`` warned "boot.mjs template not
    found" and dropped the boot part, because the lookup was relative to ``__file__`` (a checkout's ``spa-host/src`` or
    the Loader app's ``ui/``), neither of which exists beside the installed zipapp. The package-data copy is read first."""
    from floofy_core import boot_script

    monkeypatch.setattr(boot_script, "_template_path", lambda: None)
    body = boot_template()
    assert body.startswith("function floofyBoot(") and "</script" not in body.lower()


def test_the_zipapp_floofy_renders_the_boot_part(tmp_path: Path):
    """End to end: build the zipapp into a scratch directory with no checkout around it and render a boot descriptor from it."""
    import subprocess
    import sys

    out = tmp_path / "elsewhere" / "floofy.pyz"
    out.parent.mkdir()
    build = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_zipapp.py"), "--out", str(out)], capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    probe = "import floofy_core.boot_script as b; body = b.boot_template(); print(body.splitlines()[0]); print(b._template_text()[1])"
    run = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(out)!r}); {probe}"], capture_output=True, text=True, check=False, cwd=str(tmp_path))
    assert run.returncode == 0, run.stderr
    first, origin = run.stdout.strip().splitlines()
    assert first.startswith("function floofyBoot(") and origin == "floofy_core/boot.mjs", "the zipapp read its own package data, not a checkout"
