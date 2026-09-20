"""Kind handlers (task 6.3; Requirement 2.1, 2.2, 2.3, 2.8) against scratch homes and a fake host CLI.

The theme route vs direct-write byte-equivalence needs a real gateway and lives in
``test_kinds_gateway.py`` (skipped without the payload copy).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from floofy_core.audit import AuditLog, read_audit
from floofy_core.cli.main import run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.governance import GovernanceSnapshot
from floofy_core.kinds import KindContext, SEAMS, handlers
from floofy_core.kinds.app import AppHandler
from floofy_core.kinds.config import ConfigHandler
from floofy_core.kinds.dropins import AgentHandler, AppearanceHandler, SkillHandler
from floofy_core.kinds.theme import ThemeHandler
from floofy_core.themes import validate_theme_dir

from floofy_testing import EXAMPLES_DIR, REPO_ROOT, fake_payload
from test_cli_init_doctor import FAKE_KIROCREW

RIMURU_THEME = REPO_ROOT / "mods" / "rimuru-branding" / "theme"


@pytest.fixture
def kctx(tmp_path: Path):
    host_home = tmp_path / "home"
    kiro_home = tmp_path / "kiro"
    data = DataHome.for_host_home(host_home).ensure()
    said: list[str] = []
    audit = AuditLog(data.data_home, actor="test")
    return KindContext(host_home=host_home, kiro_home=kiro_home, data_home=data.data_home, governance=GovernanceSnapshot(), confirm=lambda _p: True, say=said.append, warn=said.append, audit=audit.record)


def copy_example(kind: str, tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "mods" / kind
    shutil.copytree(EXAMPLES_DIR / kind, root)
    return root, json.loads((root / "floofy.json").read_text(encoding="utf-8"))


def test_every_kind_has_a_handler_with_the_documented_seam():
    table = handlers()
    assert set(table) == set(SEAMS)
    for kind, handler in table.items():
        assert handler.kind == kind and handler.seam == SEAMS[kind]
        assert handler.modifies_payload is (kind == "patch"), "patch is the only kind touching payload files (Requirement 2.6)"


def test_theme_direct_write_is_the_host_route_without_the_route(kctx, tmp_path: Path):
    root = tmp_path / "rimuru"
    root.mkdir()
    shutil.copytree(RIMURU_THEME, root / "theme")
    part = {"kind": "theme", "side": "gateway", "path": "theme/theme.json"}
    outcome = ThemeHandler().install(kctx, "rimuru-branding", root, part, 0)
    assert outcome.ok and outcome.status == "installed" and outcome.extra["via"] == "direct-write", outcome.detail
    installed = kctx.host_home / "themes" / "rimuru"
    assert installed.is_dir() and (installed / "theme.json").read_bytes() == (RIMURU_THEME / "theme.json").read_bytes()
    summary = validate_theme_dir(installed)
    assert summary.slug == "rimuru" and summary.level == 1 and sorted(summary.files) == sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*") if p.is_file())
    assert outcome.governance == [], "no policy closes the route: no warning"
    status = ThemeHandler().status(kctx, "rimuru-branding", root, part, 0)
    assert status.status == "present" and status.extra["slug"] == "rimuru"
    removed = ThemeHandler().uninstall(kctx, "rimuru-branding", root, part, 0)
    assert removed.status == "removed" and not installed.exists()
    assert ThemeHandler().status(kctx, "rimuru-branding", root, part, 0).status == "absent"
    # a pack the host would refuse is refused here too (level 0 pack with a branding/ dir)
    bad = tmp_path / "bad"
    shutil.copytree(root, bad)
    manifest = json.loads((bad / "theme" / "theme.json").read_text(encoding="utf-8"))
    manifest["level"] = 0
    (bad / "theme" / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    refused = ThemeHandler().install(kctx, "bad", bad, part, 0)
    assert not refused.ok and "requires level 1" in refused.detail


def test_theme_closed_by_governance_is_a_warning_and_still_installs(kctx, tmp_path: Path):
    kctx.governance = GovernanceSnapshot(capabilities={"theme_install": False})
    root = tmp_path / "rimuru"
    root.mkdir()
    shutil.copytree(RIMURU_THEME, root / "theme")
    outcome = ThemeHandler().install(kctx, "rimuru-branding", root, {"kind": "theme", "side": "gateway", "path": "theme/theme.json"}, 0)
    assert outcome.ok and outcome.status == "installed"
    assert [g["code"] for g in outcome.governance] == ["ThemeInstallClosed"]
    assert (kctx.host_home / "themes" / "rimuru" / "variables.json").is_file()
    rows = read_audit(kctx.data_home / "audit.jsonl")
    assert rows[-1]["op"] == "theme-install" and rows[-1]["governanceFlags"] == ["ThemeInstallClosed"]


def test_agent_skill_appearance_dropins_with_ownership(kctx, tmp_path: Path):
    agent_root, agent_manifest = copy_example("agent", tmp_path)
    skill_root, skill_manifest = copy_example("skill", tmp_path)
    look_root, look_manifest = copy_example("appearance", tmp_path)
    agent = AgentHandler().install(kctx, "example-agent", agent_root, agent_manifest["parts"][0], 0)
    skill = SkillHandler().install(kctx, "example-skill", skill_root, skill_manifest["parts"][0], 0)
    look = AppearanceHandler().install(kctx, "example-appearance", look_root, look_manifest["parts"][0], 0)
    assert agent.ok and (kctx.kiro_home / "agents" / "example-agent.json").is_file(), agent.detail
    assert skill.ok and (kctx.host_home / "skills" / "example-skill" / "SKILL.md").is_file(), skill.detail
    pack_id = json.loads((look_root / "appearance" / "manifest.json").read_text(encoding="utf-8")).get("id", "appearance")
    assert look.ok and (kctx.host_home / "appearance-library" / "appearances" / pack_id / "manifest.json").is_file(), look.detail
    assert (kctx.kiro_home / "agents" / "example-agent.json.floofy-owner").is_file()
    assert AgentHandler().status(kctx, "example-agent", agent_root, agent_manifest["parts"][0], 0).status == "present"
    # a foreign skill directory is neither overwritten nor removed
    foreign = kctx.host_home / "skills" / "example-skill"
    (foreign.with_name("example-skill.floofy-owner")).write_text(json.dumps({"mod": "someone-else"}), encoding="utf-8")
    again = SkillHandler().install(kctx, "example-skill", skill_root, skill_manifest["parts"][0], 0)
    assert not again.ok and "not overwriting" in again.detail
    kept = SkillHandler().uninstall(kctx, "example-skill", skill_root, skill_manifest["parts"][0], 0)
    assert kept.status == "skipped" and foreign.is_dir()
    (foreign.with_name("example-skill.floofy-owner")).write_text(json.dumps({"mod": "example-skill"}), encoding="utf-8")
    assert SkillHandler().uninstall(kctx, "example-skill", skill_root, skill_manifest["parts"][0], 0).status == "removed" and not foreign.exists()
    assert AgentHandler().uninstall(kctx, "example-agent", agent_root, agent_manifest["parts"][0], 0).status == "removed"
    assert not (kctx.kiro_home / "agents" / "example-agent.json").exists() and not (kctx.kiro_home / "agents" / "example-agent.json.floofy-owner").exists()
    assert AppearanceHandler().uninstall(kctx, "example-appearance", look_root, look_manifest["parts"][0], 0).status == "removed"


def test_config_sets_records_previous_and_restores_including_absent_keys(kctx, tmp_path: Path):
    config_path = kctx.host_home / "config.json"
    config_path.write_text(json.dumps({"dashboard": {"theme_color": "default", "compact": False}, "agent": {"model": "x"}}, indent=2) + "\n", encoding="utf-8")
    config_path.chmod(0o600)
    root, manifest = copy_example("config", tmp_path)
    part = manifest["parts"][0]  # values.json sets dashboard.theme_color, inline values set dashboard.compact
    outcome = ConfigHandler().install(kctx, "example-config", root, part, 0)
    assert outcome.ok and outcome.extra["via"] == "direct-edit", outcome.detail
    document = json.loads(config_path.read_text(encoding="utf-8"))
    assert document["dashboard"]["theme_color"] == "custom-example-theme" and document["dashboard"]["compact"] is True and document["agent"]["model"] == "x"
    assert (config_path.stat().st_mode & 0o777) == 0o600, "the mode is preserved like update_config_locked does"
    previous = json.loads((root / ".floofy" / "config-previous.json").read_text(encoding="utf-8"))
    assert previous == {"dashboard.theme_color": "default", "dashboard.compact": False}
    assert ConfigHandler().status(kctx, "example-config", root, part, 0).status == "present"
    # a key that was absent before is deleted at uninstall
    part2 = {"kind": "config", "side": "gateway", "values": {"floofy.test_key": 1}}
    added = ConfigHandler().install(kctx, "example-config", root, part2, 1)
    assert added.extra["previous"] == {"floofy.test_key": "<absent>"}
    assert json.loads(config_path.read_text(encoding="utf-8"))["floofy"]["test_key"] == 1
    assert ConfigHandler().uninstall(kctx, "example-config", root, part2, 1).status == "removed"
    assert "floofy" not in json.loads(config_path.read_text(encoding="utf-8"))
    restored = ConfigHandler().uninstall(kctx, "example-config", root, part, 0)
    assert restored.status == "removed"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    assert document["dashboard"] == {"theme_color": "default", "compact": False}
    ops = [r["op"] for r in read_audit(kctx.data_home / "audit.jsonl")]
    assert ops.count("config-set") == 2 and ops.count("config-restore") == 2


def test_config_prefers_the_host_cli_when_a_launcher_exists(kctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text(FAKE_KIROCREW, encoding="utf-8")
    launcher.chmod(0o755)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_KIROCREW_LOG", str(log))
    kctx.launcher = launcher
    root, manifest = copy_example("config", tmp_path)
    outcome = ConfigHandler().install(kctx, "example-config", root, manifest["parts"][0], 0)
    assert outcome.ok and outcome.extra["via"] == "kirocrew config set", outcome.detail
    calls = log.read_text(encoding="utf-8").splitlines()
    assert 'config set dashboard.theme_color "custom-example-theme"' in calls and "config set dashboard.compact true" in calls


def test_app_installs_through_the_host_cli_offers_the_grant_and_enables(kctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text(FAKE_KIROCREW.replace("'floofycrew' in c.get", "'example-app' in c.get").replace('"floofycrew", "version"', '"example-app", "version"').replace("/apps/floofycrew/installed", "/apps/example-app/installed").replace("enabled floofycrew", "enabled example-app"), encoding="utf-8")
    launcher.chmod(0o755)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_KIROCREW_LOG", str(log))
    kctx.launcher = launcher
    root, manifest = copy_example("app", tmp_path)
    asked: list[str] = []

    def confirm(prompt: str) -> bool:
        asked.append(prompt)
        return True

    kctx.confirm = confirm
    outcome = AppHandler().install(kctx, "example-app", root, manifest["parts"][0], 0)
    assert outcome.ok and outcome.status == "installed", outcome.detail
    assert outcome.extra["appName"] == "example-app" and outcome.extra["grant"] == "written" and outcome.extra["enabled"] is True
    assert asked and "agent.apps_trusted" in asked[0]
    config = json.loads((kctx.host_home / "config.json").read_text(encoding="utf-8"))
    assert config["agent"]["apps_trusted"] == ["example-app"]
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0].startswith("app install ") and calls[-1] == "app enable example-app"
    assert AppHandler().status(kctx, "example-app", root, manifest["parts"][0], 0).status == "present"
    # declined grant: installed, host refuses enable, shown as a warning
    kctx.confirm = lambda _p: False
    (kctx.host_home / "config.json").write_text("{}", encoding="utf-8")
    again = AppHandler().install(kctx, "example-app", root, manifest["parts"][0], 0)
    assert again.ok and again.extra["grant"] == "declined" and again.extra["enabled"] is False
    assert any(g["code"] == "ThirdPartyAppsDisabled" for g in again.governance)
    assert AppHandler().uninstall(kctx, "example-app", root, manifest["parts"][0], 0).status == "removed"
    assert "app uninstall example-app" in log.read_text(encoding="utf-8")


def test_install_command_runs_the_seam_handlers_and_status_shows_presence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    payload = tmp_path / "payload"
    fake_payload(payload, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    argv = ["--home", str(home), "--root", str(payload)]
    result = run([*argv, "install", str(EXAMPLES_DIR / "skill")], non_interactive=True)
    assert result.exit == 0, result.stderr
    assert result.json["parts"][0]["status"] == "installed" and (home / "skills" / "example-skill" / "SKILL.md").is_file()
    status = run([*argv, "--json", "status"], non_interactive=True)
    part = status.json["mods"][0]["parts"][0]
    assert part["seamStatus"] == "present" and part["seam"].startswith("skills directory") and part["modifiesPayload"] is False
    removed = run([*argv, "--yes", "uninstall", "example-skill"], non_interactive=True)
    assert removed.exit == 0 and not (home / "skills" / "example-skill").exists()
    (home / "security_policy.json").write_text(json.dumps({"capabilities": {"theme_install": {"enabled": False}}}), encoding="utf-8")
    themed = run([*argv, "install", str(EXAMPLES_DIR / "theme")], non_interactive=True)
    assert themed.exit == 0, themed.stderr
    assert themed.json["parts"][0]["status"] == "installed" and [g["code"] for g in themed.json["parts"][0]["governance"]] == ["ThemeInstallClosed"]
    assert "ThemeInstallClosed" in themed.stderr
    shown = run([*argv, "--json", "status"], non_interactive=True)
    assert any(g["code"] == "ThemeInstallClosed" for g in shown.json["mods"][0]["governance"])
