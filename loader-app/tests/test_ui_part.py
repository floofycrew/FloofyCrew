"""The mod ``ui`` part (task 11.5; Requirement 16.4, 1.6, 14.3): validator rules, Loader serving, config and the mod's own routes.

Fake payload, scratch data home, the real runtime booting in-process; no browser
here (``spa-host/tests/test_playwright.py`` mounts a page for real).
"""
from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path

import pytest

from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import code_kinds_of
from floofy_core.validator import validate_mod
from floofy_loader.host import read_host_facts
from floofy_loader.modroutes import ModRouteRegistry, ModResponse, normalize_path
from floofy_loader.routes import CONFIG_MAX_BYTES, config_response, ui_file_response
from floofy_loader.runtime import LoaderRuntime

from floofy_testing import fake_payload

PAGE = "export default function mount(container, api) { container.textContent = 'hi'; return () => {}; }\n"
HOOK = '''
import json

def activate(ctx):
    def echo(request):
        return {"echo": request.json(), "config": ctx.config.to_dict(), "query": request.query, "mod": request.mod_id}

    async def item(request):
        return 201, {"key": request.params["key"], "method": request.method}

    def boom(request):
        raise RuntimeError("route exploded on purpose")

    ctx.routes.add("POST", "echo", echo)
    ctx.routes.add("GET", "items/{key}", item)
    ctx.routes.add("GET", "boom", boom)
    ctx.state["routes"] = ctx.routes.list()

def deactivate(ctx):
    pass
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_mod(root: Path, mod_id: str, *, files: dict[str, str], parts: list[dict], extra: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {"schema": 1, "id": mod_id, "name": mod_id, "version": "1.0.0", "description": "ui part test mod", "authors": ["tests"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": parts, "files": [{"path": rel, "sha256": sha((root / rel).read_bytes())} for rel in sorted(files)]}
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


UI_PART = {"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": "Settings"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    payload = fake_payload(tmp_path / "payload", "0.7.0", build="0.7.0.5")
    host_home = tmp_path / "home"
    data = DataHome.for_host_home(host_home).ensure()
    write_consent(data.consent, by="tests", how="test")
    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0.5"
    module.__file__ = str(payload / "__init__.py")
    runtime = LoaderRuntime()
    runtime.facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(host_home)}, adapters=[])
    runtime.paths = data
    yield types.SimpleNamespace(data=data, runtime=runtime, tmp=tmp_path)
    import logging
    import sys

    for key in [k for k in sys.modules if k == "floofy_mods" or k.startswith("floofy_mods.")]:
        sys.modules.pop(key, None)
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if name.startswith("floofy.mods.") and isinstance(logger, logging.Logger):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


# --- the manifest ---------------------------------------------------------------------------------------


def test_ui_part_validates_and_is_inert_until_opened(tmp_path: Path):
    mod = write_mod(tmp_path / "ok", "pagey", files={"ui/page.mjs": PAGE, "ui/icon.svg": "<svg/>"}, parts=[{**UI_PART, "icon": "ui/icon.svg"}])
    report = validate_mod(mod)
    assert report.ok and report.warnings == [], report.format()
    manifest = json.loads((mod / "floofy.json").read_text(encoding="utf-8"))
    assert code_kinds_of(manifest) == set(), "a ui part is inert until opened: the mod lands enabled (only python-hook/spa land disabled)"
    from floofy_core.kinds import SEAMS, handlers

    assert "ui" in SEAMS and "App" in SEAMS["ui"]
    outcome = handlers()["ui"].install(None, "pagey", mod, manifest["parts"][0], 0)
    assert outcome.status == "loader" and "inert" in outcome.detail and not outcome.modifies_payload


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda m, root: m["files"].__setitem__(slice(None), [f for f in m["files"] if f["path"] != "ui/page.mjs"]), "UnverifiedPart"),  # the entry is not hash-listed
        (lambda m, root: (root / "ui" / "page.mjs").unlink(), "MissingFiles"),
        (lambda m, root: m["parts"][0].update(side="gateway"), "SchemaViolation"),
        (lambda m, root: m["parts"][0].update(entry="ui/page.txt"), "SchemaViolation"),
        (lambda m, root: m["parts"][0].update(title=""), "SchemaViolation"),
        (lambda m, root: m["parts"][0].pop("title"), "SchemaViolation"),
        (lambda m, root: m["parts"][0].update(entry="../page.mjs"), "SchemaViolation"),
        (lambda m, root: m["parts"][0].update(entry="other/page.mjs"), "InvalidPart"),  # outside the part's directory
    ],
)
def test_ui_part_rules(tmp_path: Path, mutate, code):
    mod = write_mod(tmp_path / "bad", "pagey", files={"ui/page.mjs": PAGE, "other/page.mjs": PAGE}, parts=[UI_PART])
    manifest = json.loads((mod / "floofy.json").read_text(encoding="utf-8"))
    mutate(manifest, mod)
    (mod / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = validate_mod(mod)
    assert code in {f.code for f in report.errors}, report.format()


def test_scaffold_makes_a_valid_ui_mod(tmp_path: Path):
    from floofy_core.scaffold import scaffold

    root = scaffold("ui", tmp_path, mod_id="my-page", name="My Page")
    report = validate_mod(root)
    assert report.ok, report.format()
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    assert manifest["parts"] == [{"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": "My Page"}]
    assert "export default async function mount(container, api)" in (root / "ui" / "page.mjs").read_text(encoding="utf-8")


# --- the mod's own routes -------------------------------------------------------------------------------


def test_route_registry_shapes_replies_and_fails_open():
    registry = ModRouteRegistry()
    calls = registry.for_mod("alpha")
    calls.add("GET", "/hello/", lambda request: {"hi": request.mod_id})
    calls.add("post", "items/{key}", lambda request: (201, {"key": request.params["key"], "body": request.json()}))
    calls.add("GET", "raw", lambda request: ModResponse(200, b"bytes", {"Content-Type": "application/octet-stream"}))
    calls.add("GET", "boom", lambda request: 1 / 0)
    assert calls.list() == [{"method": "GET", "path": "hello"}, {"method": "POST", "path": "items/{key}"}, {"method": "GET", "path": "raw"}, {"method": "GET", "path": "boom"}]
    status, headers, body = registry.dispatch_sync("alpha", "GET", "hello")
    assert status == 200 and headers["Content-Type"].startswith("application/json") and json.loads(body) == {"hi": "alpha"}
    status, _, body = registry.dispatch_sync("alpha", "POST", "items/42", body=b'{"n": 1}')
    assert status == 201 and json.loads(body) == {"key": "42", "body": {"n": 1}}
    status, headers, body = registry.dispatch_sync("alpha", "GET", "raw")
    assert (status, body, headers["Content-Type"]) == (200, b"bytes", "application/octet-stream")
    status, _, body = registry.dispatch_sync("alpha", "GET", "boom")
    assert status == 500 and "ZeroDivisionError" in json.loads(body)["error"]
    status, _, body = registry.dispatch_sync("alpha", "DELETE", "hello")
    assert status == 404 and json.loads(body)["routes"][0] == {"method": "GET", "path": "hello"}
    assert registry.dispatch_sync("beta", "GET", "hello")[0] == 404, "routes belong to their mod"
    with pytest.raises(ValueError):
        calls.add("PATCH", "x", lambda r: None)
    with pytest.raises(ValueError):
        normalize_path("a/b/c/d/e")
    with pytest.raises(ValueError):
        normalize_path("../x")
    assert normalize_path("/a/{b}/") == "a/{b}"
    assert calls.remove("GET", "hello") == 1 and registry.remove("alpha") == 3 and calls.list() == []


def test_loader_serves_the_page_registers_the_routes_and_keeps_the_config(home):
    data, runtime = home.data, home.runtime
    write_mod(data.mods / "pagey", "pagey", files={"ui/page.mjs": PAGE, "ui/lib/util.mjs": "export const u = 1;\n", "hook.py": HOOK, "README.md": "# pagey\n"}, parts=[UI_PART, {"kind": "python-hook", "side": "gateway", "path": "hook.py", "module": "hook"}])
    data.enabled.write_text(json.dumps({"pagey": True}), encoding="utf-8")
    runtime.reload()
    state = runtime.state_dict()
    mod = state["mods"]["pagey"]
    assert mod["active"] is True
    ui_part = next(p for p in mod["parts"] if p["kind"] == "ui")
    assert ui_part["status"] == "active" and ui_part["entry"] == "ui/page.mjs" and ui_part["title"] == "Settings" and ui_part["icon"] is None and "App" in ui_part["seam"]
    assert mod["routes"] == [{"method": "POST", "path": "echo"}, {"method": "GET", "path": "items/{key}"}, {"method": "GET", "path": "boom"}]
    assert mod["exports"]["routes"] == mod["routes"], "ctx.routes.list() is what the state publishes"
    # the page and its helper are served, manifest-listed only, from the installed directory
    status, headers, body = ui_file_response(runtime, "pagey", ["ui", "page.mjs"])
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and headers["Cache-Control"] == "no-cache" and body.decode("utf-8") == PAGE
    assert ui_file_response(runtime, "pagey", ["ui", "lib", "util.mjs"])[0] == 200
    assert ui_file_response(runtime, "pagey", ["README.md"])[0] == 200, "listed files are served (the page may fetch them)"
    assert ui_file_response(runtime, "pagey", ["floofy.json"])[0] == 404, "the manifest is not a listed file"
    (data.mods / "pagey" / "ui" / "secret.mjs").write_text("export const s = 1;", encoding="utf-8")
    assert ui_file_response(runtime, "pagey", ["ui", "secret.mjs"])[0] == 404, "an unlisted file is never served"
    assert ui_file_response(runtime, "pagey", ["..", "floofy.json"])[0] == 404 and ui_file_response(runtime, "pagey", [".floofy", "config.json"])[0] == 404
    assert ui_file_response(runtime, "nope", ["ui", "page.mjs"])[0] == 404
    # the mod's routes answer through the registry with the request shaped for the handler
    status, _, body = runtime.mod_routes.dispatch_sync("pagey", "POST", "echo", query={"a": ["1"]}, body=b'{"x": 2}')
    assert status == 200 and json.loads(body) == {"echo": {"x": 2}, "config": {}, "query": {"a": ["1"]}, "mod": "pagey"}
    status, _, body = runtime.mod_routes.dispatch_sync("pagey", "GET", "items/7")
    assert status == 201 and json.loads(body) == {"key": "7", "method": "GET"}
    status, _, body = runtime.mod_routes.dispatch_sync("pagey", "GET", "boom")
    assert status == 500 and "route exploded on purpose" in json.loads(body)["error"]
    assert runtime.result.state.mods["pagey"].active is True, "a raising route is that request's 500, never a fault"
    # config: GET, PUT merges, null deletes, the cap, the same store the hook reads as ctx.config
    status, _, body = config_response(runtime, "pagey", "GET")
    assert status == 200 and json.loads(body) == {"ok": True, "mod": "pagey", "config": {}}
    status, _, body = config_response(runtime, "pagey", "PUT", {"patch": {"greeting": "hello", "toggle": True}})
    assert status == 200 and json.loads(body)["config"] == {"greeting": "hello", "toggle": True}
    status, _, body = config_response(runtime, "pagey", "PUT", {"toggle": None, "n": 3})
    assert status == 200 and json.loads(body)["config"] == {"greeting": "hello", "n": 3}
    assert json.loads((data.mods / "pagey" / ".floofy" / "config.json").read_text(encoding="utf-8")) == {"greeting": "hello", "n": 3}
    status, _, body = runtime.mod_routes.dispatch_sync("pagey", "POST", "echo", body=b"null")
    assert json.loads(body)["config"] == {"greeting": "hello", "n": 3}, "the mod's Python side sees what the page set"
    assert config_response(runtime, "pagey", "PUT", {"patch": {"big": "x" * CONFIG_MAX_BYTES}})[0] == 413
    assert config_response(runtime, "pagey", "PUT", ["not", "an", "object"])[0] == 400
    assert config_response(runtime, "nope", "GET")[0] == 404 and config_response(runtime, "../x", "GET")[0] == 404
    # disabling the mod unwinds its routes and stops serving the page
    data.enabled.write_text(json.dumps({"pagey": False}), encoding="utf-8")
    runtime.reload()
    assert runtime.state_dict()["mods"]["pagey"]["routes"] == [] and runtime.mod_routes.dispatch_sync("pagey", "POST", "echo")[0] == 404
    assert ui_file_response(runtime, "pagey", ["ui", "page.mjs"])[0] == 404, "a disabled mod's page is not reachable"
    assert config_response(runtime, "pagey", "GET")[0] == 200, "its settings stay readable"


def test_a_fault_unwinds_the_routes(home):
    data, runtime = home.data, home.runtime
    write_mod(data.mods / "pagey", "pagey", files={"ui/page.mjs": PAGE, "hook.py": HOOK}, parts=[UI_PART, {"kind": "python-hook", "side": "gateway", "path": "hook.py", "module": "hook"}])
    data.enabled.write_text(json.dumps({"pagey": True}), encoding="utf-8")
    runtime.reload()
    assert runtime.mod_routes.listing("pagey")
    assert runtime.fault_mod("pagey", "page threw", source="spa") is True
    assert runtime.mod_routes.listing("pagey") == [] and ui_file_response(runtime, "pagey", ["ui", "page.mjs"])[0] == 404
    assert runtime.state_dict()["mods"]["pagey"]["reason"] == "Error"
