"""The Loader runtime the host entry shim delegates to.

One process-wide :class:`LoaderRuntime` holds what the host handed us (the
``AppContext`` from ``kiro_crew/apps/context.py`` L55: ``name``, ``data_dir``,
``logger``, ``health``…), runs the boot sequence (:mod:`floofy_loader.boot`),
keeps the live activations for shutdown, publishes the state
(``loader-state.json`` for ``floofy doctor``; the routes of
:mod:`floofy_loader.routes` read the same object) and wires the per-mod
collaborators: the hook registry (:mod:`floofy_loader.hooks_registry`), the
network client (:mod:`floofy_loader.net`), the config store and logger
(:mod:`floofy_loader.storage`) and the event bus (:mod:`floofy_loader.events`).

Failure of the Loader itself is the shim's business (``hooks.py`` records
``loader-failure.json``; the gateway boots vanilla — Requirement 3.6); this
module may raise. A boot that *completes* removes a stale failure record.
"""
from __future__ import annotations

import json
import logging
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.audit import AuditLog
from floofy_core.resolver import Reason

from . import __version__, api
from .activation import MOD_NAMESPACE, ModContext, deactivate_part, drop_mod_modules
from .boot import BootDeps, BootResult, boot, deactivate_all, run_deferred_patches
from .events import EventBus, ModEvents
from .hooks_registry import HookRegistry
from .host import HostFacts, read_host_facts
from .modroutes import ModRouteRegistry
from .net import FloofyHttp, Response
from .paths import FloofyPaths
from .state import utc_now
from .storage import ModConfig, mod_data_dir, mod_logger

logger = logging.getLogger("floofy.loader")


