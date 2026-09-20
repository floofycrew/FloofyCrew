"""Re-apply triggers (Requirement 6.1): the protocol, the shared unit generators, the edition-neutral managers.

A trigger makes ``floofy apply --if-changed`` run after the host lands a new
payload, so an update never silently strips the mods (Requirement 6.1, design
DR-3 "Re-apply trigger"):

* **hourly user timer** on both editions — a systemd user timer at minute 55 on
  Linux (the internal distribution's hourly auto-update runs at :50, spike 1.6),
  a launchd agent with ``StartCalendarInterval`` minute 55 on macOS
  (:class:`UserTimerTrigger`);
* the **Loader's ``on_startup``** re-apply — always present once the Loader app
  is installed (``floofy_loader.boot`` hands the enabled patch set to the
  Patcher on every gateway start), reported here, not installed here;
* the **``kirocrew`` PATH wrapper** for venv installs on the public edition
  (:class:`PathWrapperTrigger`: ``~/.local/bin/kirocrew`` runs
  ``floofy apply --if-changed`` then ``exec``\\ s the real launcher).

Edition adapters expose ``reapply_triggers(host_home, floofy_command, **hooks) ->
list[TriggerManager]`` composing these classes with their own paths;
:func:`trigger_managers` collects them. Everything is unit-file generation plus a few ``systemctl --user`` /
``launchctl`` calls behind an injectable runner, so tests exercise the generated
files and a dry ``systemd-analyze verify`` without enabling anything on the
machine that runs them.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

__all__ = [
    "LAUNCHD_LABEL",
    "PathWrapperTrigger",
    "SYSTEMD_SERVICE",
    "SYSTEMD_TIMER",
    "TriggerManager",
    "TriggerReport",
    "TriggerStatus",
    "UserTimerTrigger",
    "launchd_plist",
    "path_wrapper_script",
    "self_command",
    "systemd_units",
    "trigger_managers",
]

SYSTEMD_SERVICE = "floofy-reapply.service"
SYSTEMD_TIMER = "floofy-reapply.timer"
LAUNCHD_LABEL = "dev.floofycrew.reapply"
#: Minute of the hour the timer fires: after the host's own hourly update window.
REAPPLY_MINUTE = 55

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)


# --- what the trigger runs ---------------------------------------------------------------------


def self_command(host_home: Path | None = None) -> list[str]:
    """The argv that runs this very ``floofy`` (zipapp, console script or ``python -m``), pinned to ``host_home``."""
    main_file = getattr(sys.modules.get("__main__"), "__file__", None) or ""
    for candidate in [Path(main_file), *Path(main_file).parents] if main_file else []:
        if candidate.suffix == ".pyz" and candidate.is_file():
            argv = [sys.executable, str(candidate.resolve())]
            break
    else:
        console_script = shutil.which("floofy")
        argv = [console_script] if console_script else [sys.executable, "-m", "floofy_core.cli"]
    if host_home is not None:
        argv += ["--home", str(host_home)]
    return argv


def systemd_units(command: list[str], *, minute: int = REAPPLY_MINUTE, environment: dict[str, str] | None = None) -> dict[str, str]:
    """The ``.service`` and ``.timer`` texts for the hourly re-apply."""
    exec_start = " ".join(shlex.quote(part) for part in [*command, "apply", "--if-changed", "--quiet"])
    env_lines = "".join(f"Environment={shlex.quote(f'{k}={v}')}\n" for k, v in sorted((environment or {}).items()))
    service = (
        "[Unit]\n"
        "Description=FloofyCrew (unofficial): re-apply KiroCrew mods after a host update\n"
        "Documentation=https://github.com/floofycrew\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"{env_lines}"
        f"ExecStart={exec_start}\n"
        "Nice=10\n"
        "TimeoutStartSec=15min\n"
    )
    timer = (
        "[Unit]\n"
        "Description=FloofyCrew (unofficial): hourly re-apply timer\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* *:{minute:02d}:00\n"
        "Persistent=true\n"
        "RandomizedDelaySec=30\n"
        f"Unit={SYSTEMD_SERVICE}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {SYSTEMD_SERVICE: service, SYSTEMD_TIMER: timer}


def launchd_plist(command: list[str], *, minute: int = REAPPLY_MINUTE, label: str = LAUNCHD_LABEL, log_dir: Path | None = None, environment: dict[str, str] | None = None) -> bytes:
    """The launchd agent plist: ``StartCalendarInterval`` every hour at ``minute``."""
    document: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": [*command, "apply", "--if-changed", "--quiet"],
        "StartCalendarInterval": {"Minute": minute},
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    if environment:
        document["EnvironmentVariables"] = dict(environment)
    if log_dir is not None:
        document["StandardOutPath"] = str(Path(log_dir) / f"{label}.log")
        document["StandardErrorPath"] = str(Path(log_dir) / f"{label}.err")
    return plistlib.dumps(document, sort_keys=True)


def path_wrapper_script(real_launcher: Path, command: list[str]) -> str:
    """``~/.local/bin/kirocrew``: re-apply if the payload changed, then ``exec`` the real launcher."""
    reapply = " ".join(shlex.quote(part) for part in [*command, "apply", "--if-changed", "--quiet"])
    return (
        "#!/bin/sh\n"
        "# FloofyCrew (unofficial) re-apply wrapper — written by `floofy init`, removed by `floofy deinit`.\n"
        "# Runs `floofy apply --if-changed` (never blocks the host on a failure), then hands over to the real launcher.\n"
        f"# real launcher: {real_launcher}\n"
        "if [ -z \"$FLOOFY_WRAPPER_ACTIVE\" ]; then\n"
        "  FLOOFY_WRAPPER_ACTIVE=1 " + reapply + " >/dev/null 2>&1 || true\n"
        "fi\n"
        f"exec {shlex.quote(str(real_launcher))} \"$@\"\n"
    )


# --- the protocol ------------------------------------------------------------------------------


@dataclass
class TriggerStatus:
    kind: str  # "user-timer" | "path-wrapper" | "loader-startup"
    installed: bool
    detail: str = ""
    paths: list[str] = field(default_factory=list)
    active: bool | None = None  # timer enabled / agent loaded, when knowable
    notes: list[str] = field(default_factory=list)
    platform: str = sys.platform

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "installed": self.installed, "active": self.active, "detail": self.detail, "paths": list(self.paths), "notes": list(self.notes), "platform": self.platform}


@dataclass
class TriggerReport:
    kind: str
    ok: bool
    changed: bool
    detail: str = ""
    paths: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ok": self.ok, "changed": self.changed, "detail": self.detail, "paths": list(self.paths), "commands": [list(c) for c in self.commands]}


@runtime_checkable
class TriggerManager(Protocol):
    kind: str

    def install(self) -> TriggerReport: ...

    def uninstall(self) -> TriggerReport: ...

    def status(self) -> TriggerStatus: ...


# --- the hourly user timer (both editions) ------------------------------------------------------


@dataclass
class UserTimerTrigger:
    """systemd user timer (Linux) / launchd agent (macOS) running ``floofy apply --if-changed`` hourly at :55."""

    command: list[str]
    platform: str = sys.platform
    unit_dir: Path | None = None  # systemd: ~/.config/systemd/user ; launchd: ~/Library/LaunchAgents
    runner: Runner = _default_runner
    environment: dict[str, str] = field(default_factory=dict)
    kind: str = "user-timer"
    #: Skip the ``systemctl``/``launchctl`` calls (write the files only) — for tests and dry runs.
    activate: bool = True

    @property
    def is_linux(self) -> bool:
        return self.platform.startswith("linux")

    @property
    def is_darwin(self) -> bool:
        return self.platform == "darwin"

    def directory(self) -> Path:
        if self.unit_dir is not None:
            return Path(self.unit_dir)
        if self.is_darwin:
            return Path.home() / "Library" / "LaunchAgents"
        base = os.environ.get("XDG_CONFIG_HOME")
        return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"

    def files(self) -> dict[Path, bytes]:
        directory = self.directory()
        if self.is_darwin:
            return {directory / f"{LAUNCHD_LABEL}.plist": launchd_plist(self.command, environment=self.environment)}
        return {directory / name: text.encode("utf-8") for name, text in systemd_units(self.command, environment=self.environment).items()}

    def _run(self, argv: list[str], report: TriggerReport) -> bool:
        report.commands.append(argv)
        if not self.activate:
            return True
        try:
            completed = self.runner(argv)
        except (OSError, subprocess.SubprocessError) as exc:
            report.detail += f" {argv[0]} failed: {exc}."
            return False
        if completed.returncode != 0:
            report.detail += f" {' '.join(argv)} exited {completed.returncode}: {(completed.stderr or completed.stdout or '').strip()[-300:]}."
            return False
        return True

    def install(self) -> TriggerReport:
        report = TriggerReport(self.kind, ok=True, changed=False)
        if not (self.is_linux or self.is_darwin):
            report.ok = False
            report.detail = f"no user timer implementation for {self.platform}; run `floofy apply --if-changed` from your own scheduler"
            return report
        for path, content in self.files().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file() or path.read_bytes() != content:
                path.write_bytes(content)
                report.changed = True
            report.paths.append(str(path))
        if self.is_darwin:
            plist = report.paths[0]
            uid = os.getuid() if hasattr(os, "getuid") else 0
            self._run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], TriggerReport(self.kind, True, False))  # ignore: not loaded yet
            ok = self._run(["launchctl", "bootstrap", f"gui/{uid}", plist], report)
            report.ok = ok
            report.detail = (f"launchd agent {LAUNCHD_LABEL} loaded, fires hourly at :{REAPPLY_MINUTE:02d}" if ok else "plist written; loading the agent failed:" + report.detail)
        else:
            ok = self._run(["systemctl", "--user", "daemon-reload"], report) and self._run(["systemctl", "--user", "enable", "--now", SYSTEMD_TIMER], report)
            report.ok = ok
            report.detail = (f"{SYSTEMD_TIMER} enabled, fires hourly at :{REAPPLY_MINUTE:02d}" if ok else "unit files written; enabling the timer failed:" + report.detail)
        return report

    def uninstall(self) -> TriggerReport:
        report = TriggerReport(self.kind, ok=True, changed=False)
        if self.is_darwin:
            uid = os.getuid() if hasattr(os, "getuid") else 0
            self._run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], report)
        elif self.is_linux and any(p.is_file() for p in self.files()):
            self._run(["systemctl", "--user", "disable", "--now", SYSTEMD_TIMER], report)
        for path in self.files():
            if path.is_file():
                path.unlink()
                report.changed = True
                report.paths.append(str(path))
        if self.is_linux and report.changed:
            self._run(["systemctl", "--user", "daemon-reload"], report)
        report.ok = True  # removal of the files is what matters; a stopped daemon cannot fail it
        report.detail = "removed" if report.changed else "nothing installed"
        return report

    def status(self) -> TriggerStatus:
        files = self.files()
        present = [str(p) for p in files if p.is_file()]
        status = TriggerStatus(self.kind, installed=len(present) == len(files) and bool(files), paths=present, platform=self.platform)
        if not (self.is_linux or self.is_darwin):
            status.detail = f"no user timer implementation for {self.platform}"
            return status
        expected = all(p.is_file() and p.read_bytes() == content for p, content in files.items())
        if status.installed and not expected:
            status.notes.append("unit files differ from what this floofy would write (run `floofy init` to refresh)")
        if status.installed and self.activate:
            if self.is_linux:
                try:
                    completed = self.runner(["systemctl", "--user", "is-enabled", SYSTEMD_TIMER])
                    status.active = completed.returncode == 0
                    status.detail = (completed.stdout or completed.stderr or "").strip()
                except (OSError, subprocess.SubprocessError) as exc:
                    status.notes.append(f"systemctl unavailable: {exc}")
            else:
                try:
                    uid = os.getuid() if hasattr(os, "getuid") else 0
                    completed = self.runner(["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"])
                    status.active = completed.returncode == 0
                    status.detail = "loaded" if status.active else "not loaded"
                except (OSError, subprocess.SubprocessError) as exc:
                    status.notes.append(f"launchctl unavailable: {exc}")
        elif not status.installed:
            status.detail = "not installed (floofy init installs it)"
        return status


# --- the PATH wrapper (public edition, venv installs) -----------------------------------------------


@dataclass
class PathWrapperTrigger:
    """``<bin_dir>/kirocrew`` wrapper around a venv/pipx launcher; a no-op when the launcher is not one."""

    command: list[str]
    real_launcher: Path | None
    bin_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "bin")
    kind: str = "path-wrapper"
    marker: str = "FloofyCrew (unofficial) re-apply wrapper"

    @property
    def wrapper_path(self) -> Path:
        return Path(self.bin_dir) / "kirocrew"

    def applicable(self) -> tuple[bool, str]:
        if self.real_launcher is None:
            return False, "no venv/pipx kirocrew launcher found; the wrapper only applies to venv installs"
        real = Path(self.real_launcher)
        if real.resolve() == self.wrapper_path.resolve() and not self._is_ours(real):
            return False, f"{real} is the PATH entry itself and not a wrapper; refusing to wrap it in place"
        return True, ""

    def _is_ours(self, path: Path) -> bool:
        try:
            return self.marker in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def install(self) -> TriggerReport:
        report = TriggerReport(self.kind, ok=True, changed=False)
        applicable, why = self.applicable()
        if not applicable:
            report.detail = why
            return report
        assert self.real_launcher is not None
        target = self.wrapper_path
        if target.exists() and not self._is_ours(target):
            # a real launcher already sits at the wrapper's place: keep it as the target, wrap beside it
            self.real_launcher = target.resolve()
        content = path_wrapper_script(self.real_launcher, self.command)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(encoding="utf-8", errors="replace") != content:
            target.write_text(content, encoding="utf-8")
            report.changed = True
        target.chmod(target.stat().st_mode | 0o755)
        report.paths.append(str(target))
        report.detail = f"{target} wraps {self.real_launcher}"
        if str(target.parent) not in os.environ.get("PATH", "").split(os.pathsep):
            report.detail += f" (note: {target.parent} is not on PATH)"
        return report

    def uninstall(self) -> TriggerReport:
        report = TriggerReport(self.kind, ok=True, changed=False)
        target = self.wrapper_path
        if target.is_file() and self._is_ours(target):
            target.unlink()
            report.changed = True
            report.paths.append(str(target))
            report.detail = "removed"
        else:
            report.detail = "no FloofyCrew wrapper present"
        return report

    def status(self) -> TriggerStatus:
        target = self.wrapper_path
        ours = target.is_file() and self._is_ours(target)
        status = TriggerStatus(self.kind, installed=ours, paths=[str(target)] if ours else [])
        applicable, why = self.applicable()
        if ours:
            status.detail = f"{target} wraps the launcher"
            status.active = str(target.parent) in os.environ.get("PATH", "").split(os.pathsep)
            if not status.active:
                status.notes.append(f"{target.parent} is not on PATH")
        elif not applicable:
            status.detail = why
        else:
            status.detail = "not installed (floofy init installs it)"
        return status


# --- the Loader's on_startup re-apply, reported --------------------------------------------------------


def loader_startup_status(host_home: Path) -> TriggerStatus:
    """Reported only: the Loader re-applies at every gateway start once installed and enabled."""
    app_dir = Path(host_home) / "apps" / "floofycrew"
    installed = (app_dir / "app.json").is_file()
    enabled: bool | None = None
    try:
        import json  # noqa: PLC0415

        meta = json.loads((app_dir / "installed.json").read_text(encoding="utf-8"))
        enabled = bool(meta.get("enabled")) if isinstance(meta, dict) else None
    except (OSError, ValueError):
        pass
    status = TriggerStatus("loader-startup", installed=installed, active=enabled, paths=[str(app_dir)] if installed else [])
    status.detail = "the Loader app re-applies enabled patches on every gateway start" if installed else "Loader app not installed (floofy init installs it)"
    if installed and enabled is False:
        status.notes.append("the Loader app is installed but disabled: `kirocrew app enable floofycrew` (floofy init offers the agent.apps_trusted grant)")
    return status


# --- collecting the adapters' managers ------------------------------------------------------------------


def trigger_managers(host_home: Path, adapters: Iterable[ModuleType], *, command: list[str] | None = None, edition: str | None = None, **kwargs: Any) -> list[Any]:
    """Every trigger manager the installed adapters offer for ``host_home`` (the edition's own set first)."""
    command = command or self_command(host_home)
    managers: list[Any] = []
    ordered = sorted(adapters, key=lambda a: 0 if getattr(a, "EDITION", None) == edition else 1)
    for adapter in ordered:
        if edition is not None and getattr(adapter, "EDITION", None) not in (None, edition):
            continue
        factory = getattr(adapter, "reapply_triggers", None)
        if not callable(factory):
            continue
        try:
            managers.extend(factory(Path(host_home), list(command), **kwargs) or [])
        except Exception:  # noqa: BLE001 - an adapter without trigger support is not an error
            continue
    return managers
