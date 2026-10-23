"""``POST /host/restart`` — restart the KiroCrew gateway on the user's explicit click (Requirement 16.2, 7.6).

The one action on the manager surface that touches the host process itself. A
staged install, a Loader app update and a vanilla request all take effect at the
next gateway start; until 1.1.6 the App could only say so and point at the
terminal. Now the red "Restart KiroCrew" button asks once (the App's own
``yes-no`` modal through the 409 protocol — nothing here is answered by
default) and then hands the restart to the host's own command, ``kirocrew
restart``, which is service-aware: a systemd/launchd unit is restarted through
its service manager, a foreground gateway is stopped and a detached replacement
started and verified on ``/api/ready`` (``kiro_crew/cli_server.py`` ``_restart``).

The command runs **detached** (its own session, no inherited descriptors) so
the gateway going away under it does not take it down with it; the route
answers ``202`` right away with what it started, and the App polls
``/health`` until the new gateway answers. The launcher is resolved the way
:func:`floofy_core.hostcli.find_launcher` does — ``PATH`` first, then the
payload's own ``bin/kirocrew``, then the sibling of the interpreter the
gateway runs on — and ``KIROCREW_HOME`` names the host home the Loader
manages. Every restart is an ``op: host-restart`` audit row (``actor: app``).

The CLI keeps its stance: ``floofy`` never restarts a live gateway by itself.
This route exists only behind a click and a confirmation on the surface that
lives inside the gateway.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from floofy_core.cli.console import ConfirmationNeeded

__all__ = ["RESTART_PROMPT", "find_restart_launcher", "restart_response", "spawn_restart"]

#: The question the App presents (the exact text ``confirmations.yes`` may list).
RESTART_PROMPT = "Restart the KiroCrew gateway now? The dashboard (and every session it serves) goes away for a few seconds; staged installs, a Loader app update and a vanilla request take effect at the start."


def find_restart_launcher(facts: Any) -> Path | None:
    """The ``kirocrew`` launcher to run ``restart`` with: ``PATH``, the payload's ``bin/``, the gateway interpreter's ``bin/``."""
    on_path = shutil.which("kirocrew")
    if on_path:
        return Path(on_path)
    candidates: list[Path] = []
    payload_root = getattr(facts, "payload_root", None)
    if payload_root is not None:
        candidates += [Path(payload_root) / "bin" / "kirocrew", Path(payload_root) / "Scripts" / "kirocrew.exe"]
    interpreter = getattr(facts, "interpreter", None)
    if interpreter is not None:
        candidates += [Path(interpreter).parent / "kirocrew", Path(interpreter).parent / "kirocrew.exe"]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def adapter_env() -> dict[str, str]:
    """What the edition adapters want in a host CLI subprocess's environment (``host_cli_env()``; the CLI asks the same)."""
    from floofy_core.editions import load_edition_adapters  # noqa: PLC0415

    env: dict[str, str] = {}
    for adapter in load_edition_adapters():
        factory = getattr(adapter, "host_cli_env", None)
        if callable(factory):
            try:
                env.update({str(k): str(v) for k, v in (factory() or {}).items()})
            except Exception:  # noqa: BLE001 - adapters are optional helpers
                continue
    return env


def spawn_restart(launcher: Path, *, host_home: Path, extra_env: dict[str, str] | None = None, popen: Any = subprocess.Popen) -> int:
    """Start ``kirocrew restart`` detached and return its pid (``popen`` is injectable for the tests)."""
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(host_home)
    env.update(extra_env or {})
    process = popen([str(launcher), "restart"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, cwd=str(Path.home()))
    return int(getattr(process, "pid", 0) or 0)


def _confirmed(body: dict[str, Any]) -> bool:
    confirmations = body.get("confirmations")
    if not isinstance(confirmations, dict):
        return False
    yes = confirmations.get("yes")
    return yes is True or (isinstance(yes, list) and RESTART_PROMPT in yes)


def restart_response(runtime: Any, body: Any, *, popen: Any = subprocess.Popen) -> tuple[int, dict[str, str], bytes]:
    """The pure responder: ``409`` with the question until the request carries the answer, then ``202`` with the spawn."""
    import json  # noqa: PLC0415

    def reply(status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, json.dumps(payload, default=str).encode("utf-8")

    if body is None:
        body = {}
    if not isinstance(body, dict):
        return reply(400, {"ok": False, "error": "the body must be a JSON object"})
    if runtime.facts is None or runtime.paths is None:
        return reply(503, {"ok": False, "error": "the Loader has not started"})
    if not _confirmed(body):
        question = ConfirmationNeeded("yes-no", RESTART_PROMPT, detail="missing").to_dict()
        return reply(409, {"ok": False, "confirmation": question, "command": "host restart", "argv": ["kirocrew", "restart"], "transcript": []})
    launcher = find_restart_launcher(runtime.facts)
    audit = getattr(runtime, "audit", None)
    if launcher is None:
        detail = "no `kirocrew` launcher on PATH, in the payload's bin/ or beside the gateway interpreter"
        if audit is not None:
            audit.record("host-restart", actor="app", result="refused", detail=detail)
        return reply(422, {"ok": False, "error": f"cannot restart: {detail}; run `kirocrew restart` at a terminal", "command": "host restart", "argv": ["kirocrew", "restart"], "transcript": [detail]})
    try:
        pid = spawn_restart(launcher, host_home=runtime.facts.host_home, extra_env=adapter_env(), popen=popen)
    except (OSError, subprocess.SubprocessError) as exc:
        if audit is not None:
            audit.record("host-restart", actor="app", result="failed", detail=f"{type(exc).__name__}: {exc}", files=[str(launcher)])
        return reply(500, {"ok": False, "error": f"`{launcher} restart` could not be started: {type(exc).__name__}: {exc}", "command": "host restart", "argv": [str(launcher), "restart"], "transcript": []})
    if audit is not None:
        audit.record("host-restart", actor="app", result="ok", detail=f"`{launcher} restart` started detached (pid {pid}) on the user's confirmation", files=[str(launcher)], pid=pid)
    line = f"restarting the gateway: `{launcher} restart` started (pid {pid}); the dashboard comes back when the new gateway answers"
    return reply(202, {"ok": True, "restarting": True, "launcher": str(launcher), "pid": pid, "command": "host restart", "argv": [str(launcher), "restart"], "transcript": [line]})
