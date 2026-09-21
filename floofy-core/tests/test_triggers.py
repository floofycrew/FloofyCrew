"""Re-apply triggers (task 6.4; Requirement 6.1): generated units, managers with a fake runner, the PATH wrapper.

Nothing here enables a real unit or touches ``~/.config/systemd/user``,
``~/Library/LaunchAgents`` or ``~/.local/bin``: every manager writes into a
temp directory with ``activate=False`` or a recording runner, and
``systemd-analyze verify`` only parses the generated files.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli.main import run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.triggers import (
    LAUNCHD_LABEL,
    REAPPLY_MINUTE,
    SYSTEMD_SERVICE,
    SYSTEMD_TIMER,
    PathWrapperTrigger,
    TriggerManager,
    UserTimerTrigger,
    launchd_plist,
    loader_startup_status,
    path_wrapper_script,
    self_command,
    systemd_units,
    trigger_managers,
)

from floofy_testing import fake_payload

COMMAND = ["/usr/bin/python3.12", "/opt/floofy.pyz", "--home", "/home/u/.kiro/crew"]


def test_systemd_units_fire_hourly_at_55_and_verify():
    units = systemd_units(COMMAND, environment={"FLOOFY_ACTOR": "trigger"})
    assert set(units) == {SYSTEMD_SERVICE, SYSTEMD_TIMER}
    assert f"OnCalendar=*-*-* *:{REAPPLY_MINUTE:02d}:00" in units[SYSTEMD_TIMER] and "Persistent=true" in units[SYSTEMD_TIMER]
    assert "ExecStart=/usr/bin/python3.12 /opt/floofy.pyz --home /home/u/.kiro/crew apply --if-changed --quiet" in units[SYSTEMD_SERVICE]
    assert "Environment=FLOOFY_ACTOR=trigger" in units[SYSTEMD_SERVICE] and "Type=oneshot" in units[SYSTEMD_SERVICE]
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not available")
    scratch = Path(__import__("tempfile").mkdtemp(prefix="floofy-units-"))
    try:
        # verify checks that ExecStart's program exists: generate with this interpreter for the check
        for name, text in systemd_units([sys.executable, "/opt/floofy.pyz", "--home", "/h"]).items():
            (scratch / name).write_text(text, encoding="utf-8")
        verify = subprocess.run(["systemd-analyze", "--user", "verify", str(scratch / SYSTEMD_TIMER), str(scratch / SYSTEMD_SERVICE)], capture_output=True, text=True, check=False)
        assert verify.returncode == 0, verify.stderr
        calendar = subprocess.run(["systemd-analyze", "calendar", f"*-*-* *:{REAPPLY_MINUTE:02d}:00"], capture_output=True, text=True, check=False)
        assert calendar.returncode == 0 and "Next elapse" in calendar.stdout
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_launchd_plist_is_a_valid_calendar_agent(tmp_path: Path):
    document = plistlib.loads(launchd_plist(COMMAND, log_dir=tmp_path, environment={"FLOOFY_ACTOR": "trigger"}))
    assert document["Label"] == LAUNCHD_LABEL == "dev.floofycrew.reapply"
    assert document["StartCalendarInterval"] == {"Minute": REAPPLY_MINUTE} and document["RunAtLoad"] is False
    assert document["ProgramArguments"] == [*COMMAND, "apply", "--if-changed", "--quiet"]
    assert document["EnvironmentVariables"] == {"FLOOFY_ACTOR": "trigger"} and document["StandardOutPath"].endswith(f"{LAUNCHD_LABEL}.log")


def test_user_timer_manager_on_linux_writes_units_and_records_systemctl_calls(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "enabled\n", "")

    manager = UserTimerTrigger(COMMAND, platform="linux", unit_dir=tmp_path / "units", runner=runner)
    assert isinstance(manager, TriggerManager)
    before = manager.status()
    assert before.installed is False and "floofy init" in before.detail
    report = manager.install()
    assert report.ok and report.changed and sorted(Path(p).name for p in report.paths) == sorted([SYSTEMD_SERVICE, SYSTEMD_TIMER])
    assert calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", SYSTEMD_TIMER]]
    assert (tmp_path / "units" / SYSTEMD_TIMER).read_text(encoding="utf-8") == systemd_units(COMMAND)[SYSTEMD_TIMER]
    again = manager.install()
    assert again.ok and not again.changed, "idempotent"
    status = manager.status()
    assert status.installed is True and status.active is True and status.kind == "user-timer"
    removed = manager.uninstall()
    assert removed.ok and removed.changed and not (tmp_path / "units" / SYSTEMD_TIMER).exists()
    assert ["systemctl", "--user", "disable", "--now", SYSTEMD_TIMER] in calls
    assert manager.status().installed is False


def test_user_timer_manager_on_darwin_writes_the_plist_and_bootstraps(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    manager = UserTimerTrigger(COMMAND, platform="darwin", unit_dir=tmp_path / "LaunchAgents", runner=runner)
    report = manager.install()
    assert report.ok and report.changed and report.paths == [str(tmp_path / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist")]
    uid = os.getuid()
    assert calls[-1] == ["launchctl", "bootstrap", f"gui/{uid}", report.paths[0]]
    plistlib.loads((tmp_path / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist").read_bytes())
    assert manager.status().installed is True
    assert manager.uninstall().changed and not (tmp_path / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist").exists()


def test_unsupported_platform_reports_instead_of_failing(tmp_path: Path):
    manager = UserTimerTrigger(COMMAND, platform="win32", unit_dir=tmp_path)
    report = manager.install()
    assert not report.ok and "no user timer implementation" in report.detail
    assert manager.status().installed is False


def test_path_wrapper_reapplies_then_execs_the_real_launcher(tmp_path: Path):
    real = tmp_path / "venv" / "bin" / "kirocrew"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\necho REAL \"$@\"\n", encoding="utf-8")
    real.chmod(0o755)
    fake_floofy = tmp_path / "floofy-fake"
    log = tmp_path / "floofy.log"
    fake_floofy.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n", encoding="utf-8")
    fake_floofy.chmod(0o755)
    trigger = PathWrapperTrigger([str(fake_floofy), "--home", "/x"], real, bin_dir=tmp_path / "bin")
    report = trigger.install()
    assert report.ok and report.changed and report.paths == [str(tmp_path / "bin" / "kirocrew")]
    wrapper = tmp_path / "bin" / "kirocrew"
    assert wrapper.stat().st_mode & stat.S_IXUSR
    out = subprocess.run([str(wrapper), "gateway", "--test-mode"], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and out.stdout.strip() == "REAL gateway --test-mode"
    assert log.read_text(encoding="utf-8").strip() == "--home /x apply --if-changed --quiet"
    status = trigger.status()
    assert status.installed is True and status.kind == "path-wrapper"
    assert not trigger.install().changed
    # a non-venv payload: not applicable, nothing written
    none = PathWrapperTrigger([str(fake_floofy)], None, bin_dir=tmp_path / "bin2")
    assert none.install().changed is False and "only applies to venv installs" in none.install().detail
    # a foreign kirocrew at the wrapper's path is never removed
    foreign = tmp_path / "bin3" / "kirocrew"
    foreign.parent.mkdir()
    foreign.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    keep = PathWrapperTrigger([str(fake_floofy)], real, bin_dir=tmp_path / "bin3")
    assert keep.uninstall().changed is False and foreign.read_text(encoding="utf-8").startswith("#!/bin/sh\necho foreign")
    assert trigger.uninstall().changed and not wrapper.exists()


def test_self_command_and_loader_startup_status(tmp_path: Path):
    command = self_command(tmp_path)
    assert command[-2:] == ["--home", str(tmp_path)] and (command[0].endswith("floofy") or command[:1] == [sys.executable])
    status = loader_startup_status(tmp_path)
    assert status.installed is False and status.kind == "loader-startup"
    app_dir = tmp_path / "apps" / "floofycrew"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text("{}", encoding="utf-8")
    (app_dir / "installed.json").write_text('{"enabled": false}', encoding="utf-8")
    status = loader_startup_status(tmp_path)
    assert status.installed is True and status.active is False and any("disabled" in n for n in status.notes)


def test_init_and_doctor_drive_the_trigger_managers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """init installs, doctor reports, deinit removes — through the adapters, into a scratch unit dir, never enabling anything."""
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    home = tmp_path / "home"
    fake_payload(tmp_path / "payload", "0.7.0", build="0.7.0.5")
    import floofy_core.triggers as triggers_module

    def fake_trigger_managers(host_home, adapters, *, command=None, edition=None, **kwargs):
        return [UserTimerTrigger(command or self_command(host_home), platform="linux", unit_dir=tmp_path / "units", activate=False, environment={"FLOOFY_ACTOR": "trigger"})]

    import floofy_core.cli.cmd_doctor as doctor_module
    import floofy_core.cli.cmd_init as init_module
    import floofy_core.cli.actions as actions_module

    for module in (init_module, doctor_module, actions_module):
        monkeypatch.setattr(module, "trigger_managers", fake_trigger_managers)
    argv = ["--home", str(home), "--root", str(tmp_path / "payload")]
    result = run([*argv, "init", "--i-accept-the-risk", "--no-loader-app"], non_interactive=True, actor="test")
    assert result.exit == 0, result.stderr
    assert (tmp_path / "units" / SYSTEMD_TIMER).is_file() and result.json["triggers"]["installed"][0]["ok"] is True
    assert read_audit(DataHome.for_host_home(home).audit)[-2]["op"] == "trigger-install"
    doctor = run([*argv, "--json", "doctor"], non_interactive=True)
    assert doctor.json["triggers"]["managers"][0]["installed"] is True
    assert not any("no re-apply trigger" in p for p in doctor.json["problems"])
    gone = run([*argv, "--yes", "deinit", "--keep-loader-app"], non_interactive=True)
    assert gone.exit == 0, gone.stderr
    assert not (tmp_path / "units" / SYSTEMD_TIMER).exists()
    # the triggers' entry point: apply --if-changed marks its audit rows as the trigger when FLOOFY_ACTOR says so
    write_consent(DataHome.for_host_home(home).consent, by="tests", how="test")
    monkeypatch.setenv("FLOOFY_ACTOR", "trigger")
    applied = run([*argv, "apply", "--if-changed", "--no-verify"], non_interactive=True, actor="cli")
    assert applied.exit == 0, applied.stderr
    rows = read_audit(DataHome.for_host_home(home).audit)
    assert rows[-1]["op"] == "apply-if-changed" and rows[-1]["actor"] == "trigger"
