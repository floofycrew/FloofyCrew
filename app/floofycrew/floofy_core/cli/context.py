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
from ..modstore import InstalledMod, installed_mods, read_enabled, sync_early_list, write_enabled
from ..payloads import DiscoveryResult, Payload, discover_payloads
from .console import Console

__all__ = ["CliContext", "CliError", "ISOLATION_ENV"]

#: When set, discovery is limited to ``--root`` directories (see ``--no-adapters``).
ISOLATION_ENV = "FLOOFY_NO_ADAPTERS"

#: Sub-commands that mutate the user's machine; a gateway-side caller may only run the allowlisted subset.
MUTATING_COMMANDS = frozenset({"init", "deinit", "install", "uninstall", "enable", "disable", "update", "apply", "restore", "yeet", "hold", "dev", "profile", "registry", "vanilla", "self-update", "config"})


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
    #: Gateway-side hook: the running Loader's live state document (``status`` inside the gateway reads it).
    live_state: Callable[[], dict[str, Any]] | None = None
    #: ``--offline``: never contact a network endpoint (the daily FloofyCrew update check is skipped; Requirement 7.7).
    offline: bool = False
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

    def say(self, message: str = "", *, tone: str | None = None) -> None:
        self.console.say(message, tone=tone)

    def paint(self, text: str, tone: str) -> str:
        """A painted fragment for ``say`` (plain when styling is off; Requirement 15.1)."""
        return self.console.paint(text, tone)

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

    def url_opener(self, url: str) -> Any:
        """An adapter's ``urllib`` opener for ``url`` (its network identity), or ``None`` for anonymous HTTPS (design DR-3)."""
        if self.isolated:
            return None
        from ..editions import registry_opener  # noqa: PLC0415

        return registry_opener(url, self.adapters())

    def default_registry_sources(self) -> list[dict[str, Any]]:
        """The registry source rows the edition adapter of the current payload ships (Requirement 8.3)."""
        if self.isolated:
            return []
        from ..editions import default_sources  # noqa: PLC0415

        return default_sources(self.adapters(), edition=self.edition())

    def release_feed(self) -> Any | None:
        """Where this edition publishes FloofyCrew itself (:class:`floofy_core.selfupdate.ReleaseFeed`), or ``None``.

        The test switch ``FLOOFY_RELEASE_FEED`` (a JSON feed document) wins, so a
        suite can point the check at a loopback endpoint; otherwise the adapter of
        the running edition answers, and an isolated run has no feed (Requirement 7.7).
        """
        from ..selfupdate import FeedError, ReleaseFeed, feed_from_env  # noqa: PLC0415

        try:
            override = feed_from_env()
        except FeedError as exc:
            self.warn(str(exc))
            return None
        if override is not None:
            return override
        if self.isolated:
            return None
        from ..editions import release_feed  # noqa: PLC0415

        document = release_feed(self.adapters(), edition=self.edition())
        if document is None:
            return None
        try:
            return ReleaseFeed.from_dict(document)
        except FeedError as exc:
            self.warn(f"release feed of the edition adapter ignored: {exc}")
            return None

    def settings(self) -> Any:
        """FloofyCrew's own user settings (``<data home>/config.json``; :class:`floofy_core.settings.Settings`)."""
        from ..settings import Settings  # noqa: PLC0415

        return Settings.for_data_home(self.data_home)

    def registry_target(self) -> dict[str, Any]:
        """The keyword arguments :meth:`floofy_core.registry.IndexCache.best_version` needs for *this* host (Requirement 9.2).

        ``base_version`` / ``host_version`` / ``channel`` come from the current
        payload, ``edition`` from the adapter probe, ``compat`` is the cached
        matrix (``cache/compat.json``), so the CLI, the manager UI and the
        Loader grade candidates by the same rule.
        """
        from ..compat import CompatCache  # noqa: PLC0415

        current = self.current_payload()
        return {
            "base_version": current.host_version.base if current else "0.0.0",
            "edition": self.edition(),
            "host_version": current.host_version.text if current else None,
            "channel": current.channel if current else None,
            "compat": CompatCache.load(self.home.compat_cache),
        }

    def current_compat_row(self) -> Any | None:
        """The cached matrix row for the running host (``edition × channel × hostVersion``), or ``None`` — what grades a mod ``tested`` (Requirement 8.11)."""
        target = self.registry_target()
        if not target["host_version"]:
            return None
        return target["compat"].row_for(target["edition"], target["channel"], target["host_version"])

    def tier_of(self, mod: InstalledMod, compat_row: Any | None = None) -> str:
        """The source tier of an installed mod (:func:`floofy_core.tier.tier_of`) against the running host's matrix row."""
        from ..modstore import read_source  # noqa: PLC0415
        from ..tier import tier_of  # noqa: PLC0415

        return tier_of(read_source(mod.dir), mod_id=mod.id, version=mod.version, compat_row=compat_row if compat_row is not None else self.current_compat_row())

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
        sync_early_list(self.home)

    def drop_enabled(self, mod_id: str) -> None:
        flags = self.enabled()
        if mod_id in flags:
            del flags[mod_id]
            write_enabled(self.home.enabled, flags)
        sync_early_list(self.home)

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


def build_context(args: Any, *, console: Console, actor: str = "cli", in_gateway: bool = False, on_mutation: Callable[[str], None] | None = None, live_state: Callable[[], dict[str, Any]] | None = None) -> CliContext:
    home = Path(args.home).expanduser() if getattr(args, "home", None) else default_host_home()
    if actor == "cli" and os.environ.get("FLOOFY_ACTOR") in ("trigger", "forge"):
        actor = os.environ["FLOOFY_ACTOR"]  # the re-apply triggers mark their audit rows
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
        live_state=live_state,
        offline=bool(getattr(args, "offline", False)),
    )


def dumps(document: Any) -> str:
    return json.dumps(document, indent=2, default=str, sort_keys=False)
