"""The shipped ``mods/settings-demo`` mod (task 11.6; Requirement 16.4, 8.9): validate clean, install → enable → active, the config round trip and the echo route.

Fake payload, scratch data home, the real runtime and the real CLI in-process;
the archive the two editions receive is covered by ``scripts/install_both_editions.py``
and the browser by ``spa-host/tests/test_playwright.py::test_6_manager_app_smoke``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from floofy_core import __version__ as FRAMEWORK_VERSION
from floofy_core.modstore import code_kinds_of
from floofy_core.semver import Range, Version
from floofy_core.validator import validate_mod
from floofy_loader.routes import config_response, ui_file_response

from floofy_testing import REPO_ROOT
from test_app_routes import Home, homes  # noqa: F401 - the fixture

MOD_DIR = REPO_ROOT / "mods" / "settings-demo"
MANIFEST = json.loads((MOD_DIR / "floofy.json").read_text(encoding="utf-8"))


def test_the_mod_is_valid_and_declares_what_it_needs():
    report = validate_mod(MOD_DIR)
    assert report.ok and report.warnings == [], report.format()
    kinds = [p["kind"] for p in MANIFEST["parts"]]
    assert kinds == ["ui", "python-hook"], "a settings page plus the backend that answers it"
    ui = MANIFEST["parts"][0]
    assert ui["entry"] == "ui/page.mjs" and ui["title"] == "Settings demo" and ui["icon"] == "ui/icon.svg"
    assert code_kinds_of(MANIFEST) == {"python-hook"}, "the hook makes it land disabled (Requirement 11.7); the ui part alone would not"
    assert MANIFEST["network"] == {"hosts": [], "credentials": False}
    assert Range.parse(MANIFEST["dependsOn"]["floofycrew"]).contains(Version.parse(FRAMEWORK_VERSION)), "the mod's floor is the release that ships the ui part and ctx.routes"
    assert (MOD_DIR / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    listed = {f["path"] for f in MANIFEST["files"]}
    assert listed == {"README.md", "LICENSE", "hook/__init__.py", "ui/page.mjs", "ui/icon.svg"}
    page = (MOD_DIR / "ui" / "page.mjs").read_text(encoding="utf-8")
    assert "api.config.get()" in page and "api.config.set(" in page and 'api.routes.fetch("echo"' in page and "export default async function mount(container, api)" in page
    assert "http://" not in page and "ws://" not in page, "no plaintext URL: the page talks to the Loader's same-origin routes only"


def test_install_enable_config_round_trip_and_the_echo_route(homes: Path):
    home = Home(homes / "h", consent=True)
    code, result, _ = home.cli("--yes", "install", str(MOD_DIR), "--now", "--disabled")
    assert code == 0, result
    assert result.get("enabled") is False, "installed switched off on purpose (--disabled) to exercise the disabled path first"
    disclosure = result.get("disclosure") or {}
    assert [(p.get("kind"), "App" in str(p.get("seam"))) for p in disclosure.get("parts", [])] == [("ui", True), ("python-hook", False)]
    home.runtime.reload()
    assert home.runtime.state_dict()["mods"]["settings-demo"]["active"] is False
    assert ui_file_response(home.runtime, "settings-demo", ["ui", "page.mjs"])[0] == 404, "a disabled mod's page is not served"

    status, body = home.call("mods.enable", {"id": "settings-demo"})
    assert status == 200 and body["ok"], body
    state = home.runtime.state_dict()
    mod = state["mods"]["settings-demo"]
    assert mod["active"] is True and mod["version"] == "1.0.0"
    ui_part = next(p for p in mod["parts"] if p["kind"] == "ui")
    assert ui_part["status"] == "active" and ui_part["entry"] == "ui/page.mjs" and ui_part["title"] == "Settings demo" and ui_part["icon"] == "ui/icon.svg"
    hook_part = next(p for p in mod["parts"] if p["kind"] == "python-hook")
    assert hook_part["status"] == "active"
    assert mod["routes"] == [{"method": "GET", "path": "echo"}, {"method": "POST", "path": "echo"}]
    assert mod["exports"]["routes"] == mod["routes"]

    # the App imports these same-origin: the page, its icon; nothing unlisted
    status, headers, page = ui_file_response(home.runtime, "settings-demo", ["ui", "page.mjs"])
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and page == (MOD_DIR / "ui" / "page.mjs").read_bytes()
    status, headers, _ = ui_file_response(home.runtime, "settings-demo", ["ui", "icon.svg"])
    assert status == 200 and headers["Content-Type"].startswith("image/svg+xml")
    assert ui_file_response(home.runtime, "settings-demo", ["floofy.json"])[0] == 404

    # the echo route before anything is stored: defaults over an empty config
    status, _, raw = home.runtime.mod_routes.dispatch_sync("settings-demo", "GET", "echo", query={"probe": ["1"]})
    reply = json.loads(raw)
    assert status == 200 and reply["mod"] == "settings-demo" and reply["version"] == "1.0.0" and reply["config"] == {} and reply["method"] == "GET"
    assert reply["effective"] == {"greeting": "Hello from settings-demo", "notify": False} and reply["query"] == {"probe": ["1"]}
    assert reply["host"] == {"edition": "internal", "version": "0.7.0.5"}

    # the page's config.set → the Loader's PUT → the same .floofy/config.json the hook reads
    status, _, raw = config_response(home.runtime, "settings-demo", "PUT", {"patch": {"greeting": "hi there", "notify": True}})
    assert status == 200 and json.loads(raw)["config"] == {"greeting": "hi there", "notify": True}
    assert json.loads((home.paths.mods / "settings-demo" / ".floofy" / "config.json").read_text(encoding="utf-8")) == {"greeting": "hi there", "notify": True}
    status, _, raw = home.runtime.mod_routes.dispatch_sync("settings-demo", "POST", "echo", body=b'{"from": "test"}')
    reply = json.loads(raw)
    assert status == 200 and reply["config"] == {"greeting": "hi there", "notify": True} and reply["effective"] == {"greeting": "hi there", "notify": True} and reply["echo"] == {"from": "test"} and reply["method"] == "POST"
    status, _, raw = config_response(home.runtime, "settings-demo", "PUT", {"notify": None})
    assert status == 200 and json.loads(raw)["config"] == {"greeting": "hi there"}
    status, _, raw = config_response(home.runtime, "settings-demo", "GET")
    assert json.loads(raw)["config"] == {"greeting": "hi there"}
    assert home.runtime.mod_routes.dispatch_sync("settings-demo", "DELETE", "echo")[0] == 404, "only what the hook registered"

    # disable through the App's route: the route unwinds, the page stops, the settings stay
    status, body = home.call("mods.disable", {"id": "settings-demo"})
    assert status == 200 and body["ok"], body
    mod = home.runtime.state_dict()["mods"]["settings-demo"]
    assert mod["active"] is False and mod["reason"] == "UserDisabled" and mod["routes"] == []
    assert home.runtime.mod_routes.dispatch_sync("settings-demo", "GET", "echo")[0] == 404
    assert ui_file_response(home.runtime, "settings-demo", ["ui", "page.mjs"])[0] == 404
    assert json.loads(config_response(home.runtime, "settings-demo", "GET")[2])["config"] == {"greeting": "hi there"}
    ops = [(row["op"], row.get("mod"), row.get("actor")) for row in home.audit() if row.get("mod") == "settings-demo"]
    assert ("install", "settings-demo", "cli") in ops and ("enable", "settings-demo", "app") in ops and ("disable", "settings-demo", "app") in ops
