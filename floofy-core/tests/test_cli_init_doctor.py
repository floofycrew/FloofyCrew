"""``floofy`` CLI skeleton, ``init``, ``deinit``, ``doctor``, ``status``, ``--vanilla`` (task 6.1; Requirement 6.1, 7.1, 7.2, 7.4, 11.1).

Every test runs against a temp host home (``--home``) with ``FLOOFY_NO_ADAPTERS=1``
(armed by ``floofy_testing``) and fake payloads under ``--root``; nothing reaches
a live install. The host CLI is a fake ``kirocrew`` script that records its calls.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from floofy_core import API_VERSION, __version__ as FRAMEWORK_VERSION
from floofy_core.audit import read_audit
from floofy_core.cli.console import Console
from floofy_core.cli.main import build_parser, execute, main, run
from floofy_core.consent import ACCEPT_PHRASE, WARNING_TEXT, read_consent
from floofy_core.datahome import DataHome

from floofy_testing import REPO_ROOT, FakeGateway, fake_payload

REQUIRED_COMMANDS = {
    "init", "deinit", "doctor", "status", "search", "install", "uninstall", "enable", "disable", "update", "apply", "restore",
    "verify", "profile", "yeet", "hold", "validate", "new", "dev", "registry", "vanilla",
}

FAKE_KIROCREW = """#!/bin/sh
# fake host CLI: records every call, answers like the host for app install/enable/uninstall
echo "$@" >> "$FAKE_KIROCREW_LOG"
case "$1 $2" in
  "app install")
    name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]+'/app.json'))['name'])" "$3")
    # like the host (apps/execution.py _repository_grant_denied_for_binding): a name in apps_trusted with neither a
    # local marker nor a repository binding is a legacy grant, refused for a fresh install of that name
    if python3 -c "import json,sys; c=json.load(open(sys.argv[1])); a=c.get('agent',{}); n=sys.argv[2]; sys.exit(0 if n in a.get('apps_trusted',[]) and n not in a.get('apps_trusted_local',[]) and n not in a.get('apps_trusted_repositories',{}) else 1)" "$KIROCREW_HOME/config.json" "$name" 2>/dev/null; then
      echo "execution trust predates repository binding and is inactive for repository-backed code; refresh the app listing, review its current repository, and grant trust again" >&2
      exit 1
    fi
    mkdir -p "$KIROCREW_HOME/apps/$name"
    cp -r "$3"/. "$KIROCREW_HOME/apps/$name/"
    printf '{"name": "%s", "version": "0.0.0", "enabled": false}\\n' "$name" > "$KIROCREW_HOME/apps/$name/installed.json"
    echo "installed $name v0.0.0"
    ;;
  "app enable")
    # like the host: the name grant plus its binding (local marker for a directory/archive install, or a repository)
    if python3 -c "import json,sys; c=json.load(open(sys.argv[1])); a=c.get('agent',{}); n=sys.argv[2]; sys.exit(0 if n in a.get('apps_trusted',[]) and (n in a.get('apps_trusted_local',[]) or n in a.get('apps_trusted_repositories',{})) else 1)" "$KIROCREW_HOME/config.json" "$3" 2>/dev/null; then
      printf '{"name": "%s", "version": "0.0.0", "enabled": true}\\n' "$3" > "$KIROCREW_HOME/apps/$3/installed.json"
      echo "enabled $3"
    else
      echo "blocked by execution policy: third-party app execution is disabled" >&2
      exit 1
    fi
    ;;
  "app uninstall")
    rm -rf "$KIROCREW_HOME/apps/$3"
    # the host revokes the per-app grant on uninstall (apps/manager.py L1452 "Remove *name* from agent.apps_trusted")
    python3 - "$KIROCREW_HOME/config.json" "$3" <<'PY' 2>/dev/null
import json, sys
path, name = sys.argv[1], sys.argv[2]
try:
    doc = json.load(open(path))
except (OSError, ValueError):
    sys.exit(0)
agent = doc.get("agent") or {}
trusted = agent.get("apps_trusted")
changed = False
if isinstance(trusted, list) and name in trusted:
    agent["apps_trusted"] = [n for n in trusted if n != name]; changed = True
