"""The Loader runtime the host entry shim delegates to.

One process-wide :class:`LoaderRuntime` holds what the host handed us (the
``AppContext`` from ``kiro_crew/apps/context.py`` L55: ``name``, ``data_dir``,
``logger``, ``health``…), runs the boot sequence (:mod:`floofy_loader.boot`),
keeps the live activations for shutdown, publishes the state
(``loader-state.json`` for ``floofy doctor``; the routes of task 4.6 read the
same object) and wires the per-mod collaborators: the hook registry
(:mod:`floofy_loader.hooks_registry`), the network client (4.5), config store,
logger and event bus (4.6).

Failure of the Loader itself is the shim's business (``hooks.py`` records
``loader-failure.json``; the gateway boots vanilla — Requirement 3.6); this
module may raise. A boot that *completes* removes a stale failure record.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.patcher import AuditLog
from floofy_core.resolver import Reason

from . import __version__, api
from .activation import MOD_NAMESPACE, ModContext, deactivate_part, drop_mod_modules
from .boot import BootDeps, BootResult, boot, deactivate_all
from .hooks_registry import HookRegistry
from .host import HostFacts, read_host_facts
from .net import FloofyHttp, Response
from .paths import FloofyPaths
from .state import utc_now

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
    faults: list[dict[str, Any]] = field(default_factory=list)
    http_clients: dict[str, FloofyHttp] = field(default_factory=dict)
    audit: AuditLog | None = None

    def __post_init__(self) -> None:
        self.hooks = HookRegistry(on_fault=self.on_hook_fault)

    # -- collaborators (tasks 4.5–4.6 add the network client, config store and event bus) -------

    def make_context(self, mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        assert self.facts is not None and self.paths is not None
        http = FloofyHttp.from_manifest(mod_id, manifest, audit=self.audit.record if self.audit is not None else None)
        self.http_clients[mod_id] = http
        return ModContext(
            mod_id=mod_id,
            version=version,
            mod_dir=mod_dir,
            host=self.facts,
            log=logging.getLogger(f"floofy.mods.{mod_id}"),
            hooks=self.hooks.for_mod(mod_id),
            http=http,
            data_dir=self.paths.mod_dir(mod_id),
        )

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

    def on_mod_deactivated(self, mod_id: str) -> None:
        """Unwind every hook registration of ``mod_id`` (Requirement 3.4: restored on deactivate)."""
        removed = self.hooks.deactivate(mod_id)
        if removed:
            logger.info("unwound %d hook registration(s) of %s", removed, mod_id)

    def deps(self) -> BootDeps:
        assert self.facts is not None and self.paths is not None
        return BootDeps(paths=self.paths, facts=self.facts, make_context=self.make_context, log=logger, on_deactivated=self.on_mod_deactivated)

    # -- faults ------------------------------------------------------------------------------

    def on_hook_fault(self, mod_id: str, target: str, exc: BaseException) -> None:
        """A mod's hook callback raised: the registry already dropped its hooks; disable the mod with ``Error``."""
        self.fault_mod(mod_id, f"hook on {target} raised {type(exc).__name__}: {exc}", source="hook", traceback_text="".join(traceback.format_exception(exc)))

    def fault_mod(self, mod_id: str, message: str, *, source: str = "runtime", traceback_text: str = "") -> bool:
        """Disable ``mod_id`` after a runtime failure (hook fault, SPA fault route): fail-open, the host continues.

        Returns ``False`` when the mod is unknown to this boot.
        """
        if self.result is None or mod_id not in self.result.state.mods:
            return False
        record = {"mod": mod_id, "source": source, "message": message, "ts": utc_now()}
        self.faults.append(record)
        activation = self.result.activations.pop(mod_id, None)
        if activation is not None:
            for handle in reversed(activation.handles):
                deactivate_part(handle, activation.ctx)
            drop_mod_modules(mod_id)
        self.hooks.deactivate(mod_id)
        mod_state = self.result.state.mods[mod_id]
        mod_state.errors.append(f"{source}: {message}")
        if traceback_text:
            mod_state.errors.append(traceback_text[-2000:])
        mod_state.set_reason(Reason.Error.value, message)
        for part in mod_state.parts:
            if part.status == "active":
                part.status = "error"
        logger.error("mod %s disabled after a %s fault: %s", mod_id, source, message)
        self.write_state()
        return True

    # -- host-facing verbs ------------------------------------------------------------------

    def startup(self, ctx: Any) -> None:
        self.host_ctx = ctx
        self.startups += 1
        self.boot_started_at = utc_now()
        self.facts = read_host_facts()
        self.paths = FloofyPaths(self.facts.data_home)
        self.audit = AuditLog(self.paths.data_home)
        self.http_clients.clear()
        api.bind(host_facts=self.facts, fetch_impl=self.fetch)
        api.install_alias()
        logger.info("FloofyCrew Loader %s (unofficial) booting: host %s %s/%s data_home=%s", __version__, self.facts.version, self.facts.edition, self.facts.channel, self.facts.data_home)
        self.result = boot(self.deps())
        self.started = True
        self.write_state()
        state = self.result.state
        summary = f"FloofyCrew Loader {__version__} (unofficial): loader={state.loader} active={state.active} inactive={sorted(m for m in state.mods if not state.mods[m].active)}"
        logger.info(summary)
        host_logger = getattr(ctx, "logger", None)
        if host_logger is not None:
            host_logger.info(summary)

    def shutdown(self, ctx: Any) -> None:
        if self.result is not None:
            self.shutdown_errors = deactivate_all(self.result, self.deps())
            self.result.state.loader = "inert"
        leftover = self.hooks.shutdown()
        if leftover:
            logger.warning("hook registry still held %d registration(s) at shutdown; restored their targets", leftover)
        if self.result is not None:
            self.write_state()
        self.started = False
        logger.info("FloofyCrew Loader on_shutdown (%d error(s))", len(self.shutdown_errors))

    def register_routes(self, ctx: Any) -> list:
        return []

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
        payload["faults"] = list(self.faults)
        payload["network"] = {mod_id: client.to_dict() for mod_id, client in sorted(self.http_clients.items())}
        return payload

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
