"""The host's own CLI and config file, driven by the manager (Requirement 2.2, 2.3, 7.1).

Two things the manager needs from the host without importing it:

* **the ``kirocrew`` launcher** — for ``app install|enable|disable|uninstall|dev``
  (``kiro_crew/cli.py`` L2769–L2835) and ``config set`` (``cli_config.py`` L41).
  :func:`find_launcher` looks at an explicit path, then ``PATH``, then the
  payload's own ``bin/kirocrew``; :func:`run_host_cli` runs it with
  ``KIROCREW_HOME`` pointing at the managed host home so ``--home`` is honoured
  by the host too, plus whatever environment the edition adapters ask for
  (``host_cli_env()``).
* **``config.json`` edits with ``kirocrew config set`` semantics** — the host's
  setter parses the value (``_parse_value`` L590: ``true``/``false``, int,
  float, JSON, else string), writes the dotted key creating intermediate
  objects (``_dict_set_create`` L509) under an advisory ``flock`` on the
  sidecar ``<config.json>.lock`` with a temp-file rename that preserves the
  file mode (``config/loader.py`` L1460 ``update_config_locked``). The host CLI
  additionally refuses keys its schema does not declare; the direct edit here
  is used for the ``agent.apps_trusted`` grant (declared, ``config/sections.py``
  L1026) and as the fallback when no launcher is found, and for the restore of
  previous values at uninstall (which ``config set`` cannot express: it has no
  delete).

FloofyCrew never edits the host's governance files (Requirement 11.4);
``config.json`` is the operator's own configuration and ``agent.apps_trusted``
is the operator-writable grant the design names (DR-5, Requirement 2.3).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .governance import LOADER_APP_NAME
from .payloads import Payload

__all__ = [
    "ConfigEdit",
    "HostCliResult",
    "config_get",
    "config_set_many",
    "find_launcher",
    "grant_app_trust",
    "parse_config_value",
    "run_host_cli",
]

_MISSING = object()


@dataclass
class HostCliResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()

    def to_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "returncode": self.returncode, "stdout": self.stdout[-4000:], "stderr": self.stderr[-4000:]}


def find_launcher(explicit: Path | str | None = None, payload: Payload | None = None) -> Path | None:
    """The ``kirocrew`` launcher to drive: ``--kirocrew``, then ``PATH``, then the payload's ``bin/kirocrew``."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    on_path = shutil.which("kirocrew")
    if on_path:
        return Path(on_path)
    if payload is not None:
        for candidate in (payload.root / "bin" / "kirocrew", payload.root / "Scripts" / "kirocrew.exe"):
            if candidate.is_file():
                return candidate
    return None


def run_host_cli(launcher: Path, args: Iterable[str], *, host_home: Path, extra_env: dict[str, str] | None = None, timeout: float = 300.0, cwd: Path | None = None) -> HostCliResult:
    """Run ``kirocrew <args>`` against ``host_home`` (never the process's own home unless that is what was asked)."""
    argv = [str(launcher), *args]
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(host_home)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.update(extra_env or {})
    try:
        completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return HostCliResult(argv, 127, "", f"{type(exc).__name__}: {exc}")
    return HostCliResult(argv, completed.returncode, completed.stdout, completed.stderr)


# --- config.json with `kirocrew config set` semantics -------------------------------------------