local = agent.get("apps_trusted_local")
if isinstance(local, list) and name in local:
    agent["apps_trusted_local"] = [n for n in local if n != name]; changed = True
if changed:
    doc["agent"] = agent
    json.dump(doc, open(path, "w"), indent=2)
PY
    echo "uninstalled $3"
    ;;
  *)
    echo "fake kirocrew: $*"
    ;;
esac
"""


@pytest.fixture
def fake_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fake payload (stamped 0.7.0.5), a temp host home, and a fake ``kirocrew`` launcher."""
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    home.mkdir()
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text(FAKE_KIROCREW, encoding="utf-8")
    launcher.chmod(0o755)
    log = tmp_path / "kirocrew.log"
    monkeypatch.setenv("FAKE_KIROCREW_LOG", str(log))
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)

    class Host:
        root = payload_root
        home_dir = home
        data = DataHome.for_host_home(home)
        kirocrew = launcher
        calls = log

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), "--kirocrew", str(launcher), *args]

        def run(self, *args: str, **kw):
            return run(self.argv(*args), non_interactive=True, actor="test", **kw)

        def called(self) -> list[str]:
            return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []

    return Host()


def test_every_required_command_is_registered_and_version_prints_unofficial():
    parser = build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)  # type: ignore[union-attr]
    assert REQUIRED_COMMANDS <= choices, REQUIRED_COMMANDS - choices
    result = run(["--version"])
    assert result.exit == 0 and FRAMEWORK_VERSION in result.stdout and API_VERSION in result.stdout and "unofficial" in result.stdout
    assert result.json["unofficial"] is True


def test_unimplemented_command_names_its_task(fake_host):
    """Commands of a later task print the task and exit 2 (none are left once package 6 is complete)."""
    from floofy_core.cli.main import _LANDS_LATER

    if not _LANDS_LATER:
        pytest.skip("every command has landed")
    name = next(iter(_LANDS_LATER))
    result = fake_host.run(name, "x")
    assert result.exit == 2 and "lands in task" in result.stderr


def test_init_refuses_without_the_typed_acceptance_and_yes_does_not_help(fake_host):
    result = fake_host.run("init", "--no-loader-app", "--no-trigger")
    assert result.exit == 3 and "consent not given" in result.stderr
    assert not fake_host.data.consent.exists()
    with_yes = fake_host.run("--yes", "init", "--no-loader-app", "--no-trigger")
    assert with_yes.exit == 3 and not fake_host.data.consent.exists(), "--yes never accepts the warning"
    # a wrong phrase typed interactively is refused too
    console = Console(input_fn=lambda _prompt: "yes", non_interactive=False)
    code, _ = execute(fake_host.argv("init", "--no-loader-app", "--no-trigger"), console)
    assert code == 3 and not fake_host.data.consent.exists()
    assert WARNING_TEXT.splitlines()[0] in "\n".join(console.transcript), "the warning was shown"


def test_init_with_the_typed_phrase_records_typed_consent(fake_host):
    console = Console(input_fn=lambda _prompt: ACCEPT_PHRASE, non_interactive=False)
    code, result = execute(fake_host.argv("init", "--no-loader-app", "--no-trigger"), console)
    assert code == 0, console.transcript
    status = read_consent(fake_host.data.consent)
    assert status.ok and status.how == "typed" and status.by
    assert result["consent"]["status"] == "recorded"
    # second init: consent already recorded, no prompt
    console2 = Console(input_fn=lambda _prompt: pytest.fail("must not ask again"), non_interactive=False)
    code2, result2 = execute(fake_host.argv("init", "--no-loader-app", "--no-trigger"), console2)
    assert code2 == 0 and result2["consent"]["status"] == "existing"


