"""The Loader runtime the host entry shim delegates to.

One process-wide :class:`LoaderRuntime` holds what the host handed us (the
``AppContext`` from ``kiro_crew/apps/context.py`` L55: ``name``, ``data_dir``,
``logger``, ``health``…), runs the boot sequence (:mod:`floofy_loader.boot`),
keeps the live activations for shutdown, publishes the state
(``loader-state.json`` for ``floofy doctor``; the routes of task 4.6 read the
same object) and wires the per-mod collaborators: the hook registry (4.4), the
network client (4.5), config store, logger and event bus (4.6).

Failure of the Loader itself is the shim's business (``hooks.py`` records
``loader-failure.json``; the gateway boots vanilla — Requirement 3.6); this
module may raise. A boot that *completes* removes a stale failure record.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, api
from .activation import ModContext
from .boot import BootDeps, BootResult, boot, deactivate_all
from .host import HostFacts, read_host_facts
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

    # -- collaborators (tasks 4.4–4.6 replace the stand-ins) -------------------------------

    def make_context(self, mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        assert self.facts is not None and self.paths is not None
        return ModContext(
            mod_id=mod_id,
            version=version,
            mod_dir=mod_dir,
            host=self.facts,
            log=logging.getLogger(f"floofy.mods.{mod_id}"),
            data_dir=self.paths.mod_dir(mod_id),
        )

    def on_mod_deactivated(self, mod_id: str) -> None:
        """Unwind hook registrations of ``mod_id`` (task 4.4)."""
        return None

    def deps(self) -> BootDeps:
        assert self.facts is not None and self.paths is not None
        return BootDeps(paths=self.paths, facts=self.facts, make_context=self.make_context, log=logger, on_deactivated=self.on_mod_deactivated)

    # -- host-facing verbs ------------------------------------------------------------------

    def startup(self, ctx: Any) -> None:
        self.host_ctx = ctx
        self.startups += 1
        self.boot_started_at = utc_now()
        self.facts = read_host_facts()
        self.paths = FloofyPaths(self.facts.data_home)
        api.bind(host_facts=self.facts)
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
