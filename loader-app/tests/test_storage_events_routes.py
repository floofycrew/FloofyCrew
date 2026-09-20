"""Per-mod config and logger (Requirement 3.7), the event bus (3.8) and the Loader routes (state, fault, spa, health)."""
from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.resolver import Reason
from floofy_loader import api
from floofy_loader.consent import write_consent
from floofy_loader.events import EVENTS, EventBus, ModEvents
from floofy_loader.paths import FloofyPaths
from floofy_loader.routes import ROUTE_TABLE, SPA_MAX_DEPTH, content_type_for, fault_response, health_response, spa_response, state_response
from floofy_loader.runtime import LoaderRuntime
from floofy_loader.storage import RUNTIME_DIR, ModConfig, mod_data_dir, mod_logger

from loader_testing import LOADER_APP_DIR
from test_boot import make_mod


# --- storage ---------------------------------------------------------------------------------------


def test_mod_data_dir_is_contained_and_hidden_from_the_validator(tmp_path: Path):
    home = tmp_path / "floofy"
    target = mod_data_dir(home, "alpha", payload_root=tmp_path / "payload")
    assert target == (home / "mods" / "alpha" / RUNTIME_DIR).resolve() and target.is_dir()
    with pytest.raises(ValueError):
        mod_data_dir(home, "../escape")
    with pytest.raises(ValueError, match="payload"):
        mod_data_dir(tmp_path / "payload" / "floofy", "alpha", payload_root=tmp_path / "payload")
    from floofy_core.validator import _shipped_files

    (target / "config.json").write_text("{}", encoding="utf-8")
    assert _shipped_files(home / "mods" / "alpha") == [], "runtime files are invisible to floofy validate"


def test_mod_config_round_trips_atomically(tmp_path: Path):
    config = ModConfig(tmp_path / "floofy", "alpha")
    assert config.get("missing", 1) == 1 and "x" not in config
    config.set("x", {"nested": [1, 2]})
    config["y"] = "z"
    config.update({"n": 3})
    again = ModConfig(tmp_path / "floofy", "alpha")
    assert again.to_dict() == {"x": {"nested": [1, 2]}, "y": "z", "n": 3} and again["y"] == "z"
    assert again.delete("y") and not again.delete("y")
    assert list(again.items()) == [("n", 3), ("x", {"nested": [1, 2]})]
    assert not list((tmp_path / "floofy" / "mods" / "alpha" / RUNTIME_DIR).glob(".config-*")), "no temp files left behind"
    with pytest.raises(TypeError):
        config.set("bad", object())
    again.clear()
    assert ModConfig(tmp_path / "floofy", "alpha").to_dict() == {}