def test_init_installs_the_loader_app_offers_the_grant_and_enables(fake_host):
    result = fake_host.run("--yes", "init", "--i-accept-the-risk", "--no-trigger")
    assert result.exit == 0, result.stderr + result.stdout
    consent = read_consent(fake_host.data.consent)
    assert consent.ok and consent.how == "flag"
    calls = fake_host.called()
    assert any(c.startswith("app install") for c in calls) and "app enable floofycrew" in calls
    config = json.loads((fake_host.home_dir / "config.json").read_text(encoding="utf-8"))
    assert config["agent"]["apps_trusted"] == ["floofycrew"] and config["agent"]["apps_trusted_local"] == ["floofycrew"], "the grant was written with the user's (--yes) confirmation, in the host's local-source shape"
    assert "floofycrew" not in config["agent"].get("apps_trusted_repositories", {})
    installed = json.loads((fake_host.home_dir / "apps" / "floofycrew" / "installed.json").read_text(encoding="utf-8"))
    assert installed["enabled"] is True
    assert (fake_host.home_dir / "apps" / "floofycrew" / "floofy_core" / "cli" / "main.py").is_file(), "the vendored core is in the installed app"
    assert result.json["loaderApp"]["hostVerdict"]["appExecutionAllowed"] is False
    assert result.json["loaderApp"]["grant"]["written"] is True
    ops = [row["op"] for row in read_audit(fake_host.data.audit)]
    assert ops[:2] == ["consent", "loader-app-install"] and "grant-apps-trusted" in ops and ops[-1] == "init"
    rows = read_audit(fake_host.data.audit)
    assert all(row["actor"] == "test" and row["consentRef"] for row in rows[1:]), "every row after the consent carries the consent reference"
    assert (fake_host.data.data_home / "registries.json").is_file()


def test_reinstall_restores_the_grant_the_host_drops_on_uninstall(fake_host):
    """Seen on a live 0.7.0.5: `kirocrew app uninstall` removes the app from agent.apps_trusted, so every
    `--reinstall-loader` (and the self-update Loader install, which never offers the grant) came back
    ungranted and the Loader was left installed but disabled. A grant the user gave earlier is restored
    without asking; a first install still has to be granted."""
    first = fake_host.run("--yes", "init", "--i-accept-the-risk", "--no-trigger")
    assert first.exit == 0, first.stderr + first.stdout
    config = json.loads((fake_host.home_dir / "config.json").read_text(encoding="utf-8"))
    assert config["agent"]["apps_trusted"] == ["floofycrew"]
    # the reinstall path self-update takes: no grant offered
    again = fake_host.run("init", "--reinstall-loader", "--no-grant", "--no-trigger")
    assert again.exit == 0, again.stderr + again.stdout
    calls = fake_host.called()
    assert calls.count("app uninstall floofycrew") == 1
    config = json.loads((fake_host.home_dir / "config.json").read_text(encoding="utf-8"))
    assert config["agent"]["apps_trusted"] == ["floofycrew"] and config["agent"]["apps_trusted_local"] == ["floofycrew"], "the grant the uninstall dropped is back, local marker included (a name-only grant would have been refused at `app install`)"
    assert again.json["loaderApp"]["grantRestored"]["written"] is True
    assert "grant" not in again.json["loaderApp"] and again.json["loaderApp"]["hostVerdict"]["appExecutionAllowed"] is True, "with the grant back the host verdict is allowed and no grant question arises"
    assert "grant: restored" in again.stdout
    installed = json.loads((fake_host.home_dir / "apps" / "floofycrew" / "installed.json").read_text(encoding="utf-8"))
    assert installed["enabled"] is True, "the new Loader runs: enable was not refused"
    rows = [r for r in read_audit(fake_host.data.audit) if r["op"] == "grant-apps-trusted"]
    assert rows[-1]["result"] == "restored" and "kirocrew app uninstall" in rows[-1]["detail"]
    # never granted: a reinstall does not invent a grant (simulate a host whose grant was removed by hand)
    doc = json.loads((fake_host.home_dir / "config.json").read_text(encoding="utf-8"))
    doc["agent"]["apps_trusted"] = []
    (fake_host.home_dir / "config.json").write_text(json.dumps(doc), encoding="utf-8")
    third = fake_host.run("init", "--reinstall-loader", "--no-grant", "--no-trigger")
    assert third.exit == 0 and "grantRestored" not in third.json["loaderApp"]
    assert json.loads((fake_host.home_dir / "config.json").read_text(encoding="utf-8"))["agent"]["apps_trusted"] == []


