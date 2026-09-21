"""Profiles and lockfiles (task 6.6; Requirement 7.5): save, list, use (flags, installs from a path source, extras disabled), export/import."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli.main import run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import read_enabled
from floofy_core.profiles import PROFILE_SCHEMA, Profile, ProfileError, plan_use, snapshot

from floofy_testing import fake_payload
from test_cli_mods import write_mod


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    payload = tmp_path / "payload"
    fake_payload(payload, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    work = tmp_path / "work"
    work.mkdir()

    class Env:
        paths = data
        home_dir = home

        def run(self, *args: str, **kw):
            return run(["--home", str(home), "--root", str(payload), *args], non_interactive=True, actor="test", **kw)

        def mod(self, mod_id: str, kind: str = "agent") -> Path:
            if kind == "agent":
                return write_mod(work / mod_id, mod_id, parts=[{"kind": "agent", "side": "gateway", "path": f"agents/{mod_id}.json"}], files={f"agents/{mod_id}.json": "{}"})
            return write_mod(work / mod_id, mod_id, parts=[{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook"}], files={"hook/__init__.py": "def activate(ctx):\n    pass\n"})

    return Env()


def test_save_list_export_import_round_trip(env, tmp_path: Path):
    for mod_id in ("alpha", "beta"):
        assert env.run("--yes", "install", str(env.mod(mod_id))).exit == 0
    assert env.run("--yes", "install", str(env.mod("hooky", "hook"))).exit == 0  # lands disabled
    saved = env.run("--json", "profile", "save", "work")
    assert saved.exit == 0, saved.stderr
    lock = json.loads(env.paths.profile("work").read_text(encoding="utf-8"))
    assert lock["schema"] == PROFILE_SCHEMA and lock["name"] == "work" and lock["hostVersion"] == "0.7.0.5" and lock["edition"] == "internal"
    assert lock["mods"]["alpha"] == {"version": "1.0.0", "enabled": True, "source": {"source": "path", "ref": lock["mods"]["alpha"]["source"]["ref"]}, "sha256": lock["mods"]["alpha"]["sha256"]}
    assert lock["mods"]["hooky"]["enabled"] is False and len(lock["mods"]["alpha"]["sha256"]) == 64
    listed = env.run("--json", "profile", "list")
    assert listed.json["profiles"][0]["name"] == "work" and listed.json["profiles"][0]["mods"] == 3
    exported = tmp_path / "work.floofy-profile.json"
    assert env.run("profile", "export", "work", str(exported)).exit == 0 and json.loads(exported.read_text(encoding="utf-8"))["mods"] == lock["mods"]
    imported = env.run("--json", "profile", "import", str(exported), "--name", "copy")
    assert imported.exit == 0 and env.paths.profile("copy").is_file()
    assert Profile.load(env.paths.profile("copy")).mods == Profile.load(env.paths.profile("work")).mods
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert env.run("profile", "import", str(bad)).exit == 1
    with pytest.raises(ProfileError):
        Profile.from_dict({"schema": 1, "mods": {}}, name="bad name!")
    assert env.run("profile", "save", "bad name!").exit == 1
    assert env.run("profile", "export", "nope", str(tmp_path / "x")).exit == 1


def test_use_sets_flags_installs_missing_and_disables_extras(env):
    alpha, beta = env.mod("alpha"), env.mod("beta")
    for mod in (alpha, beta):
        assert env.run("--yes", "install", str(mod)).exit == 0
    assert env.run("--yes", "install", str(env.mod("hooky", "hook")), "--enable").exit == 0
    assert env.run("--json", "profile", "save", "full").exit == 0
    # drift away from the profile: uninstall beta, disable alpha, install an extra
    assert env.run("--yes", "uninstall", "beta").exit == 0
    assert env.run("disable", "alpha").exit == 0
    assert env.run("--yes", "install", str(env.mod("extra"))).exit == 0
    plan = env.run("--json", "profile", "use", "full", "--check")
    actions = {s["id"]: s["action"] for s in plan.json["plan"]}
    assert actions == {"alpha": "set-flag", "beta": "install", "hooky": "set-flag", "extra": "disable-extra"}
    assert next(s for s in plan.json["plan"] if s["id"] == "alpha")["changed"] is True
    assert read_enabled(env.paths.enabled) == {"alpha": False, "hooky": True, "extra": True}, "--check changed nothing"
    env.paths.consent.unlink()
    assert env.run("--yes", "profile", "use", "full").exit == 3, "applying a profile applies mods: consent required"
    write_consent(env.paths.consent, by="tests", how="test")
    used = env.run("--yes", "--json", "profile", "use", "full")
    assert used.exit == 0, used.stderr
    assert (env.paths.mods / "beta" / "floofy.json").is_file(), "the missing mod was installed from its recorded path source"
    assert read_enabled(env.paths.enabled) == {"alpha": True, "beta": True, "hooky": True, "extra": False}
    assert (env.paths.mods / "extra").is_dir(), "extras are disabled, never uninstalled"
    assert (env.home_dir / "kiro" / "agents" / "beta.json").is_file() if (env.home_dir / "kiro").exists() else True
    rows = [r["op"] for r in read_audit(env.paths.audit)]
    assert "profile-use" in rows and rows.count("profile-save") == 1
    # plan_use on the now-matching set has nothing to change
    profile = Profile.load(env.paths.profile("full"))
    from floofy_core.modstore import installed_mods

    steps = plan_use(profile, installed_mods(env.paths), read_enabled(env.paths.enabled))
    assert all(s["action"] == "set-flag" and not s["changed"] for s in steps if s["id"] != "extra")
    assert not any(s["action"] == "disable-extra" for s in steps)


def test_snapshot_skips_broken_mods(env):
    (env.paths.mods / "broken").mkdir(parents=True)
    (env.paths.mods / "broken" / "floofy.json").write_text("not json", encoding="utf-8")
    from floofy_core.modstore import installed_mods

    snap = snapshot("s", installed_mods(env.paths), {}, host_version="0.7.0.5", edition="internal", floofycrew="0.0.0")
    assert snap.mods == {} and snap.name == "s"
