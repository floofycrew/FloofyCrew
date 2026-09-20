"""Shared state of one CLI invocation: homes, flags, lazily discovered payloads, gateway, audit.

Everything a sub-command needs hangs off :class:`CliContext`; the expensive
lookups (edition adapters, payload discovery, governance snapshot, a gateway
session) are computed once on first use. ``FLOOFY_NO_ADAPTERS=1`` (set by the
test session) or ``--no-adapters`` limits payload discovery to the ``--root``
directories and the interpreter's own ``find_spec`` (never a live install).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .. import API_VERSION, __version__ as FRAMEWORK_VERSION
from ..audit import AuditLog
from ..datahome import DataHome, default_host_home
from ..gateway import GatewaySession, session_for
from ..governance import GovernanceSnapshot
from ..hostcli import find_launcher
from ..modstore import InstalledMod, installed_mods, read_enabled, write_enabled
from ..payloads import DiscoveryResult, Payload, discover_payloads
from .console import Console

__all__ = ["CliContext", "CliError", "ISOLATION_ENV"]

#: When set, discovery is limited to ``--root`` directories (see ``--no-adapters``).
ISOLATION_ENV = "FLOOFY_NO_ADAPTERS"

#: Sub-commands that mutate the user's machine; a gateway-side caller may only run the allowlisted subset.
MUTATING_COMMANDS = frozenset({"init", "deinit", "install", "uninstall", "enable", "disable", "update", "apply", "restore", "yeet", "hold", "dev", "profile", "registry", "vanilla"})


class CliError(Exception):
    """A user-facing failure: the message is printed and the command exits with ``exit_code``."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class CliContext:
    host_home: Path
    console: Console
    json_mode: bool = False
    assume_yes: bool = False
    roots: list[Path] = field(default_factory=list)
    no_adapters: bool = False
    kirocrew: Path | None = None
    port: int | None = None
    token: str | None = None
    actor: str = "cli"
    #: True when the CLI runs inside the gateway process (the manager UI): never talk to the gateway over HTTP.
    in_gateway: bool = False
    #: The command's machine-readable result (printed in ``--json`` mode, returned by ``run()``).
    result: dict[str, Any] = field(default_factory=dict)
    #: Gateway-side hook: called after a mutation so the runtime reloads (set by the Loader route).
    on_mutation: Callable[[str], None] | None = None
    _adapters: list[ModuleType] | None = field(default=None, repr=False)
    _discovery: DiscoveryResult | None = field(default=None, repr=False)
    _governance: GovernanceSnapshot | None = field(default=None, repr=False)
    _session: GatewaySession | None | bool = field(default=False, repr=False)
    _audit: AuditLog | None = field(default=None, repr=False)

    # -- homes -------------------------------------------------------------------------------

    @property
    def home(self) -> DataHome:
        return DataHome.for_host_home(self.host_home)

    @property
    def data_home(self) -> Path:
        return self.home.data_home

    @property
    def kiro_home(self) -> Path:
        """``~/.kiro`` (``KIRO_HOME`` honoured), where the host's agents directory lives (``config/paths.py`` L624 ``kiro_home``)."""
        override = (os.environ.get("KIRO_HOME") or "").strip()
        return Path(override).expanduser() if override else Path.home() / ".kiro"

    # -- output ------------------------------------------------------------------------------

    def say(self, message: str = "") -> None:
        self.console.say(message)

    def warn(self, message: str) -> None:
        self.console.warn(message)

    def set_result(self, **fields: Any) -> None:
        self.result.update(fields)

    # -- adapters and payloads ---------------------------------------------------------------

    @property
    def isolated(self) -> bool:
        return self.no_adapters or os.environ.get(ISOLATION_ENV) == "1"

    def adapters(self) -> list[ModuleType]:
        if self._adapters is None:
            if self.isolated:
                self._adapters = []
            else:
                from ..editions import load_edition_adapters  # noqa: PLC0415

                self._adapters = load_edition_adapters()
        return self._adapters

    def adapter_for(self, edition: str | None) -> ModuleType | None:
        for adapter in self.adapters():
            if getattr(adapter, "EDITION", None) == edition:
                return adapter
        return None

    def discovery(self) -> DiscoveryResult:
        if self._discovery is None:
            if self.isolated:
                # Only the roots named on the command line plus this interpreter's own kiro_crew
                # (the Loader's in-process case): no edition adapter can list live install roots.
                self._discovery = discover_payloads([], extra_roots=self.roots, include_find_spec=True)
            else:
                from ..editions import combined_edition_probe, edition_providers  # noqa: PLC0415

                adapters = self.adapters()
                self._discovery = discover_payloads(edition_providers(adapters), extra_roots=self.roots, edition_probe=combined_edition_probe(adapters))
        return self._discovery

    def payloads(self) -> list[Payload]:
        return self.discovery().payloads

    def current_payload(self) -> Payload | None:
        """The payload the launcher runs, else the newest one found."""
        payloads = self.payloads()
        return self.discovery().current or (payloads[-1] if payloads else None)

    def edition(self) -> str:
        """The current payload's edition; without an adapter, a stamped ``X.Y.Z.N`` build is the internal one (``kiro_crew/__init__.py`` L10–L46)."""
        current = self.current_payload()
        if current is not None and current.edition not in ("", "unknown"):
            return current.edition
        if current is not None:
            return "internal" if current.host_version.build is not None else "external"
        adapters = self.adapters()
        if len(adapters) == 1 and getattr(adapters[0], "EDITION", None):
            return str(adapters[0].EDITION)
        return "unknown"

    def host_cli_env(self) -> dict[str, str]:
        """Extra environment the edition adapters want for host CLI subprocesses."""
        env: dict[str, str] = {}
        for adapter in self.adapters():
            factory = getattr(adapter, "host_cli_env", None)
            if callable(factory):
                try:
                    env.update({str(k): str(v) for k, v in (factory() or {}).items()})
                except Exception:  # noqa: BLE001 - adapters are optional helpers
                    continue
        return env

    def launcher(self) -> Path | None:
        return find_launcher(self.kirocrew, self.current_payload())

    # -- governance, gateway, audit ----------------------------------------------------------

    def governance(self) -> GovernanceSnapshot:
        if self._governance is None:
            from ..editions import governance_locations  # noqa: PLC0415

            self._governance = GovernanceSnapshot.read(governance_locations(self.host_home, self.current_payload(), self.adapters()))
        return self._governance

    def session(self) -> GatewaySession | None:
        """An authenticated loopback session with the running gateway, or ``None`` (never inside the gateway itself)."""
        if self._session is False:
            if self.in_gateway:
                self._session = None
            else:
                try:
                    self._session = session_for(self.host_home, port=self.port, token=self.token)
                except OSError:
                    self._session = None
        return self._session  # type: ignore[return-value]

    def gateway_running(self) -> bool:
        if self.in_gateway:
            return True
        return self.session() is not None

    @property
    def audit(self) -> AuditLog:
        if self._audit is None:
            self._audit = AuditLog(self.data_home, actor=self.actor)
        return self._audit

    # -- consent -----------------------------------------------------------------------------

    def require_consent(self, what: str) -> None:
        """Requirement 11.1: nothing is applied before the one-time warning was acknowledged (``floofy init``)."""
        from ..consent import read_consent  # noqa: PLC0415

        status = read_consent(self.home.consent)
        if not status.ok:
            raise CliError(f"cannot {what}: {status.detail}", exit_code=3)

    # -- the mod set -------------------------------------------------------------------------

    def mods(self, *, include_broken: bool = True) -> list[InstalledMod]:
        return installed_mods(self.home, include_broken=include_broken)

    def mod(self, mod_id: str) -> InstalledMod:
        for candidate in self.mods():
            if candidate.id == mod_id:
                return candidate
        raise CliError(f"mod {mod_id!r} is not installed (see `floofy status`)")

    def enabled(self) -> dict[str, bool]:
        return read_enabled(self.home.enabled)

    def set_enabled(self, mod_id: str, value: bool) -> None:
        flags = self.enabled()
        flags[mod_id] = value
        write_enabled(self.home.enabled, flags)

    def drop_enabled(self, mod_id: str) -> None:
        flags = self.enabled()
        if mod_id in flags:
            del flags[mod_id]
            write_enabled(self.home.enabled, flags)

    def mutated(self, what: str) -> None:
        """Tell a gateway-side caller that the mod set changed (the Loader route reloads)."""
        if self.on_mutation is not None:
            try:
                self.on_mutation(what)
            except Exception as exc:  # noqa: BLE001 - never fail the CLI on the hook
                self.warn(f"post-mutation hook failed: {exc}")

    # -- facts for reports -------------------------------------------------------------------

    def about(self) -> dict[str, Any]:
        return {
            "version": FRAMEWORK_VERSION,
            "apiVersion": API_VERSION,
            "unofficial": True,
            "python": sys.version.split()[0],
            "interpreter": sys.executable,
            "zipapp": _running_from_zipapp(),
            "hostHome": str(self.host_home),
            "dataHome": str(self.data_home),
        }