def test_init_declined_grant_leaves_the_loader_disabled_with_a_warning(fake_host):
    result = fake_host.run("init", "--i-accept-the-risk", "--no-trigger")  # non-interactive, no --yes: the grant question answers no
    assert result.exit == 0
    assert result.json["loaderApp"]["grant"] == "declined"
    assert "refused" in result.stderr and "blocked by execution policy" in result.stderr
    assert not (fake_host.home_dir / "config.json").exists() or "apps_trusted" not in (fake_host.home_dir / "config.json").read_text(encoding="utf-8")
    installed = json.loads((fake_host.home_dir / "apps" / "floofycrew" / "installed.json").read_text(encoding="utf-8"))
    assert installed["enabled"] is False, "the host's verdict stands; FloofyCrew shows it as a warning"


def test_init_trigger_flag_keeps_only_the_named_kinds(fake_host, monkeypatch: pytest.MonkeyPatch):
    """``--trigger KIND`` (the install scripts' menu answer, Requirement 15.2) filters the edition's set; an unknown kind warns."""
    from dataclasses import dataclass

    from floofy_core.cli import cmd_init
    from floofy_core.triggers import TriggerReport

    installed: list[str] = []

    @dataclass
    class Fake:
        kind: str

        def install(self) -> TriggerReport:
            installed.append(self.kind)
            return TriggerReport(self.kind, True, True, f"{self.kind} installed")

    monkeypatch.setattr(cmd_init, "trigger_managers", lambda *a, **k: [Fake("user-timer"), Fake("path-wrapper")])
    result = fake_host.run("init", "--i-accept-the-risk", "--no-loader-app", "--trigger", "path-wrapper")
    assert result.exit == 0, result.stderr
    assert installed == ["path-wrapper"] and result.json["triggers"]["selected"] == ["path-wrapper"]
    assert [t["kind"] for t in result.json["triggers"]["installed"]] == ["path-wrapper"]
    installed.clear()
    both = fake_host.run("init", "--i-accept-the-risk", "--no-loader-app")
    assert both.exit == 0 and installed == ["user-timer", "path-wrapper"] and "selected" not in both.json["triggers"], "without the flag: every trigger the edition offers"
    installed.clear()
    monkeypatch.setattr(cmd_init, "trigger_managers", lambda *a, **k: [Fake("user-timer")])
    missing = fake_host.run("init", "--i-accept-the-risk", "--no-loader-app", "--trigger", "path-wrapper")
    assert missing.exit == 0 and installed == [] and "offers no such trigger" in missing.stderr
    bad = fake_host.run("init", "--i-accept-the-risk", "--no-loader-app", "--trigger", "cron")
    assert bad.exit == 2 and "invalid choice" in bad.stderr


def test_doctor_json_shape_and_problems(fake_host):
    result = fake_host.run("--json", "doctor")
    assert result.exit == 0
    report = result.json
    for key in ("floofycrew", "host", "loader", "consent", "earlyShim", "triggers", "governance", "drift", "compat", "playwright", "mods", "problems"):
        assert key in report, key
    assert report["floofycrew"]["unofficial"] is True and report["floofycrew"]["apiVersion"] == API_VERSION
    assert report["host"]["edition"] == "internal" and report["host"]["version"] == "0.7.0.5"
    assert report["host"]["payloads"] and report["host"]["payloads"][0]["dormant"] is True, "no launcher runs the fake payload"
    assert report["loader"]["installed"] is False and report["consent"]["required"] is True
    assert report["compat"]["row"] is None
    assert report["governance"]["warnings"] == [] and "never" in report["governance"]["note"]
    assert any("consent" in p for p in report["problems"]) and any("Loader app" in p for p in report["problems"])
    assert result.json["command"] == "doctor" and result.json["exit"] == 0
    # text mode prints the same facts
    text = fake_host.run("doctor")
    assert "floofy doctor" in text.stdout and "unofficial" in text.stdout and "consent: required" in text.stdout


