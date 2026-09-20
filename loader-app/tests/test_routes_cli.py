"""The manager UI's routes (task 6.7): ``POST /cli`` runs allowlisted commands in-process, ``GET /registry`` reports updates."""
from __future__ import annotations

import json
import types
from pathlib import Path

from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import read_enabled
from floofy_loader.host import read_host_facts
from floofy_loader.routes import CLI_ALLOWLIST, ROUTE_TABLE, cli_response, registry_response
from floofy_loader.runtime import LoaderRuntime

from floofy_testing import fake_payload
from test_boot import make_mod


def _runtime(tmp_path: Path, monkeypatch) -> tuple[LoaderRuntime, DataHome]:
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    package_dir = fake_payload(tmp_path / "payload", "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0.5"
    module.__file__ = str(package_dir / "__init__.py")
    runtime = LoaderRuntime()
    runtime.facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(home)}, adapters=[])
    runtime.paths = data
    return runtime, data


def _body(reply) -> dict:
    return json.loads(reply[2].decode("utf-8"))


def test_cli_route_is_read_only_and_runs_in_process(tmp_path: Path, monkeypatch):
    runtime, data = _runtime(tmp_path, monkeypatch)
    make_mod(data, "alpha")
    data.enabled.write_text(json.dumps({"alpha": False}), encoding="utf-8")
    assert ("POST", "/cli") in ROUTE_TABLE and ("GET", "/registry") in ROUTE_TABLE
    # task 11.1: every mutation has a typed route with the confirmation protocol (test_app_routes.py); /cli keeps the read-only commands
    assert CLI_ALLOWLIST == {"status", "doctor", "search", "info", "list", "audit", "which"}
    refused = cli_response(runtime, {"argv": ["init", "--i-accept-the-risk"]}, reload=False)
    assert refused[0] == 403 and "/consent" in _body(refused)["error"] and _body(refused)["route"].startswith("POST /consent")
    assert cli_response(runtime, {"argv": ["--home", "/tmp/x", "install", "/tmp/mod"]}, reload=False)[0] == 403
    for argv in (["enable", "alpha"], ["disable", "alpha"], ["uninstall", "alpha"], ["update", "--all"], ["yeet", "alpha"], ["apply"], ["profile", "list"], ["vanilla"], ["registry", "list"]):
        assert cli_response(runtime, {"argv": argv}, reload=False)[0] == 403, argv
    assert cli_response(runtime, {"nope": 1}, reload=False)[0] == 400
    assert cli_response(runtime, {"argv": ["status", 5]}, reload=False)[0] == 400
    status = _body(cli_response(runtime, {"argv": ["status"]}, reload=False))
    assert status["ok"] is True and status["command"] == "status" and status["json"]["mods"][0]["id"] == "alpha" and status["json"]["mods"][0]["enabled"] is False
    assert status["json"]["loader"]["source"] == "in-process", "inside the gateway the CLI reads the runtime's live state"
    assert read_enabled(data.enabled) == {"alpha": False} and not data.audit.exists(), "nothing mutated, no audit row"


def test_registry_route_reports_update_availability(tmp_path: Path, monkeypatch):
    runtime, data = _runtime(tmp_path, monkeypatch)
    make_mod(data, "alpha", version="1.0.0")
    empty = _body(registry_response(runtime))
    assert empty["ok"] is True and empty["updates"]["alpha"]["inRegistry"] is False and empty["updates"]["alpha"]["update"] is False
    assert empty["sources"] == [] and empty["compat"]["row"] is None
    data.index_cache.write_text(json.dumps({"schema": 1, "source": "test", "mods": [{"id": "alpha", "name": "Alpha", "repo": "https://forge.example/alpha", "versions": [{"version": "1.1.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "expected"}, "files": [{"url": "https://example.invalid/a.zip", "sha256": "0" * 64}], "changelog": "https://forge.example/alpha/releases/1.1.0"}]}]}), encoding="utf-8")
    document = _body(registry_response(runtime))
    assert document["updates"]["alpha"] == {"installed": "1.0.0", "candidate": "1.1.0", "verdict": "expected", "update": True, "inRegistry": True, "why": "", "installedVerdict": None, "tier": "unlisted", "candidateTier": "listed", "changelog": "https://forge.example/alpha/releases/1.1.0", "repo": "https://forge.example/alpha", "key": "alpha"}
    # without a changelog URL the record's repository is the link (Requirement 16.6)
    data.index_cache.write_text(json.dumps({"schema": 1, "source": "test", "mods": [{"id": "alpha", "name": "Alpha", "repo": "https://forge.example/alpha", "versions": [{"version": "1.1.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "expected"}, "files": [{"url": "https://example.invalid/a.zip", "sha256": "0" * 64}]}]}]}), encoding="utf-8")
    assert _body(registry_response(runtime))["updates"]["alpha"]["changelog"] == "https://forge.example/alpha"
    assert document["tiers"] == {"alpha": "unlisted"}, "a mod without an install record is unlisted (Requirement 8.11)"
    assert document["cache"]["mods"] == 1
    # the matrix row for this host grades the candidates (Requirement 9.2) and the sources carry their last verdict
    facts = runtime.facts
    data.compat_cache.write_text(json.dumps({"schema": 1, "rows": [{"edition": facts.edition, "channel": facts.channel or "beta", "hostVersion": facts.version, "framework": {"loader": "ok"}, "mods": {"alpha@1.1.0": "broken", "alpha@1.0.0": "tested"}}]}), encoding="utf-8")
    data.registries.write_text(json.dumps({"schema": 1, "sources": [{"url": "https://registry.example/", "name": "ex", "trust": "index", "allowUnsigned": False, "keyId": None, "addedAt": "2026-01-01T00:00:00Z"}]}), encoding="utf-8")
    key_dir = data.cache_sources / __import__("floofy_core.registry_sources", fromlist=["source_key"]).source_key("https://registry.example/")
    key_dir.mkdir(parents=True)
    (key_dir / "meta.json").write_text(json.dumps({"usable": True, "signature": {"status": "verified", "keyId": "a" * 16}, "mods": 1}), encoding="utf-8")
    graded = _body(registry_response(runtime))
    alpha = graded["updates"]["alpha"]
    assert alpha["update"] is False and alpha["candidate"] is None and "1.1.0 (broken on" in alpha["why"] and alpha["installedVerdict"] == "tested"
    assert graded["compat"]["row"]["mods"] == {"alpha@1.0.0": "tested", "alpha@1.1.0": "broken"}
    assert graded["sources"][0]["label"] == "ex" and graded["sources"][0]["cache"]["signature"]["status"] == "verified" and graded["sources"][0]["allowUnsigned"] is False
    # a registry install record lifts the tier to listed, and the matrix row's `tested` cell for this host to tested
    (data.mods / "alpha" / ".floofy").mkdir(parents=True, exist_ok=True)
    (data.mods / "alpha" / ".floofy" / "source.json").write_text(json.dumps({"source": "registry", "ref": "alpha@1.0.0", "tier": "listed", "registryKey": "alpha"}), encoding="utf-8")
    tested = _body(registry_response(runtime))
    assert tested["tiers"] == {"alpha": "tested"} and tested["updates"]["alpha"]["tier"] == "tested"
