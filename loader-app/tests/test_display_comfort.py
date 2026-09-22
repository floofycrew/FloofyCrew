"""The shipped ``mods/display-comfort`` mod: validate clean, install → enable → active, the config round trip, and the first-frame boot descriptor.

Fake payload, scratch data home, the real runtime and the real CLI in-process;
the browser side (sliders, live preview, the runtime part's style element) is a
pure-frontend concern the source-hygiene assertions pin here.
"""
from __future__ import annotations

import json
from pathlib import Path

from floofy_core import __version__ as FRAMEWORK_VERSION
from floofy_core.boot_script import MAX_BOOT_CSS_BYTES, MAX_BOOT_HOOK_BYTES, boot_mods_from_manifests, build_boot_descriptor
from floofy_core.modstore import code_kinds_of
from floofy_core.semver import Range, Version
from floofy_core.validator import validate_mod
from floofy_loader.routes import config_response, ui_file_response

from floofy_testing import REPO_ROOT
from test_app_routes import Home, homes  # noqa: F401 - the fixture

MOD_DIR = REPO_ROOT / "mods" / "display-comfort"
MANIFEST = json.loads((MOD_DIR / "floofy.json").read_text(encoding="utf-8"))


def test_the_mod_is_valid_and_declares_what_it_needs():
    report = validate_mod(MOD_DIR)
    assert report.ok and report.warnings == [], report.format()
    kinds = [p["kind"] for p in MANIFEST["parts"]]
    assert kinds == ["ui", "spa", "spa"], "a settings page, the runtime applier, and the first-frame hook"
    ui = MANIFEST["parts"][0]
    assert ui["entry"] == "ui/page.mjs" and ui["title"] == "Display comfort" and ui["icon"] == "ui/icon.svg"
    runtime = MANIFEST["parts"][1]
    assert runtime["activation"] == "runtime" and runtime["path"] == "spa/runtime.mjs"
    boot = MANIFEST["parts"][2]
    assert boot["activation"] == "boot" and boot["boot"] == {"css": "spa/boot.css"}, "the bubble rules are baked so the cached scale paints on the first frame"
    assert code_kinds_of(MANIFEST) == {"spa"}, "no python-hook: the config route is the Loader's own"
    assert MANIFEST["network"] == {"hosts": [], "credentials": False}
    assert Range.parse(MANIFEST["dependsOn"]["floofycrew"]).contains(Version.parse(FRAMEWORK_VERSION))
    assert (MOD_DIR / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    listed = {f["path"] for f in MANIFEST["files"]}
    assert listed == {"README.md", "LICENSE", "spa/boot.css", "spa/boot.js", "spa/runtime.mjs", "ui/icon.svg", "ui/page.mjs"}


def test_the_sources_keep_the_mod_page_rules():
    page = (MOD_DIR / "ui" / "page.mjs").read_text(encoding="utf-8")
    assert "api.config.get()" in page and "api.config.set(" in page and "export default async function mount(container, api)" in page
    assert "floofy-display-comfort-changed" in page, "the runtime part is told about every change"
    for source_name in ("ui/page.mjs", "spa/runtime.mjs", "spa/boot.js"):
        text = (MOD_DIR / source_name).read_text(encoding="utf-8")
        assert "http://" not in text and "ws://" not in text, f"{source_name}: no plaintext URL — same-origin only"
        for popup in ("window.confirm", "window.prompt", "window.alert"):
            assert popup not in text, f"{source_name}: browser pop-ups do not work in the desktop app"


def test_the_boot_hook_and_css_fit_the_inline_constraints():
    hook = (MOD_DIR / "spa" / "boot.js").read_text(encoding="utf-8")
    assert "`" not in hook and "=>" not in hook and "</script" not in hook.lower(), "inlined into index.html: ES5 only, no closing script tag"
    assert len(hook.encode("utf-8")) <= MAX_BOOT_HOOK_BYTES
    assert "localStorage.getItem('floofy-display-comfort')" in hook and "--floofy-dc-chat-scale" in hook
    css = (MOD_DIR / "spa" / "boot.css").read_text(encoding="utf-8")
    assert "</style" not in css.lower() and len(css.encode("utf-8")) <= MAX_BOOT_CSS_BYTES
    assert '[data-testid="message-bubble"]' in css and "var(--floofy-dc-chat-scale, 1)" in css
    runtime = (MOD_DIR / "spa" / "runtime.mjs").read_text(encoding="utf-8")
    for rule_marker in ('[data-testid="message-bubble"]', 'html[data-floofy-dc-composer="on"] [data-testid="chat-footer"]'):
        assert rule_marker in runtime, "the runtime holds the same rules for a home the Patcher has not visited yet"


def test_boot_descriptor_bakes_the_css_and_the_hook(tmp_path: Path):
    boot_mods = boot_mods_from_manifests([("display-comfort", MOD_DIR, MANIFEST)])
    assert len(boot_mods) == 1
    descriptor = build_boot_descriptor(boot_mods, host_home=tmp_path, defaults={"color": "kiro", "mode": "system"})
    assert [op.op for op in descriptor.ops] == ["insert-before", "insert-before", "append-head"], "loader tag + boot script + baked css before </head>"
    script = descriptor.ops[1].content
    assert "floofy-display-comfort" in script and "data-floofy-dc-composer" in script
    baked = descriptor.ops[2].content
    assert baked.startswith('<style id="floofy-boot-css-display-comfort">') and 'zoom: var(--floofy-dc-chat-scale, 1)' in baked


def test_install_enable_config_round_trip(homes: Path):
    home = Home(homes / "h", consent=True)
    code, result, _ = home.cli("--yes", "install", str(MOD_DIR), "--now")
    assert code == 0, result
    assert result.get("enabled") is True, "a confirmed install lands enabled"
    home.runtime.reload()
    state = home.runtime.state_dict()
    mod = state["mods"]["display-comfort"]
    assert mod["active"] is True and mod["version"] == "1.0.0"
    statuses = [(p["kind"], p["status"]) for p in mod["parts"]]
    assert statuses == [("ui", "active"), ("spa", "active"), ("spa", "active")]
    assert mod["routes"] == [], "no python-hook, no routes"

    # the App imports these same-origin: the page and its icon; nothing unlisted
    status, headers, page = ui_file_response(home.runtime, "display-comfort", ["ui", "page.mjs"])
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and page == (MOD_DIR / "ui" / "page.mjs").read_bytes()
    status, headers, _ = ui_file_response(home.runtime, "display-comfort", ["ui", "icon.svg"])
    assert status == 200 and headers["Content-Type"].startswith("image/svg+xml")
    assert ui_file_response(home.runtime, "display-comfort", ["floofy.json"])[0] == 404

    # the page's config.set → the Loader's PUT → .floofy/config.json; null deletes
    status, _, raw = config_response(home.runtime, "display-comfort", "PUT", {"patch": {"chatScale": 130, "composerToo": True, "pageZoom": 90}})
    assert status == 200 and json.loads(raw)["config"] == {"chatScale": 130, "composerToo": True, "pageZoom": 90}
    assert json.loads((home.paths.mods / "display-comfort" / ".floofy" / "config.json").read_text(encoding="utf-8")) == {"chatScale": 130, "composerToo": True, "pageZoom": 90}
    status, _, raw = config_response(home.runtime, "display-comfort", "PUT", {"patch": {"composerToo": None}})
    assert status == 200 and json.loads(raw)["config"] == {"chatScale": 130, "pageZoom": 90}

    # disable: the parts unwind, the settings stay for the next enable
    status, body = home.call("mods.disable", {"id": "display-comfort"})
    assert status == 200 and body["ok"], body
    mod = home.runtime.state_dict()["mods"]["display-comfort"]
    assert mod["active"] is False and mod["reason"] == "UserDisabled"
    assert ui_file_response(home.runtime, "display-comfort", ["ui", "page.mjs"])[0] == 404
    assert json.loads(config_response(home.runtime, "display-comfort", "GET")[2])["config"] == {"chatScale": 130, "pageZoom": 90}