def test_doctor_reports_the_served_version_and_dormancy_through_the_socket_fallback(fake_host, tmp_path: Path):
    """Task 10.8: against a real-shaped gateway (bare answer over the socket) doctor still says running, serving X, and grades dormancy per payload."""
    older = tmp_path / "older"
    fake_payload(older, "0.7.0", build="0.7.0.4")
    dist = fake_host.root / "kiro_crew" / "static" / "dist"
    with FakeGateway(dist, "0.7.0.5", socket_dir=fake_host.home_dir) as gw:
        result = fake_host.run("--root", str(older), "--json", "doctor")
        assert result.exit == 0, result.stderr
        gateway = result.json["host"]["gateway"]
        assert gateway == {"running": True, "servedVersion": "0.7.0.5", "endpoint": str(gw.socket_path), "via": "loopback-tcp", "detail": ""}
        assert gw.probes("unix") and all(p["identity"] is False for p in gw.probes("unix")), "the socket never disclosed the version"
        assert gw.probes("tcp") and gw.probes("tcp")[0]["host"] == f"127.0.0.1:{gw.port}" and gw.probes("tcp")[0]["identity"] is True
        dormant = {p["version"]: p["dormant"] for p in result.json["host"]["payloads"]}
        assert dormant == {"0.7.0.5": False, "0.7.0.4": True}, dormant
        drift = {p["host_version"]: p["dormant"] for p in result.json["drift"]["payloads"]}
        assert drift == {"0.7.0.5": False, "0.7.0.4": True} and result.json["drift"]["servedVersion"] == "0.7.0.5"
        text = fake_host.run("--root", str(older), "doctor")
        assert "gateway: running, serving 0.7.0.5 at" in text.stdout and "(version via loopback-tcp)" in text.stdout
    # a --test-mode look-alike (dashboard-0.sock) is running but cannot be asked for its version: no false "not running", dormancy from `current`
    with FakeGateway(dist, "0.7.0.5", socket_path=fake_host.home_dir / "dashboard-0.sock") as gw:
        result = fake_host.run("--json", "doctor")
        gateway = result.json["host"]["gateway"]
        assert gateway["running"] is True and gateway["servedVersion"] is None and gateway["via"] is None and "no loopback port" in gateway["detail"]
        assert result.json["host"]["payloads"][0]["dormant"] is True, "no launcher runs the fake payload, and the gateway did not say otherwise"
        text = fake_host.run("doctor")
        assert "gateway: running at" in text.stdout and "served version unknown" in text.stdout
    offline = fake_host.run("--json", "doctor", "--no-live")
    assert offline.json["host"]["gateway"]["running"] is False and offline.json["host"]["gateway"]["detail"] == "not probed (--no-live)"


def test_doctor_reports_the_early_shim_state_per_payload_from_the_installed_app(fake_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Task 10.9: with the Loader app installed, doctor loads its early_install.py (dataclasses and all) and grades every payload's shim."""
    from floofy_core.loaderapp import EARLY_INSTALL_MODULE_NAME  # noqa: PLC0415
    from test_loaderapp import install_fake_loader_app  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "floofy_loader.early_install", None)  # the fallback import must not answer for the installed copy
    monkeypatch.delitem(sys.modules, EARLY_INSTALL_MODULE_NAME, raising=False)
    directory = install_fake_loader_app(fake_host.home_dir)
    venv_root = tmp_path / "venvpayload"
    fake_payload(venv_root, "0.7.0", layout="venv")
    (venv_root / "bin" / "python").unlink()
    (venv_root / "bin" / "python").symlink_to(sys.executable)  # a real interpreter: site_dirs() asks it (files only, nothing installed)
    result = fake_host.run("--root", str(venv_root), "--json", "doctor")
    assert result.exit == 0, result.stderr
    shim = result.json["earlyShim"]
    assert shim["available"] is True, shim
    assert result.json["loader"]["installed"] is True and result.json["loader"]["enabled"] is True
    by_root = {Path(p["root"]).name: p["id"] for p in result.json["host"]["payloads"]}
    entries = {e["payload"]: e for e in shim["payloads"]}
    flat = entries[by_root["payload"]]
    assert flat["status"] is None and flat["note"] == "no interpreter", "a flat payload carries no interpreter to install into"
    venv = entries[by_root["venvpayload"]]
    assert venv["kind"] == "venv-site" and venv["status"]["installed"] is False and venv["status"]["pth_present"] is False
    assert venv["status"]["interpreter"] == str(venv_root / "bin" / "python") and venv["status"]["site_dir"], venv
    assert not any("Loader app not installed" in p for p in result.json["problems"])
    text = fake_host.run("--root", str(venv_root), "doctor")
    assert "early shim [venv-site]" in text.stdout and "not installed" in text.stdout and "early shim: Loader app not installed" not in text.stdout
    # without the app the report says so — the message the live install wrongly showed before the fix
    shutil.rmtree(directory)
    gone = fake_host.run("--json", "doctor")
    assert gone.json["earlyShim"] == {"available": False, "detail": "Loader app not installed (the shim ships inside it)", "payloads": []}


