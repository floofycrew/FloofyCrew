"""Re-apply triggers of the public edition (Requirement 6.1, design DR-3).

The public installer promotes a shadow venv (``~/.kiro/crew-venv-<ver>`` →
``~/.kiro/crew-venv``) and pipx re-creates its venv in place; desktop bundles
update through electron-updater. Three triggers cover that:

* the **hourly user timer** at :55 (systemd user timer on Linux, launchd agent
  on macOS) running ``floofy apply --if-changed``;
* the **``kirocrew`` PATH wrapper** for venv/pipx installs — ``~/.local/bin/kirocrew``
  runs ``floofy apply --if-changed`` and ``exec``\\ s the real launcher, so the
  first ``kirocrew`` command after an update re-applies before the gateway
  starts (:class:`floofy_core.triggers.PathWrapperTrigger`; a no-op when the
  payload is not a venv, e.g. a desktop bundle);
* the **Loader's ``on_startup``** re-apply, present once the Loader app is installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from floofy_core.payloads import Payload
from floofy_core.triggers import PathWrapperTrigger, UserTimerTrigger

__all__ = ["triggers", "venv_launcher"]


def venv_launcher(payload: Payload | None) -> Path | None:
    """The venv/pipx console script ``<venv>/bin/kirocrew`` of the current payload, else ``None``."""
    if payload is None or not (payload.root / "pyvenv.cfg").is_file():
        return None
    for candidate in (payload.root / "bin" / "kirocrew", payload.root / "Scripts" / "kirocrew.exe"):
        if candidate.is_file():
            return candidate
    return None


def triggers(host_home: Path, command: list[str], *, payload: Payload | None = None, unit_dir: Path | None = None, bin_dir: Path | None = None, activate: bool = True, platform: str | None = None, **_ignored: Any) -> list[Any]:
    """The edition's trigger managers; ``unit_dir``/``bin_dir``/``activate``/``platform`` are test hooks."""
    environment = {"FLOOFY_ACTOR": "trigger"}
    timer = UserTimerTrigger(list(command), environment=environment, unit_dir=unit_dir, activate=activate)
    if platform is not None:
        timer.platform = platform
    wrapper = PathWrapperTrigger(list(command), venv_launcher(payload)) if bin_dir is None else PathWrapperTrigger(list(command), venv_launcher(payload), bin_dir=Path(bin_dir))
    return [timer, wrapper]
