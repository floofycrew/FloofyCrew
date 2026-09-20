"""Host-version-change handling, the quarantine and `hold` (task 6.5; Requirement 6.2, 6.3, 6.6).

Fake payloads at two versions in temp dirs stand in for a host update and a
rollback; the anchors are checked against the real 0.7.0.5 sources when the
payload copy exists; ``hold`` is exercised through a fake adapter hook here and
against each edition's real hook in the edition test packages.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from floofy_core.anchors import check_anchors, load_anchors
from floofy_core.audit import read_audit
from floofy_core.cli.main import run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import read_enabled

from floofy_testing import REPO_ROOT, fake_payload
from test_cli_mods import write_mod

PAYLOAD_COPY = REPO_ROOT / ".scratch" / "payload-0.7.0.5"


def _descriptor(fingerprint: str, marker: str) -> dict:
    return {"schema": 1, "target": "kiro_crew/static/dist/index.html", "appliesTo": ">=0.7.0 <0.9.0", "ops": [{"op": "insert-before", "fingerprint": fingerprint, "content": f'<meta name="{marker}" content="1">', "marker": marker}]}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two fake payload roots (0.7.0.5 and 0.7.0.6), a temp home with consent, three mods with different fates."""
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    roots = tmp_path / "roots"
    old = roots / "0.7.0.5"
    fake_payload(old, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")

    class World:
        home_dir = home
        paths = data
        old_root = old
        new_root = roots / "0.7.0.6"
        work = tmp_path / "work"

        def argv(self, *args: str, roots_: list[Path] | None = None) -> list[str]:
            argv = ["--home", str(home)]
            for root in roots_ if roots_ is not None else [old]:
                argv += ["--root", str(root)]
            return [*argv, *args]

        def run(self, *args: str, roots_: list[Path] | None = None, **kw):
            return run(self.argv(*args, roots_=roots_), non_interactive=True, actor="test", **kw)

        def land_new_version(self) -> Path:
            """A host update: a second payload whose shell lost the fingerprint one patch relies on."""
            shell = (
                '<!doctype html>\n<html lang="en" data-theme="dark">\n  <head>\n    <meta charset="UTF-8" />\n    <title>KiroCrew</title>\n'
                '    <script type="importmap">{"imports":{"react":"/vendor/react.mjs"}}</script>\n'
                '    <script type="module" crossorigin src="/assets/main-NEWHASH1.js"></script>\n  </head>\n  <body><div id="root"></div></body>\n</html>\n'
            )
            fake_payload(self.new_root, "0.7.0", build="0.7.0.6", index_html=shell)
            return self.new_root

    world = World()
    world.work.mkdir()
    # a patch mod anchored on the <title> line: survives the update
    write_mod(world.work / "steady", "steady", parts=[{"kind": "patch", "side": "spa", "path": "patches/p.json"}], files={"patches/p.json": json.dumps(_descriptor("<title>KiroCrew</title>", "floofy-steady"))})
    # a patch mod anchored on the modulepreload hint: the new shell has none → fingerprint miss → yeet
    write_mod(world.work / "fragile", "fragile", parts=[{"kind": "patch", "side": "spa", "path": "patches/p.json"}], files={"patches/p.json": json.dumps(_descriptor('<link rel="modulepreload" crossorigin href="/assets/chunk-a-AAAA1111.js">', "floofy-fragile"))})
    # a strict skill mod whose range ends before 0.7.0 patch bumps? no: strict range that excludes nothing yet; and one the matrix breaks
    write_mod(world.work / "matrixed", "matrixed", parts=[{"kind": "skill", "side": "gateway", "path": "skills/matrixed/SKILL.md"}], files={"skills/matrixed/SKILL.md": "# m\n"})
    write_mod(world.work / "loose", "loose", parts=[{"kind": "agent", "side": "gateway", "path": "agents/loose.json"}], files={"agents/loose.json": "{}"}, extra={"kirocrew": {"version": ">=0.6.0 <0.7.0", "strict": False}})
    for mod in ("steady", "fragile", "matrixed", "loose"):
        result = world.run("--yes", "install", str(world.work / mod))
        assert result.exit == 0, result.stderr
    return world


def test_anchors_match_the_real_host_sources():
    anchors = load_anchors()
    assert len(anchors) >= 12 and all(a["module"].startswith("kiro_crew") for a in anchors)
    if not PAYLOAD_COPY.is_dir():
        pytest.skip("no payload copy for the real anchor check")
    package_root = PAYLOAD_COPY / "lib" / "python3.12" / "site-packages"
    report = check_anchors(package_root, host_version="0.7.0.5")
    assert report.ok, report.missed
    assert report.to_dict()["pythonAnchors"]["matched"] == len(anchors)
    # a made-up anchor is missed with a reason
    missed = check_anchors(package_root, [{"module": "kiro_crew.apps.manager", "attr": "no_such_function"}, {"module": "kiro_crew.nope", "attr": None}])
    assert [m["name"] for m in missed.missed] == ["kiro_crew.apps.manager:no_such_function", "kiro_crew.nope"]


def test_first_apply_if_changed_records_state_and_host_change_outcome(world):
    first = world.run("--json", "apply", "--if-changed", "--no-verify")
    assert first.exit == 0, first.stderr
    assert first.json["change"]["firstRun"] is True and first.json["reapplied"] is True
    assert first.json["anchors"]["pythonAnchors"]["total"] >= 12 and first.json["reporter"].get("spaFingerprints") is not None
    assert first.json["yeeted"] == [], "nothing breaks on the version the mods were installed on"
    warnings = [w for d in first.json["decisions"] for w in d["warnings"]]
    assert any("loose" in d["id"] and d["warnings"] for d in first.json["decisions"]) and any("non-strict" in w for w in warnings)
    shell = (world.old_root / "kiro_crew" / "static" / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'name="floofy-steady"' in shell and 'name="floofy-fragile"' in shell
    assert world.paths.hostchange("0.7.0.5").is_file() and world.paths.host_state.is_file()
    quiet = world.run("--json", "apply", "--if-changed", "--no-verify")
    assert quiet.json["change"]["changed"] is False


def test_update_yeets_broken_mods_and_rollback_restores_them(world):
    world.run("apply", "--if-changed", "--no-verify")
    # the compat matrix marks `matrixed` broken on the new version
    world.paths.cache.mkdir(exist_ok=True)
    world.paths.compat_cache.write_text(json.dumps({"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.6", "framework": {"loader": "ok"}, "mods": {"matrixed@1.0.0": {"verdict": "broken", "run": "https://example.invalid/run/1"}}}]}), encoding="utf-8")
    new_root = world.land_new_version()
    shutil.rmtree(world.old_root)  # the updater removed the old version dir
    # the Loader asked for `loose` to be parked (a quarantine request)
    world.paths.quarantine_requests.mkdir(parents=True, exist_ok=True)
    (world.paths.quarantine_requests / "loose.json").write_text(json.dumps({"mod": "loose", "reason": "test request"}), encoding="utf-8")
    result = world.run("--json", "apply", "--if-changed", "--no-verify", roots_=[new_root])
    assert result.exit == 0, result.stderr
    change = result.json["change"]
    assert change["versionChanged"] is True and change["previousCurrent"] == "0.7.0.5" and change["current"] == "0.7.0.6"
    yeeted = {y["id"]: y for y in result.json["yeeted"]}
    assert set(yeeted) == {"fragile", "matrixed", "loose"}, result.json["decisions"]
    assert any("fingerprint" in r for r in yeeted["fragile"]["reasons"]) and any("matrix" in r for r in yeeted["matrixed"]["reasons"]) and any("loader request" in r for r in yeeted["loose"]["reasons"])
    assert all(y["hostVersion"] == "0.7.0.5" for y in yeeted.values()), "parked under the version they last worked on"
    quarantine = world.paths.quarantine_dir("0.7.0.5")
    assert (quarantine / "fragile" / "floofy.json").is_file() and (quarantine / "matrixed" / "floofy.json").is_file()
    assert read_enabled(quarantine / "enabled.json") == {"fragile": True, "matrixed": True, "loose": True}
    assert read_enabled(world.paths.enabled) == {"steady": True}
    assert not (world.home_dir / "skills" / "matrixed").exists(), "the yeeted skill's seam part was uninstalled"
    assert not (world.paths.quarantine_requests / "loose.json").exists()
    shell = (new_root / "kiro_crew" / "static" / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'name="floofy-steady"' in shell and 'name="floofy-fragile"' not in shell
    outcome = json.loads(world.paths.hostchange("0.7.0.6").read_text(encoding="utf-8"))
    assert outcome["previousVersion"] == "0.7.0.5" and [y["id"] for y in outcome["yeeted"]] == ["fragile", "loose", "matrixed"]
    ops = [r["op"] for r in read_audit(world.paths.audit)]
    assert ops.count("yeet") == 3 and "host-change" in ops and ops[-1] == "apply-if-changed"
    listing = world.run("--json", "yeet", "--list", roots_=[new_root])
    assert listing.json["quarantine"] == {"0.7.0.5": ["fragile", "loose", "matrixed"]}
    status = world.run("--json", "status", roots_=[new_root])
    assert status.json["quarantine"]["0.7.0.5"] == ["fragile", "loose", "matrixed"]

    # rollback: the 0.7.0.5 payload is back and current → its set is restored automatically
    fake_payload(world.old_root, "0.7.0", build="0.7.0.5")
    shutil.rmtree(new_root)
    rolled = world.run("--json", "apply", "--if-changed", "--no-verify")
    assert rolled.exit == 0, rolled.stderr
    restored = {r["id"]: r for r in rolled.json["rollbackRestore"]["restored"]}
    assert set(restored) == {"fragile", "loose", "matrixed"} and all(r["enabled"] is True for r in restored.values())
    assert read_enabled(world.paths.enabled) == {"fragile": True, "loose": True, "matrixed": True, "steady": True}
    assert (world.home_dir / "skills" / "matrixed" / "SKILL.md").is_file(), "the seam part came back with the mod"
    assert not world.paths.quarantine_dir("0.7.0.5").exists()
    shell = (world.old_root / "kiro_crew" / "static" / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'name="floofy-fragile"' in shell, "the restored patch applies again on the version it works on"


def test_manual_yeet_and_restore(world):
    parked = world.run("--json", "yeet", "steady", "--reason", "testing")
    assert parked.exit == 0 and parked.json["yeeted"][0]["hostVersion"] == "0.7.0.5"
    assert not (world.paths.mods / "steady").exists() and (world.paths.quarantine_dir("0.7.0.5") / "steady").is_dir()
    assert "steady" not in read_enabled(world.paths.enabled)
    world.paths.consent.unlink()
    no_consent = world.run("yeet", "--restore", "0.7.0.5")
    assert no_consent.exit == 3, "restoring applies mods again: consent is required"
    write_consent(world.paths.consent, by="tests", how="test")
    back = world.run("--json", "yeet", "--restore", "0.7.0.5", "steady")
    assert back.exit == 0 and [r["id"] for r in back.json["restored"]] == ["steady"]
    assert (world.paths.mods / "steady" / "floofy.json").is_file() and read_enabled(world.paths.enabled)["steady"] is True
    missing = world.run("--json", "yeet", "--restore", "9.9.9")
    assert missing.exit == 0 and "no quarantine" in missing.json["detail"]


def test_hold_is_explicit_confirmed_and_audited(world, monkeypatch: pytest.MonkeyPatch):
    """The CLI flow around the adapter's update_hold(): declined → nothing runs; confirmed → the hook runs and is audited; no hook → explained."""
    import types

    import floofy_core.cli.context as context_module

    calls: list[dict] = []

    def update_hold(host_home, *, duration, release):
        calls.append({"duration": duration, "release": release})
        return {"ok": True, "detail": "paused" if not release else "resumed", "command": ["fake-tool", "--pause", str(duration)] if not release else ["fake-tool", "--unpause"]}

    adapter = types.SimpleNamespace(EDITION="internal", update_hold=update_hold)
    monkeypatch.setattr(context_module.CliContext, "adapters", lambda self: [adapter])
    declined = world.run("hold")
    assert declined.exit == 1 and calls == [], "non-interactive without --yes: nothing runs"
    held = world.run("--yes", "--json", "hold", "--duration", "3d")
    assert held.exit == 0 and calls == [{"duration": "3d", "release": False}] and held.json["hostCommand"] == ["fake-tool", "--pause", "3d"]
    released = world.run("--yes", "--json", "hold", "--release")
    assert released.exit == 0 and calls[-1] == {"duration": None, "release": True}
    rows = [r for r in read_audit(world.paths.audit) if r["op"] == "hold"]
    assert [r["result"] for r in rows] == ["declined", "ok", "ok"] and rows[1]["hostCommand"] == ["fake-tool", "--pause", "3d"]
    # an edition without a hook: explained and audited, never a default
    monkeypatch.setattr(context_module.CliContext, "adapters", lambda self: [types.SimpleNamespace(EDITION="internal")])
    none = world.run("--yes", "--json", "hold")
    assert none.exit == 0 and none.json["available"] is False and read_audit(world.paths.audit)[-1]["result"] == "unavailable"