def test_doctor_reports_governance_warnings_and_crossing_mods(fake_host):
    (fake_host.home_dir / "security_policy.json").write_text(json.dumps({"capabilities": {"theme_install": {"enabled": False}}}), encoding="utf-8")
    mods = fake_host.data.ensure().mods
    (mods / "themey").mkdir()
    (mods / "themey" / "floofy.json").write_text(json.dumps({"schema": 1, "id": "themey", "name": "T", "version": "1.0.0", "description": "d", "authors": ["a"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], "files": []}), encoding="utf-8")
    result = fake_host.run("--json", "doctor")
    codes = {w["code"] for w in result.json["governance"]["warnings"]}
    assert "ThemeInstallClosed" in codes
    assert result.json["governance"]["crossing"]["ThemeInstallClosed"] == ["themey"]
    assert result.json["governance"]["snapshot"]["capabilities"]["theme_install"] is False
    status = fake_host.run("--json", "status")
    row = status.json["mods"][0]
    assert row["id"] == "themey" and row["hostCompat"] == "in-range" and row["parts"][0]["seam"].startswith("themes directory")


def test_status_reads_loader_state_file(fake_host):
    fake_host.data.ensure()
    state = {"loader": "ok", "bootedAt": "2026-09-19T00:00:00Z", "mods": {"alpha": {"active": False, "reason": "UserDisabled", "detail": "disabled by the user", "parts": [{"index": 0, "kind": "spa", "side": "spa", "path": "spa/main.js", "status": "inactive", "seam": "SPA host (Loader app ui route)", "modifiesPayload": False}], "warnings": [{"code": "HostVersionOutOfRange", "message": "x"}], "governance": []}}}
    fake_host.data.loader_state.write_text(json.dumps(state), encoding="utf-8")
    (fake_host.data.mods / "alpha").mkdir()
    (fake_host.data.mods / "alpha" / "floofy.json").write_text(json.dumps({"schema": 1, "id": "alpha", "name": "A", "version": "2.0.0", "description": "d", "authors": ["a"], "license": "MIT", "kirocrew": {"version": ">=0.9.0", "strict": True}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "spa", "side": "spa", "path": "spa/main.js"}], "files": []}), encoding="utf-8")
    fake_host.data.enabled.write_text(json.dumps({"alpha": False}), encoding="utf-8")
    result = fake_host.run("--json", "status")
    assert result.json["loader"]["source"] == "file"
    row = result.json["mods"][0]
    assert row["reason"] == "UserDisabled" and row["enabled"] is False and row["hostCompat"] == "out-of-range(strict)"
    assert row["warnings"][0]["code"] == "HostVersionOutOfRange" and row["stateSource"] == "published"
    text = fake_host.run("status")
    assert "reason=UserDisabled" in text.stdout and "out-of-range(strict)" in text.stdout