def parse_config_value(raw: str) -> Any:
    """``cli_config._parse_value`` (L590–L607): true/false, int, float, JSON, else the string."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return json.loads(raw)
    except ValueError:
        pass
    return raw


def _dict_get(document: dict[str, Any], key: str) -> Any:
    current: Any = document
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _dict_set_create(document: dict[str, Any], key: str, value: Any) -> None:
    """``cli_config._dict_set_create`` (L509–L517): intermediate objects are created."""
    parts = key.split(".")
    current = document
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _dict_delete(document: dict[str, Any], key: str) -> bool:
    """Delete a dotted key; intermediate objects left empty by the deletion are pruned (the restore of an absent key)."""
    parts = key.split(".")
    chain: list[dict[str, Any]] = [document]
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
        chain.append(current)
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    for depth in range(len(chain) - 1, 0, -1):
        if chain[depth] == {}:
            del chain[depth - 1][parts[depth - 1]]
        else:
            break
    return True


@dataclass
class ConfigEdit:
    """The outcome of one locked read-modify-write of ``config.json``."""

    path: Path
    previous: dict[str, Any] = field(default_factory=dict)  # key -> previous value (or the _MISSING marker string "<absent>")
    written: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "previous": dict(self.previous), "written": self.written, "detail": self.detail}


def _locked_rmw(config_path: Path, mutate) -> tuple[dict[str, Any], bool]:
    """Read-modify-write under ``<config>.lock`` (``update_config_locked``): mode-preserving temp+rename."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = config_path.with_name(config_path.name + ".lock")
    lock = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 - held for the whole critical section
    try:
        if sys.platform != "win32":
            import fcntl  # noqa: PLC0415

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        except ValueError as exc:
            raise ValueError(f"{config_path} is not valid JSON; not touching it ({exc})") from exc
        if not isinstance(existing, dict):
            raise ValueError(f"{config_path} is not a JSON object; not touching it")
        before = json.dumps(existing, sort_keys=True)
        updated = mutate(existing)
        if updated is None or json.dumps(updated, sort_keys=True) == before:
            return existing, False
        mode = config_path.stat().st_mode & 0o777 if config_path.is_file() else 0o600
        tmp = config_path.with_name(config_path.name + ".floofy-tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, config_path)
        return updated, True
    finally:
        lock.close()


def config_get(config_path: Path, key: str) -> Any:
    """The current value of a dotted key, or the string ``"<absent>"``."""
    try:
        document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {}
    value = _dict_get(document if isinstance(document, dict) else {}, key)
    return "<absent>" if value is _MISSING else value


def config_set_many(config_path: Path, values: dict[str, Any], *, delete_absent_marker: bool = True) -> ConfigEdit:
    """Set every ``key -> value`` (``"<absent>"`` deletes the key) in one locked write; returns the previous values."""
    edit = ConfigEdit(Path(config_path))

    def mutate(document: dict[str, Any]) -> dict[str, Any]:
        for key, value in values.items():
            current = _dict_get(document, key)
            edit.previous[key] = "<absent>" if current is _MISSING else current
            if value == "<absent>" and delete_absent_marker:
                _dict_delete(document, key)
            else:
                _dict_set_create(document, key, value)
        return document

    _document, edit.written = _locked_rmw(edit.path, mutate)
    edit.detail = "written" if edit.written else "unchanged"
    return edit


def grant_app_trust(config_path: Path, app_name: str = LOADER_APP_NAME) -> ConfigEdit:
    """Record the per-app execution grant for a **local-source** app, in the host's own shape.

    The grant the manager offers with the user's confirmation (Requirement 2.3),
    written exactly as the dashboard's "trust this app" does for an app installed
    from a directory or archive (``dashboard/handlers/security.py`` ``_mutate``):
    the name in ``agent.apps_trusted`` **and** in ``agent.apps_trusted_local``, with
    no ``agent.apps_trusted_repositories`` entry. The host (``apps/execution.py``
    ``_repository_grant_denied_for_binding``) treats a name that is in
    ``apps_trusted`` alone as a legacy grant: it is honoured only while an app with
    positively local provenance is installed under that name, and refused as
    "execution trust predates repository binding" for a fresh install — which is
    what a Loader reinstall is between its uninstall and install steps (seen on a
    live 0.7.0.5: `kirocrew app install` refused with exactly that reason). The
    local marker covers the empty source coordinate without consulting installed
    metadata, so the grant holds across the reinstall. Literal names only;
    idempotent; this is the operator's own config key, not a governance file.
    """
    edit = ConfigEdit(Path(config_path))

    def mutate(document: dict[str, Any]) -> dict[str, Any] | None:
        agent = document.get("agent")
        if not isinstance(agent, dict):
            agent = document["agent"] = {}
        trusted = agent.get("apps_trusted")
        if not isinstance(trusted, list):
            trusted = []
        local = agent.get("apps_trusted_local")
        if not isinstance(local, list):
            local = []
        repositories = agent.get("apps_trusted_repositories")
        if not isinstance(repositories, dict):
            repositories = {}
        edit.previous["agent.apps_trusted"] = list(trusted)
        edit.previous["agent.apps_trusted_local"] = list(local)
        edit.previous["agent.apps_trusted_repositories"] = dict(repositories)
        if app_name in trusted and app_name in local and app_name not in repositories:
            return None
        agent["apps_trusted"] = trusted if app_name in trusted else [*trusted, app_name]
        agent["apps_trusted_local"] = local if app_name in local else [*local, app_name]
        # a stale repository binding from a former registry-owned occupant of the name must not survive
        agent["apps_trusted_repositories"] = {k: v for k, v in repositories.items() if k != app_name}
        return document

    _document, edit.written = _locked_rmw(edit.path, mutate)
    edit.detail = f"agent.apps_trusted and agent.apps_trusted_local now include {app_name!r} (local-source binding)" if edit.written else f"{app_name!r} was already granted as a local-source app"
    return edit