def _running_from_zipapp() -> str | None:
    """The ``.pyz`` this process runs from, or ``None`` (a package install / checkout)."""
    archive = getattr(sys.modules.get("__main__"), "__file__", None) or sys.argv[0]
    try:
        for parent in [Path(archive), *Path(archive).parents]:
            if parent.suffix == ".pyz" and parent.is_file():
                return str(parent)
    except (OSError, ValueError):
        return None
    return None


def build_context(args: Any, *, console: Console, actor: str = "cli", in_gateway: bool = False, on_mutation: Callable[[str], None] | None = None) -> CliContext:
    home = Path(args.home).expanduser() if getattr(args, "home", None) else default_host_home()
    return CliContext(
        host_home=home,
        console=console,
        json_mode=bool(getattr(args, "json", False)),
        assume_yes=bool(getattr(args, "yes", False)),
        roots=[Path(r).expanduser() for r in (getattr(args, "root", None) or [])],
        no_adapters=bool(getattr(args, "no_adapters", False)),
        kirocrew=Path(args.kirocrew).expanduser() if getattr(args, "kirocrew", None) else None,
        port=getattr(args, "port", None),
        token=getattr(args, "token", None),
        actor=actor,
        in_gateway=in_gateway,
        on_mutation=on_mutation,
    )


def dumps(document: Any) -> str:
    return json.dumps(document, indent=2, default=str, sort_keys=False)