@dataclass
class LoaderRuntime:
    """Process-wide Loader state; ``startup`` / ``shutdown`` / ``register_routes`` are the host-facing verbs."""

    host_ctx: Any = None
    facts: HostFacts | None = None
    paths: FloofyPaths | None = None
    result: BootResult | None = None
    started: bool = False
    startups: int = 0
    shutdown_errors: list[str] = field(default_factory=list)
    boot_started_at: str = ""
    hooks: HookRegistry = field(default_factory=HookRegistry)
    events: EventBus = field(default_factory=EventBus)
    #: The mods' own backend routes (``ctx.routes``; served under ``/mods/{id}/api/`` — task 11.5).
    mod_routes: ModRouteRegistry = field(default_factory=ModRouteRegistry)
    faults: list[dict[str, Any]] = field(default_factory=list)
    http_clients: dict[str, FloofyHttp] = field(default_factory=dict)
    audit: AuditLog | None = None
    #: The outcome of the in-process host-version-change handling that ran before this boot (Requirement 6.2).
    host_change: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.hooks = HookRegistry(on_fault=self.on_hook_fault)

    # -- versions ----------------------------------------------------------------------------------

    @staticmethod
    def api_version() -> str:
        return api.API_VERSION

    @staticmethod
    def loader_version() -> str:
        return __version__

    # -- per-mod collaborators -------------------------------------------------------------------------

    def make_context(self, mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        assert self.facts is not None and self.paths is not None
        audit = self.audit.record if self.audit is not None else None
        http = FloofyHttp.from_manifest(mod_id, manifest, audit=audit)
        self.http_clients[mod_id] = http
        payload_root = self.facts.payload_root
        return ModContext(
            mod_id=mod_id,
            version=version,
            mod_dir=mod_dir,
            host=self.facts,
            log=mod_logger(self.paths.data_home, mod_id, payload_root=payload_root),
            hooks=self.hooks.for_mod(mod_id),
            http=http,
            config=ModConfig(self.paths.data_home, mod_id, payload_root=payload_root),
            events=ModEvents(self.events, mod_id),
            routes=self.mod_routes.for_mod(mod_id),
            data_dir=mod_data_dir(self.paths.data_home, mod_id, payload_root=payload_root),
        )

    def on_mod_deactivated(self, mod_id: str) -> None:
        """Unwind hooks and subscriptions of ``mod_id`` (Requirement 3.4, 3.8)."""
        removed = self.hooks.deactivate(mod_id)
        dropped = self.events.unsubscribe_mod(mod_id)
        routes = self.mod_routes.remove(mod_id)
        if removed or dropped or routes:
            logger.info("unwound %d hook(s), %d subscription(s) and %d route(s) of %s", removed, dropped, routes, mod_id)
        version = self.result.state.mods[mod_id].version if self.result is not None and mod_id in self.result.state.mods else None
        self.events.publish("mod.deactivated", {"mod": mod_id, "version": version})

    def http_for(self, mod_id: str) -> FloofyHttp:
        """The network client of ``mod_id``; an unknown caller gets the strictest policy (no declared hosts)."""
        client = self.http_clients.get(mod_id)
        if client is None:
            client = self.http_clients[mod_id] = FloofyHttp(mod_id, (), self.audit.record if self.audit is not None else None)
        return client

    def fetch(self, url: str, **kwargs: Any) -> Response:
        """``floofy.fetch``: attributed to the calling mod by its ``floofy_mods.<id>`` module name (Requirement 11.6 d)."""
        caller = api.caller_module(depth=2)
        prefix = MOD_NAMESPACE + "."
        mod_id = caller[len(prefix):].split(".", 1)[0] if caller.startswith(prefix) else f"unknown:{caller}"
        return self.http_for(mod_id).fetch(url, **kwargs)

    def deps(self, *, defer_patches: bool = False) -> BootDeps:
        assert self.facts is not None and self.paths is not None
        return BootDeps(paths=self.paths, facts=self.facts, make_context=self.make_context, log=logger, on_deactivated=self.on_mod_deactivated, defer_patches=defer_patches)

    # -- faults ------------------------------------------------------------------------------

    def on_hook_fault(self, mod_id: str, target: str, exc: BaseException) -> None:
        """A mod's hook callback raised: the registry already dropped its hooks; disable the mod with ``Error``."""
        self.fault_mod(mod_id, f"hook on {target} raised {type(exc).__name__}: {exc}", source="hook", traceback_text="".join(traceback.format_exception(exc)))

    def fault_mod(self, mod_id: str, message: str, *, source: str = "runtime", traceback_text: str = "") -> bool:
        """Disable ``mod_id`` after a runtime failure (hook fault, SPA fault report): fail-open, the host continues.

        Returns ``False`` when the mod is unknown to this boot.
        """
        if self.result is None or mod_id not in self.result.state.mods or self.paths is None:
            return False
        record = {"mod": mod_id, "source": source, "message": message, "ts": utc_now()}
        self.faults.append(record)
        activation = self.result.activations.pop(mod_id, None)
        if activation is not None:
            for handle in reversed(activation.handles):
                deactivate_part(handle, activation.ctx)
            drop_mod_modules(mod_id)
        self.hooks.deactivate(mod_id)
        self.events.unsubscribe_mod(mod_id)
        self.mod_routes.remove(mod_id)
        mod_state = self.result.state.mods[mod_id]
        mod_state.errors.append(f"{source}: {message}")
        if traceback_text:
            mod_state.errors.append(traceback_text[-2000:])
        mod_state.set_reason(Reason.Error.value, message)
        for part in mod_state.parts:
            if part.status == "active" or (source == "spa" and part.kind == "spa"):
                part.status = "error"
                if source == "spa" and part.kind == "spa":
                    part.detail = message[:200]
        shutil.rmtree(self.paths.spa_dir(mod_id), ignore_errors=True)
        logger.error("mod %s disabled after a %s fault: %s", mod_id, source, message)
        self.events.publish("mod.deactivated", {"mod": mod_id, "version": mod_state.version})
        self.events.publish("mod.faulted", {"mod": mod_id, "source": source, "message": message})
        self.write_state()
        return True

    # -- host-facing verbs ------------------------------------------------------------------

    def startup(self, ctx: Any) -> None:
        self.host_ctx = ctx
        self.startups += 1
        self.boot_started_at = utc_now()
        self.facts = read_host_facts()
        self.paths = FloofyPaths(self.facts.data_home)
        self.audit = AuditLog(self.paths.data_home, actor="loader")
        self.http_clients.clear()
        api.bind(host_facts=self.facts, fetch_impl=self.fetch, event_bus=self.events)
        api.install_alias()
        logger.info("FloofyCrew Loader %s (unofficial) booting: host %s %s/%s data_home=%s", __version__, self.facts.version, self.facts.edition, self.facts.channel, self.facts.data_home)
        previous = self._previous_host_version()
        if previous and previous != self.facts.version:
            self._handle_host_change(previous)
        self.result = boot(self.deps())
        self.started = True
        self.write_state()
        self._announce(previous)
        state = self.result.state
        summary = f"FloofyCrew Loader {__version__} (unofficial): loader={state.loader} active={state.active} inactive={sorted(m for m in state.mods if not state.mods[m].active)}"
        logger.info(summary)
        host_logger = getattr(ctx, "logger", None)
        if host_logger is not None:
            host_logger.info(summary)

    def reload(self, *, defer_patches: bool = False) -> None:
        """Re-run the boot sequence (the manager's "apply now"): deactivate everything, then boot again.

        ``defer_patches`` leaves the Patcher pass for :meth:`finish_patches` — what
        :meth:`reload_async` uses so a request handler never holds the gateway's
        event loop through the file work (the host's loop watchdog exits the
        process after ~20 s of silence).
        """
        if self.facts is None or self.paths is None:
            raise RuntimeError("the Loader has not started")
        if self.result is not None:
            deactivate_all(self.result, self.deps())
        self.hooks.shutdown()
        self.mod_routes = ModRouteRegistry()
        self.http_clients.clear()
        self.boot_started_at = utc_now()
        self.result = boot(self.deps(defer_patches=defer_patches))
        self.write_state()
        self._announce(None)

    def finish_patches(self) -> bool:
        """Run the Patcher pass a deferred reload left behind (in a worker thread) and publish the state again."""
        if self.result is None:
            return False
        ran = run_deferred_patches(self.result, self.deps())
        if ran:
            self.write_state()
        return ran

    async def reload_async(self) -> None:
        """The reload for a request handler: the boot on the event loop, the Patcher pass off it."""
        import asyncio  # noqa: PLC0415

        self.reload(defer_patches=True)
        await asyncio.get_running_loop().run_in_executor(None, self.finish_patches)

    def shutdown(self, ctx: Any) -> None:
        if self.result is not None:
            self.shutdown_errors = deactivate_all(self.result, self.deps())
            self.result.state.loader = "inert"
        leftover = self.hooks.shutdown()
        if leftover:
            logger.warning("hook registry still held %d registration(s) at shutdown; restored their targets", leftover)
        self.mod_routes = ModRouteRegistry()
        if self.result is not None:
            self.write_state()
        self.started = False
        logger.info("FloofyCrew Loader on_shutdown (%d error(s))", len(self.shutdown_errors))

    def register_routes(self, ctx: Any) -> list:
        """``backend.hooks.routes``: the host's ``AppRoute`` list (empty, with a log line, outside a gateway)."""
        try:
            from .routes import build_routes  # noqa: PLC0415 - needs aiohttp, present only inside the gateway

            return build_routes(self)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.split(".")[0] in ("aiohttp", "kiro_crew"):
                logger.info("routes not registered: %s is not importable (not running inside a gateway)", exc.name)
                return []
            raise

    # -- host version change -------------------------------------------------------------------

    def _handle_host_change(self, previous_version: str) -> None:
        """The Loader is a re-apply trigger (Requirement 6.1): a new host version runs the manager's
        host-change handling in-process — reporter, anchors, compat, re-apply, yeet — BEFORE the mods boot,
        so a mod the new version breaks is quarantined rather than activated (Requirement 6.2)."""
        assert self.facts is not None
        try:
            from floofy_core.cli.main import run as cli_run  # noqa: PLC0415

            argv = ["apply", "--if-changed", "--no-verify"]
            if self.facts.package_dir is not None:
                argv = ["--root", str(self.facts.package_dir.parent), *argv]  # the running payload, whatever discovery else finds
            result = cli_run(argv, home=self.facts.host_home, in_gateway=True, actor="loader", assume_yes=True)
            self.host_change = {"previous": previous_version, "current": self.facts.version, "exit": result.exit, "outcome": result.json, "stderr": result.stderr[-2000:]}
            yeeted = [y.get("id") for y in (result.json.get("yeeted") or [])]
            logger.info("host version changed %s -> %s: host-change handling exit %s, yeeted %s", previous_version, self.facts.version, result.exit, yeeted)
        except Exception as exc:  # noqa: BLE001 - never fatal for the boot
            self.host_change = {"previous": previous_version, "current": self.facts.version, "error": f"{type(exc).__name__}: {exc}"}
            logger.exception("host-change handling failed; booting anyway")

    # -- events ------------------------------------------------------------------------------

    def _previous_host_version(self) -> str | None:
        assert self.paths is not None
        try:
            previous = json.loads(self.paths.loader_state.read_text(encoding="utf-8"))
            return str(previous.get("host", {}).get("version")) if isinstance(previous, dict) else None
        except (OSError, ValueError):
            return None

    def _announce(self, previous_version: str | None) -> None:
        assert self.result is not None and self.facts is not None
        state = self.result.state
        if previous_version and previous_version != self.facts.version:
            self.events.publish("host.version_changed", {"previous": previous_version, "current": self.facts.version})
        for mod_id in state.active:
            self.events.publish("mod.activated", {"mod": mod_id, "version": state.mods[mod_id].version})
        for mod_id, mod_state in state.mods.items():
            if mod_state.quarantined:
                self.events.publish("mod.quarantined", {"mod": mod_id, "version": mod_state.version, "hostVersion": self.facts.version})
        self.events.publish("loader.state", {"loader": state.loader, "active": state.active, "bootedAt": state.booted_at})

    # -- state -------------------------------------------------------------------------------

    def state_dict(self) -> dict[str, Any]:
        if self.result is None:
            return {"loader": "inert", "detail": "not booted", "api_version": api.API_VERSION, "unofficial": True}
        self.result.refresh_exports()
        payload = self.result.state.to_dict()
        payload["api_version"] = api.API_VERSION
        payload["loaderVersion"] = __version__
        payload["unofficial"] = True
        payload["hooks"] = [r.to_dict() for r in self.hooks.registrations()]
        for mod_id, mod_payload in payload["mods"].items():
            mod_payload["routes"] = self.mod_routes.listing(mod_id)  # the mod's own backend routes (floofy.mod(id).routes)
        payload["faults"] = list(self.faults)
        payload["network"] = {mod_id: client.to_dict() for mod_id, client in sorted(self.http_clients.items())}
        payload["events"] = self.events.to_dict()
        payload["hostChange"] = self.host_change
        payload["selfUpdate"] = self.self_update_summary()
        return payload

    def self_update_summary(self) -> dict[str, Any]:
        """The FloofyCrew release notice for the App (Requirement 7.7), from the CLI's cached daily check — no network inside the gateway.

        ``cache/self-update.json`` is written by ``floofy doctor``/``status``/
        ``self-update`` (also when the App runs ``doctor`` in-process); this
        re-grades it for the Loader's own version and the host facts and adds the
        staged Loader app update, if any. Nothing cached yet is ``not-checked``.
        """
        from floofy_core.selfupdate import UpdateCheck, read_stage  # noqa: PLC0415

        if self.paths is None or self.facts is None:
            return {"available": False, "status": "not-checked", "detail": "the Loader has not started"}
        staged = read_stage(self.paths.pending)
        record = UpdateCheck.load(self.paths.self_update_cache)
        if record is None:
            return {"available": False, "status": "not-checked", "detail": "no check cached yet (floofy doctor or status runs it, at most once a day)", "staged": staged, "running": __version__}
        record.regrade(running=__version__, edition=self.facts.edition, channel=self.facts.channel, host_version=self.facts.version)
        return {**record.summary(), "staged": staged}

    def write_state(self) -> None:
        if self.paths is None:
            return
        try:
            self.paths.data_home.mkdir(parents=True, exist_ok=True)
            tmp = self.paths.loader_state.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state_dict(), indent=2, default=str) + "\n", encoding="utf-8")
            tmp.replace(self.paths.loader_state)
            self._clear_stale_failure()
        except OSError as exc:
            logger.warning("could not write loader-state.json: %s", exc)

    def _clear_stale_failure(self) -> None:
        """Drop a ``loader-failure.json`` left by an earlier boot; one written during this boot (a routes failure) stays."""
        assert self.paths is not None
        failure = self.paths.loader_failure
        if not failure.exists():
            return
        try:
            record = json.loads(failure.read_text(encoding="utf-8"))
            recorded_at = str(record.get("ts", "")) if isinstance(record, dict) else ""
        except (OSError, ValueError):
            recorded_at = ""
        if not recorded_at or recorded_at < self.boot_started_at:
            failure.unlink()


RUNTIME = LoaderRuntime()


def startup(ctx: Any) -> None:
    RUNTIME.startup(ctx)


def shutdown(ctx: Any) -> None:
    RUNTIME.shutdown(ctx)


def register_routes(ctx: Any) -> list:
    return RUNTIME.register_routes(ctx)