def test_mod_logger_writes_the_file_and_prefixes_propagated_records(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    log = mod_logger(tmp_path / "floofy", "alpha")
    same = mod_logger(tmp_path / "floofy", "alpha")
    assert log is same and log.name == "floofy.mods.alpha" and len(log.handlers) == 1, "idempotent: one file handler"
    with caplog.at_level(logging.INFO, logger="floofy.mods.alpha"):
        log.info("hello %s", "world")
    assert caplog.records[-1].getMessage() == "[floofy:alpha] hello world"
    text = (tmp_path / "floofy" / "mods" / "alpha" / RUNTIME_DIR / "mod.log").read_text(encoding="utf-8")
    assert "INFO [floofy:alpha] hello world" in text


# --- events ----------------------------------------------------------------------------------------


def test_event_bus_delivers_fail_open_with_attribution():
    bus = EventBus()
    seen: list[tuple[str, str, dict]] = []
    alpha, beta = ModEvents(bus, "alpha"), ModEvents(bus, "beta")
    alpha.subscribe("mod.activated", lambda name, payload: seen.append(("alpha", name, payload)))
    beta.subscribe("*", lambda name, payload: seen.append(("beta", name, payload)))

    def explode(name, payload):
        raise RuntimeError("subscriber down")

    beta.subscribe("mod.activated", explode)
    assert bus.publish("mod.activated", {"mod": "x"}) == 2
    assert seen == [("alpha", "mod.activated", {"mod": "x"}), ("beta", "mod.activated", {"mod": "x"})]
    assert bus.errors == [{"event": "mod.activated", "mod": "beta", "error": "RuntimeError: subscriber down"}]
    assert alpha.publish("mod.alpha.custom", {"n": 1}) == 1 and bus.history[-1]["source"] == "mod:alpha"
    assert bus.unsubscribe_mod("beta") == 2 and [s.mod_id for s in bus.subscriptions] == ["alpha"]
    assert set(EVENTS) >= {"mod.activated", "mod.deactivated", "mod.quarantined", "mod.faulted", "host.version_changed", "loader.state"}
    snapshot = bus.to_dict()
    assert snapshot["events"] == list(EVENTS) and len(snapshot["history"]) == 2


@settings(max_examples=40, deadline=None)
@given(events=st.lists(st.sampled_from(EVENTS), max_size=20), bad=st.booleans())
def test_every_published_event_reaches_every_healthy_subscriber(events, bad):
    bus = EventBus()
    counts = {"a": 0, "b": 0}
    bus.subscribe("*", lambda n, p: counts.__setitem__("a", counts["a"] + 1), "a")
    if bad:
        bus.subscribe("*", lambda n, p: 1 / 0, "bad")
    bus.subscribe("*", lambda n, p: counts.__setitem__("b", counts["b"] + 1), "b")
    for name in events:
        bus.publish(name)
    assert counts == {"a": len(events), "b": len(events)} and len(bus.errors) == (len(events) if bad else 0)


# --- routes (pure responders) -------------------------------------------------------------------------


def test_route_table_matches_app_json_documentation():
    doc = json.loads((LOADER_APP_DIR / "app.json").read_text(encoding="utf-8"))["floofycrew"]
    assert doc["routesMountedAt"] == "/api/apps/floofycrew" and doc["unofficial"] is True
    documented = {(r["method"], r["path"]) for r in doc["routes"]}
    # the per-depth catch-alls (spa files, ui files, a mod's own api routes) are documented once each
    coded = {(m, p) for m, p in ROUTE_TABLE if not (p.startswith("/spa/") or p.startswith("/ui/mods/") or "/api/{p1}" in p)}
    coded |= {("GET", "/spa/{id}/{file...}"), ("GET", "/ui/mods/{id}/{file...}"), *((m, "/mods/{id}/api/{path...}") for m in ("GET", "POST", "PUT", "DELETE"))}
    assert documented == coded, documented ^ coded
    assert sum(1 for m, p in ROUTE_TABLE if p.startswith("/spa/")) == SPA_MAX_DEPTH
    assert sum(1 for m, p in ROUTE_TABLE if p.startswith("/ui/mods/")) == SPA_MAX_DEPTH
    assert sum(1 for m, p in ROUTE_TABLE if "/api/{p1}" in p) == 4 * 4
    assert ("GET", "/spa/{id}/{p1}/{p2}") in ROUTE_TABLE and ("GET", "/ui/mods/{id}/{p1}/{p2}") in ROUTE_TABLE and ("PUT", "/mods/{id}/api/{p1}") in ROUTE_TABLE
    reserved = {"_jobs", "config", "dev", "disable", "enable", "manifest", "migrate-cleanup", "open", "token", "uninstall", "update"}
    assert not any(p.split("/")[1] in reserved for _, p in ROUTE_TABLE)


def test_spa_response_serves_only_enabled_files_safely(tmp_path: Path):
    spa = tmp_path / "spa"
    (spa / "ui" / "lib").mkdir(parents=True)
    (spa / "ui" / "main.js").write_text("export default 1;", encoding="utf-8")
    (spa / "ui" / "lib" / "util.mjs").write_text("export const u = 1;", encoding="utf-8")
    (spa / "ui" / "style.css").write_text("body{}", encoding="utf-8")
    (spa / "ui" / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    status, headers, body = spa_response(spa, "ui", ["main.js"])
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and headers["Cache-Control"] == "no-cache" and body == b"export default 1;"
    assert spa_response(spa, "ui", ["lib", "util.mjs"])[0] == 200
    assert spa_response(spa, "ui", ["style.css"])[1]["Content-Type"].startswith("text/css")
    for bad in (["..", "..", "secret.txt"], ["../secret.txt"], [".hidden"], ["lib"], ["missing.js"], [""]):
        assert spa_response(spa, "ui", bad)[0] == 404, bad
    assert spa_response(spa, "other", ["main.js"])[0] == 404 and spa_response(spa, "..", ["main.js"])[0] == 404
    assert content_type_for("x.json").startswith("application/json") and content_type_for("x.bin") == "application/octet-stream"


def test_state_health_and_fault_responses_through_a_booted_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(sys.modules, "kiro_crew", None)
    paths = FloofyPaths(tmp_path / "home" / "floofy").ensure()
    write_consent(paths.consent, by="tests")
    make_mod(paths, "alpha", extra={"kirocrew": {"version": "*"}})
    make_mod(paths, "ui", code=None, parts=[{"kind": "spa", "side": "spa", "path": "spa/main.js"}], files={"spa/main.js": "export default 1;\n"}, extra={"kirocrew": {"version": "*"}})
    paths.enabled.write_text(json.dumps({"alpha": True, "ui": True}), encoding="utf-8")
    runtime = LoaderRuntime()
    runtime.startup(types.SimpleNamespace(name="floofycrew", data_dir=tmp_path, logger=None))
    try:
        status, headers, body = state_response(runtime)
        state = json.loads(body)
        assert status == 200 and headers["Content-Type"].startswith("application/json")
        assert state["loader"] == "ok" and state["active"] == ["alpha", "ui"] and state["unofficial"] is True and state["api_version"] == api.API_VERSION
        assert state["events"]["history"][-1]["event"] == "loader.state" and {e["event"] for e in state["events"]["history"]} >= {"mod.activated", "loader.state"}
        assert api.events is runtime.events
        health = json.loads(health_response(runtime)[2])
        assert health == {"ok": True, "loader": "ok", "active": ["alpha", "ui"], "bootedAt": state["bootedAt"], "unofficial": True, "api_version": api.API_VERSION, "loaderVersion": state["loaderVersion"]}
        assert spa_response(paths.spa, "ui", ["main.js"])[0] == 200
        # the SPA host reports a broken module
        assert fault_response(runtime, "nope", {"message": "x"})[0] == 404 and fault_response(runtime, "ui", "not a dict")[0] == 400
        status, _, body = fault_response(runtime, "ui", {"message": "TypeError: boom", "stack": "at main.js:1", "source": "spa"})
        verdict = json.loads(body)
        assert status == 200 and verdict["faulted"] is True and verdict["mod"]["reason"] == Reason.Error.value and verdict["mod"]["parts"][0]["status"] == "error"
        assert spa_response(paths.spa, "ui", ["main.js"])[0] == 404, "a faulted mod's spa files are no longer served"
        assert runtime.state_dict()["faults"][0]["source"] == "spa"
        assert [e["event"] for e in list(runtime.events.history)[-2:]] == ["mod.deactivated", "mod.faulted"]
        written = json.loads(paths.loader_state.read_text(encoding="utf-8"))
        assert written["mods"]["ui"]["reason"] == "Error" and written["mods"]["alpha"]["active"] is True
        # ctx.config / ctx.log landed under mods/<id>/.floofy
        ctx = runtime.result.activations["alpha"].ctx
        ctx.config.set("k", 1)
        ctx.log.info("from the mod")
        assert (paths.mod_dir("alpha") / RUNTIME_DIR / "config.json").is_file() and "[floofy:alpha] from the mod" in (paths.mod_dir("alpha") / RUNTIME_DIR / "mod.log").read_text(encoding="utf-8")
        assert ctx.data_dir == (paths.mod_dir("alpha") / RUNTIME_DIR).resolve()
        # reload picks up a changed enabled.json and re-announces
        paths.enabled.write_text(json.dumps({"alpha": False, "ui": True}), encoding="utf-8")
        runtime.reload()
        assert runtime.result.state.mods["alpha"].reason == Reason.UserDisabled.value and runtime.result.state.mods["ui"].active
        assert runtime.events.history[-1]["event"] == "loader.state" and runtime.hooks.registrations() == []
    finally:
        runtime.shutdown(None)