def test_vanilla_writes_the_marker_and_the_loader_consumes_it_once(fake_host, monkeypatch: pytest.MonkeyPatch):
    result = fake_host.run("--vanilla")
    assert result.exit == 0 and fake_host.data.vanilla_marker.is_file()
    assert "restart" not in result.stdout.lower() or "never restarts" in result.stdout
    assert read_audit(fake_host.data.audit)[-1]["op"] == "vanilla"
    # the Loader honours the marker for exactly one boot
    sys.path.insert(0, str(REPO_ROOT / "loader-app"))
    import logging
    import types

    from floofy_core.governance import GovernanceSnapshot
    from floofy_loader.activation import ModContext
    from floofy_loader.boot import BootDeps, boot
    from floofy_loader.consent import write_consent
    from floofy_loader.host import read_host_facts

    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0.5"
    module.__file__ = str(fake_host.root / "kiro_crew" / "__init__.py")
    facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(fake_host.home_dir)}, adapters=[])
    paths = fake_host.data.ensure()
    write_consent(paths.consent, by="tests")
    (paths.mods / "alpha").mkdir(exist_ok=True)
    (paths.mods / "alpha" / "floofy.json").write_text(json.dumps({"schema": 1, "id": "alpha", "name": "A", "version": "1.0.0", "description": "d", "authors": ["a"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], "files": [{"path": "theme/theme.json", "sha256": "8ab0d8d5ee2aa5bfb4d4e3d1ddc2f5cd5ef7fbc9fa1f3cbaf4f9d35c0ed5b4ba"}]}), encoding="utf-8")
    (paths.mods / "alpha" / "theme").mkdir(exist_ok=True)
    (paths.mods / "alpha" / "theme" / "theme.json").write_text("{}", encoding="utf-8")
    import hashlib

    manifest = json.loads((paths.mods / "alpha" / "floofy.json").read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = hashlib.sha256(b"{}").hexdigest()
    (paths.mods / "alpha" / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    paths.enabled.write_text(json.dumps({"alpha": True}), encoding="utf-8")
    runs: list[list[str]] = []

    def make_context(mod_id, version, mod_dir, manifest):
        return ModContext(mod_id, version, mod_dir, facts, logging.getLogger("t"), data_dir=paths.mod_dir(mod_id))

    deps = BootDeps(paths=paths, facts=facts, make_context=make_context, governance_reader=lambda _f: GovernanceSnapshot(), patch_runner=lambda planned, f, p: runs.append([pp.label for pp in planned]) or {"ran": True})
    first = boot(deps).state
    assert first.vanilla is True and first.mods["alpha"].active is False and first.mods["alpha"].reason == "UserDisabled" and "vanilla" in first.mods["alpha"].detail
    assert runs == [[]], "the Patcher ran with an empty set (revert) for the vanilla boot"
    assert first.patches.get("vanilla") is True and not paths.vanilla_marker.exists(), "the marker is consumed"
    second = boot(deps).state
    assert second.vanilla is False and second.mods["alpha"].active is True
    cancelled = fake_host.run("vanilla", "--cancel")
    assert cancelled.exit == 0 and cancelled.json["cancelled"] is False


def test_deinit_removes_the_loader_app_and_keeps_consent(fake_host):
    fake_host.run("--yes", "init", "--i-accept-the-risk", "--no-trigger")
    assert (fake_host.home_dir / "apps" / "floofycrew").is_dir()
    declined = fake_host.run("deinit")  # non-interactive without --yes: cancelled
    assert declined.exit == 1 and (fake_host.home_dir / "apps" / "floofycrew").is_dir()
    result = fake_host.run("--yes", "deinit")
    assert result.exit == 0, result.stderr
    assert not (fake_host.home_dir / "apps" / "floofycrew").exists()
    assert fake_host.data.consent.is_file() and fake_host.data.audit.is_file()
    assert "app uninstall floofycrew" in fake_host.called()
    purged = fake_host.run("--yes", "deinit", "--purge")
    assert purged.exit == 0 and not fake_host.data.data_home.exists()


def test_main_entry_and_zipapp_run_on_this_interpreter(tmp_path: Path):
    assert main(["--version"]) == 0
    out = tmp_path / "floofy.pyz"
    build = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_zipapp.py"), "--out", str(out)], capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    env = dict(os.environ, FLOOFY_NO_ADAPTERS="1")
    version = subprocess.run([sys.executable, str(out), "--version"], capture_output=True, text=True, check=False, env=env)
    assert version.returncode == 0 and FRAMEWORK_VERSION in version.stdout and "unofficial" in version.stdout
    doctor = subprocess.run([sys.executable, str(out), "--home", str(tmp_path / "h"), "--json", "doctor"], capture_output=True, text=True, check=False, env=env)
    assert doctor.returncode == 0, doctor.stderr
    document = json.loads(doctor.stdout)
    assert document["floofycrew"]["zipapp"] == str(out)
    check = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_zipapp.py"), "--out", str(out), "--check"], capture_output=True, text=True, check=False)
    assert check.returncode == 0, check.stderr
