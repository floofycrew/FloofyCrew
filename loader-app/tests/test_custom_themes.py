"""The shipped ``mods/custom-themes`` mod: validate clean, install → enable → active, the routes, the compiler, the first-frame sheet.

Fake payload, scratch data home, the real runtime and the real CLI in-process — the same
harness as ``test_settings_demo.py``. The presets route reads the payload's stylesheet, so
one test writes a small ``src-test.css`` with two built-in themes into the fake payload and
checks the harvest; the compiler and scoper are unit-tested on strings.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from floofy_core import __version__ as FRAMEWORK_VERSION
from floofy_core.boot_script import boot_mods_from_manifests, build_boot_descriptor
from floofy_core.modstore import code_kinds_of
from floofy_core.semver import Range, Version
from floofy_core.themes import THEME_CSS_VARS, validate_theme_dir
from floofy_core.validator import validate_mod
from floofy_loader.routes import ui_file_response

from floofy_testing import REPO_ROOT
from test_app_routes import Home, homes  # noqa: F401 - the fixture

MOD_DIR = REPO_ROOT / "mods" / "custom-themes"
MANIFEST = json.loads((MOD_DIR / "floofy.json").read_text(encoding="utf-8"))

HOST_CSS = (
    "[data-theme=neon-dark]{--bg:#0b0a1f;--bg-accent:#120e2e;--text:#c7f6ff;--accent:#ff2e88;--card:#130f2c;--lightningcss-dark:initial;color-scheme:dark}"
    "[data-theme=neon-light]{--bg:#fff;--text:#111;--accent:#c0006a}"
    "[data-theme=neon-dark] body,[data-theme=neon-light] body{background:linear-gradient(#3a1d5e,#05040f) fixed}"
    "[data-theme=neon-dark] body:after{content:\"\";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(#fff1 0 1px,#0000 1px 3px)}"
    "[data-theme=neon-dark] .msg-content:after,[data-theme=neon-light] .msg-content:after{animation:1s linear infinite neon-spin}"
    "@keyframes neon-spin{to{transform:rotate(1turn)}}"
    "@keyframes unrelated{to{opacity:0}}"
    "[data-theme=plain-dark]{--bg:#000;--text:#eee;--accent:#0af}"
    "[data-theme=plain-light]{--bg:#fff;--text:#111;--accent:#06c}"
    "[data-theme=tiny-dark]{--bg:#000}"
    ".neon-scanner{position:fixed;bottom:0}"
)
HOST_JS = "ss([{value:`neon`,label:`🌴 Neon Nights`},{value:`plain`,label:`Plain`}])"


def _css_assets(payload: Path) -> Path:
    assets = payload / "static" / "dist" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    return assets


def _enable(home: Home) -> None:
    code, result, _ = home.cli("--yes", "install", str(MOD_DIR), "--now")
    assert code == 0, result
    assert result.get("enabled") is False, "lands disabled: the python-hook and spa parts (Requirement 11.7)"
    status, body = home.call("mods.enable", {"id": "custom-themes"})
    assert status == 200 and body["ok"], body


def _route(home: Home, method: str, path: str, body: dict | None = None, query: dict | None = None):
    raw_body = json.dumps(body).encode("utf-8") if body is not None else b""
    status, headers, raw = home.runtime.mod_routes.dispatch_sync("custom-themes", method, path, query=query or {}, body=raw_body)
    text = raw.decode("utf-8")
    return status, headers, (json.loads(text) if headers.get("Content-Type", "").startswith("application/json") else text)


def test_the_mod_is_valid_and_declares_what_it_needs():
    report = validate_mod(MOD_DIR)
    assert report.ok and report.warnings == [], report.format()
    kinds = [p["kind"] for p in MANIFEST["parts"]]
    assert kinds == ["ui", "python-hook", "spa", "spa", "patch"]
    ui = MANIFEST["parts"][0]
    assert ui["entry"] == "ui/page.mjs" and ui["title"] == "Custom themes" and ui["icon"] == "ui/icon.svg"
    boot = MANIFEST["parts"][3]
    assert boot["activation"] == "boot" and boot["boot"] == {} and "when" not in boot["boot"], "generic: acts for every custom theme, no baked css (the hook writes the sheet)"
    assert code_kinds_of(MANIFEST) == {"python-hook", "spa"}
    assert MANIFEST["network"] == {"hosts": [], "credentials": False}
    assert Range.parse(MANIFEST["dependsOn"]["floofycrew"]).contains(Version.parse(FRAMEWORK_VERSION))
    assert json.loads((MOD_DIR / "patches" / "theme-reset-guard.json").read_text(encoding="utf-8")) == json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "patches" / "theme-reset-guard.json").read_text(encoding="utf-8")) if (REPO_ROOT / "mods" / "rimuru-branding" / "patches" / "theme-reset-guard.json").exists() else True
    page = (MOD_DIR / "ui" / "page.mjs").read_text(encoding="utf-8")
    assert "http://" not in page and "ws://" not in page
    hook = (MOD_DIR / "spa" / "boot.js").read_text(encoding="utf-8")
    assert "</script" not in hook.lower() and "`" not in hook and "=>" not in hook, "ES5, inlinable"


def test_the_page_lists_exactly_the_host_variables():
    page = (MOD_DIR / "ui" / "page.mjs").read_text(encoding="utf-8")
    import re

    names = set(re.findall(r'"(--[a-z-]+)"', page.split("export const ALL_VARS")[0]))
    assert names == set(THEME_CSS_VARS), sorted(names ^ set(THEME_CSS_VARS))


def test_boot_descriptor_links_the_sheet_only_for_custom_themes(tmp_path: Path):
    boot_mods = boot_mods_from_manifests([("custom-themes", MOD_DIR, MANIFEST)])
    assert len(boot_mods) == 1
    descriptor = build_boot_descriptor(boot_mods, host_home=tmp_path, defaults={"color": "kiro", "mode": "system"})
    assert [op.op for op in descriptor.ops] == ["insert-before", "insert-before"], "loader tag + boot script; no baked css (the hook's sheet is dynamic)"
    script = descriptor.ops[1].content
    assert "/apps/floofycrew/ui/boot/custom-themes/themes.css" in script and "indexOf('custom-') === 0" in script and "</script" not in script.lower().replace("</script>\n  ", "", 1)
    assert descriptor.ui_assets == ()


def test_compiler_scopes_and_refuses(tmp_path: Path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("ct_hook_test", MOD_DIR / "hook" / "__init__.py", submodule_search_locations=[str(MOD_DIR / "hook")])
    module = importlib.util.module_from_spec(spec)
    sys.modules["ct_hook_test"] = module
    spec.loader.exec_module(module)
    cssc = sys.modules["ct_hook_test.cssc"]
    css = cssc.scope_css("neon", ".topbar-glass{color:red}\nbody::before,.a>.b{content:''}\n@floofy-mode dark{ body{background:#000} }\n@media (max-width:900px){ .x{display:none} }\n@keyframes spin{to{transform:rotate(1turn)}}\nhtml{--foo:1}")
    assert 'html[data-theme="custom-neon-dark"] .topbar-glass,html[data-theme="custom-neon-light"] .topbar-glass{color:red}' in css
    assert 'html[data-theme="custom-neon-dark"] body::before,html[data-theme="custom-neon-light"] body::before,html[data-theme="custom-neon-dark"] .a>.b' in css
    assert 'html[data-theme="custom-neon-dark"] body{background:#000}' in css and 'html[data-theme="custom-neon-light"] body{background:#000}' not in css, "@floofy-mode dark scopes to one mode"
    assert '@media (max-width:900px){html[data-theme="custom-neon-dark"] .x' in css
    assert "@keyframes spin{to{transform:rotate(1turn)}}" in css, "keyframes pass through unscoped"
    assert 'html[data-theme="custom-neon-dark"],html[data-theme="custom-neon-light"]{--foo:1}' in css, "html fuses with the scope"
    assert cssc.scope_css("neon", "a{background:url(pack:branding/logo.png)}") == 'html[data-theme="custom-neon-dark"] a,html[data-theme="custom-neon-light"] a{background:url(/api/theme/neon/assets/branding/logo.png)}'
    for bad in ("@import url(a.css);", "a{background:url(https://x/y.png)}", "a{background:url(//x/y.png)}", "a{b:c}</style><script>", "a{width:expression(1)}", "a{b:ur\\6c(http://x)}"):
        with pytest.raises(cssc.CssRefused):
            cssc.scope_css("neon", bad)
    assert cssc.scope_css("neon", "a{background:url(/assets/x.png),url(data:image/png;base64,AA==)}").endswith("{background:url(/assets/x.png),url(data:image/png;base64,AA==)}")
    preview = cssc.preview_css("neon", {"dark": {"--bg": "#000", "--text": "#fff", "--accent": "#f0f"}, "light": {"--bg": "#fff", "--text": "#000", "--accent": "#00f"}}, ".x{color:red}", "light")
    assert preview.count("html[data-theme]{") == 1 and "--bg:#fff" in preview and "--bg:#000" not in preview and "html[data-theme] .x{color:red}" in preview


def test_presets_are_harvested_from_the_payload_stylesheet(homes: Path):
    home = Home(homes / "h", consent=True)
    assets = _css_assets(home.payload)
    (assets / "src-test.css").write_text(HOST_CSS, encoding="utf-8")
    (assets / "main-test.js").write_text(HOST_JS, encoding="utf-8")
    _enable(home)
    status, _, body = _route(home, "GET", "presets")
    assert status == 200, body
    by_slug = {p["slug"]: p for p in body["presets"]}
    assert set(by_slug) == {"neon", "plain"}, "tiny has too few variables to be a theme"
    assert by_slug["neon"]["label"] == "Neon Nights" and by_slug["neon"]["emoji"] == "🌴" and by_slug["neon"]["decorated"] is True
    assert by_slug["plain"]["label"] == "Plain" and by_slug["plain"]["emoji"] == "" and by_slug["plain"]["decorated"] is False
    assert by_slug["neon"]["swatch"]["dark"]["--bg"] == "#0b0a1f" and "dark" not in by_slug["neon"], "summary rows carry swatches, not palettes"
    status, _, body = _route(home, "GET", "presets/neon")
    preset = body["preset"]
    assert preset["dark"] == {"--bg": "#0b0a1f", "--bg-accent": "#120e2e", "--text": "#c7f6ff", "--accent": "#ff2e88", "--card": "#130f2c"}, "only allowlisted variables"
    assert preset["light"] == {"--bg": "#fff", "--text": "#111", "--accent": "#c0006a"}
    extra = preset["extraCss"]
    assert "@keyframes neon-spin" in extra and "unrelated" not in extra, "only the keyframes the rules reference"
    assert "body{background:linear-gradient(#3a1d5e,#05040f) fixed}" in extra and ".msg-content:after{animation:1s linear infinite neon-spin}" in extra
    assert "@floofy-mode dark {" in extra and "body:after{" in extra.split("@floofy-mode dark {")[1]
    assert ".neon-scanner" not in extra, "a rule not scoped to the theme is not the theme's"
    assert _route(home, "GET", "presets/nope")[0] == 404


def test_library_round_trip_install_boot_sheet_export_import(homes: Path):
    home = Home(homes / "h", consent=True)
    assets = _css_assets(home.payload)
    (assets / "src-test.css").write_text(HOST_CSS, encoding="utf-8")
    _enable(home)
    state = home.runtime.state_dict()
    mod = state["mods"]["custom-themes"]
    assert mod["active"] is True
    assert {(r["method"], r["path"]) for r in mod["routes"]} >= {("GET", "themes"), ("POST", "themes"), ("PUT", "themes/{slug}"), ("POST", "themes/{slug}/install"), ("POST", "preview"), ("GET", "css/{slug}"), ("GET", "presets"), ("POST", "import"), ("POST", "refresh")}
    sheet = Path(mod["exports"]["bootSheet"]["path"])
    assert sheet == home.host_home / "apps" / "floofycrew" / "ui" / "boot" / "custom-themes" / "themes.css" and sheet.is_file()
    assert mod["exports"]["bootSheet"]["themes"] == [] and mod["exports"]["bootSheetUrl"] == "/apps/floofycrew/ui/boot/custom-themes/themes.css"
    status, headers, page = ui_file_response(home.runtime, "custom-themes", ["ui", "page.mjs"])
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and page == (MOD_DIR / "ui" / "page.mjs").read_bytes()

    # empty library, nothing installed
    status, _, body = _route(home, "GET", "themes")
    assert status == 200 and body["library"] == [] and body["installed"] == [] and body["activeTheme"] is None

    # create from the harvested preset: palette + decorations become a library theme
    status, _, body = _route(home, "POST", "themes", {"slug": "my-neon", "name": "My Neon", "from": "preset:neon"})
    assert status == 200, body
    theme = body["theme"]
    assert theme["slug"] == "my-neon" and theme["manifest"] == {"slug": "my-neon", "name": "My Neon", "emoji": "🎨", "formatVersion": 1, "level": 0}
    assert theme["variables"]["dark"]["--accent"] == "#ff2e88" and "@keyframes neon-spin" in theme["extraCss"] and theme["installed"] is False
    assert theme["meta"]["basedOn"] == "preset:neon" and theme["meta"]["createdAt"]
    library_dir = home.paths.mods / "custom-themes" / ".floofy" / "library" / "my-neon"
    assert (library_dir / "pack" / "theme.json").is_file() and (library_dir / "pack" / "variables.json").is_file() and (library_dir / "extra.css").is_file()
    assert not (library_dir / "pack" / "extra.css").exists(), "the extra layer never enters the pack"
    validate_theme_dir(library_dir / "pack")
    assert _route(home, "POST", "themes", {"slug": "my-neon", "name": "again", "from": "blank"})[0] == 409

    # save: a bot name lifts the level to 1; an unknown variable and a bad extra rule are refused
    theme["manifest"]["branding"] = {"botName": "Crockett"}
    theme["variables"]["light"]["--json-key"] = "#c0006a"
    status, _, body = _route(home, "PUT", "themes/my-neon", theme)
    assert status == 200, body
    assert body["theme"]["manifest"]["level"] == 1 and body["theme"]["manifest"]["branding"] == {"botName": "Crockett"}
    assert body["bootSheet"]["themes"] == [], "not installed yet: the first-frame sheet covers installed themes"
    bad = dict(theme)
    bad["variables"] = {"dark": {**theme["variables"]["dark"], "--nope": "#fff"}, "light": theme["variables"]["light"]}
    status, _, body = _route(home, "PUT", "themes/my-neon", bad)
    assert status == 400 and "--nope" in body["error"]
    bad = dict(theme)
    bad["extraCss"] = "a{background:url(https://evil.example/x.png)}"
    status, _, body = _route(home, "PUT", "themes/my-neon", bad)
    assert status == 400 and "another origin" in body["error"]
    bad = dict(theme)
    bad["overridesCss"] = "@import url(x.css);"
    status, _, body = _route(home, "PUT", "themes/my-neon", bad)
    assert status == 400 and "overrides.css" in body["error"]

    # compiled css and the live preview
    status, headers, css = _route(home, "GET", "css/my-neon")
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8"
    assert css.startswith("/* floofy custom-themes: my-neon */\nhtml[data-theme=\"custom-my-neon-dark\"]{--bg:#0b0a1f;") and 'html[data-theme="custom-my-neon-dark"] body,html[data-theme="custom-my-neon-light"] body{background:linear-gradient' in css and "@keyframes neon-spin" in css
    status, headers, css = _route(home, "POST", "preview", {"slug": "my-neon", "variables": theme["variables"], "extraCss": ".x{color:red}", "mode": "dark"})
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8" and "html[data-theme]{--bg:#0b0a1f" in css and "html[data-theme] .x{color:red}" in css
    assert _route(home, "GET", "css/unknown")[0] == 404

    # branding upload within the host's caps; the file lands in the pack, the level stays 1
    status, _, body = _route(home, "POST", "assets/my-neon/branding/favicon.png", {"data": base64.b64encode(b"\x89PNG fake favicon").decode("ascii")})
    assert status == 200 and body["theme"]["assets"] == [{"path": "branding/favicon.png", "bytes": 17}]
    assert _route(home, "POST", "assets/my-neon/branding/evil.exe", {"data": "QUFB"})[0] == 400
    assert _route(home, "POST", "assets/my-neon/branding/logo.png", {"data": base64.b64encode(b"x" * (100 * 1024 + 1)).decode("ascii")})[0] == 400

    # install: the host's themes directory receives exactly the pack, validated the host's way
    status, _, body = _route(home, "POST", "themes/my-neon/install")
    assert status == 200, body
    installed = home.host_home / "themes" / "my-neon"
    assert {k: (sorted(v) if k == "files" else v) for k, v in body["installed"].items()} == {"slug": "my-neon", "level": 1, "path": str(installed), "files": ["branding/favicon.png", "theme.json", "variables.json"]}
    assert validate_theme_dir(installed).level == 1 and not (installed / "extra.css").exists()
    assert body["bootSheet"]["themes"] == ["my-neon"]
    boot_css = sheet.read_text(encoding="utf-8")
    assert 'html[data-theme="custom-my-neon-dark"]{--bg:#0b0a1f;' in boot_css and 'html[data-theme="custom-my-neon-dark"] body,html[data-theme="custom-my-neon-light"] body{background:linear-gradient' in boot_css, "palette AND extra layer on the first frame"
    status, headers, css_all = _route(home, "GET", "css")
    assert status == 200 and css_all == boot_css
    status, _, body = _route(home, "GET", "themes")
    assert body["library"][0]["installed"] is True and body["installed"] == [{"slug": "my-neon", "name": "My Neon", "emoji": "🎨", "level": 1, "kind": "pack", "inLibrary": True}]

    # export → import under another slug carries the palette, the extra layer and the binary asset
    status, headers, exported = _route(home, "GET", "themes/my-neon/export")
    assert status == 200 and headers["Content-Disposition"] == 'attachment; filename="my-neon.floofy-theme.json"'
    document = exported if isinstance(exported, dict) else json.loads(exported)
    assert document["format"] == "floofy-theme/1" and base64.b64decode(document["assets"]["branding/favicon.png"]) == b"\x89PNG fake favicon" and "installed" not in document
    status, _, body = _route(home, "POST", "import", document, query={"slug": ["neon-two"]})
    assert status == 200, body
    assert body["theme"]["slug"] == "neon-two" and body["theme"]["manifest"]["slug"] == "neon-two" and body["theme"]["assets"] == [{"path": "branding/favicon.png", "bytes": 17}] and body["theme"]["extraCss"] == theme["extraCss"]
    assert _route(home, "POST", "import", document, query={"slug": ["neon-two"]})[0] == 409
    assert _route(home, "POST", "import", document, query={"slug": ["neon-two"], "overwrite": ["1"]})[0] == 200

    # adopt an installed pack that is not in the library (another mod's, a GitHub install…)
    foreign = home.host_home / "themes" / "foreign"
    foreign.mkdir()
    (foreign / "theme.json").write_text(json.dumps({"slug": "foreign", "name": "Foreign", "emoji": "🧩", "level": 0, "formatVersion": 1}), encoding="utf-8")
    (foreign / "variables.json").write_text(json.dumps({"dark": {"--bg": "#000", "--text": "#eee", "--accent": "#0af"}, "light": {"--bg": "#fff", "--text": "#111", "--accent": "#06c"}}), encoding="utf-8")
    status, _, body = _route(home, "GET", "themes")
    assert {t["slug"]: t["inLibrary"] for t in body["installed"]} == {"foreign": False, "my-neon": True}
    assert [t["slug"] for t in body["library"]] == ["my-neon", "neon-two"]
    status, _, info = _route(home, "GET", "status")
    assert status == 200 and info["host"] == {"version": "0.7.0.5", "edition": "internal"} and info["bootSheet"]["url"] == "/apps/floofycrew/ui/boot/custom-themes/themes.css"
    status, _, body = _route(home, "POST", "themes", {"slug": "foreign-edit", "name": "Foreign edit", "from": "installed:foreign"})
    assert status == 200 and body["theme"]["meta"]["basedOn"] == "installed:foreign" and body["theme"]["variables"]["dark"]["--accent"] == "#0af"
    assert sheet.read_text(encoding="utf-8").count("custom-foreign-dark") == 1, "the first-frame sheet covers every installed custom theme, library or not"

    # uninstall keeps the library copy; delete with ?uninstall=1 removes both
    status, _, body = _route(home, "POST", "themes/my-neon/uninstall")
    assert status == 200 and body["uninstalled"] is True and not installed.exists() and body["bootSheet"]["themes"] == ["foreign"]
    assert _route(home, "GET", "themes/my-neon")[0] == 200
    _route(home, "POST", "themes/my-neon/install")
    status, _, body = _route(home, "DELETE", "themes/my-neon", query={"uninstall": ["1"]})
    assert status == 200 and body["uninstalled"] is True and not installed.exists() and not library_dir.exists()
    assert _route(home, "GET", "themes/my-neon")[0] == 404

    # disable: the routes unwind and the first-frame sheet goes away with the mod
    status, body = home.call("mods.disable", {"id": "custom-themes"})
    assert status == 200 and body["ok"], body
    assert home.runtime.state_dict()["mods"]["custom-themes"]["routes"] == [] and not sheet.exists()
    assert (home.paths.mods / "custom-themes" / ".floofy" / "library" / "neon-two" / "pack" / "theme.json").is_file(), "the library survives a disable"
