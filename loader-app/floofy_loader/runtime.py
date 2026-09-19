"""The Loader runtime the host entry shim delegates to (task 4.1 scaffold).

One process-wide :class:`LoaderRuntime` holds what the host handed us (the
``AppContext`` from ``kiro_crew/apps/context.py`` L55: ``name``, ``data_dir``,
``logger``, ``health``…) and, from task 4.3 on, the boot sequence, the hook
registry, the event bus and the state the routes publish.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import __version__

logger = logging.getLogger("floofy.loader")


@dataclass
class LoaderRuntime:
    """Process-wide Loader state; ``startup`` / ``shutdown`` / ``register_routes`` are the host-facing verbs."""

    host_ctx: Any = None
    started: bool = False
    startups: int = 0
    log: list[str] = field(default_factory=list)

    def startup(self, ctx: Any) -> None:
        self.host_ctx = ctx
        self.startups += 1
        self.started = True
        message = f"FloofyCrew Loader {__version__} (unofficial) on_startup: app={getattr(ctx, 'name', '?')} data_dir={getattr(ctx, 'data_dir', '?')}"
        self.log.append(message)
        logger.info(message)
        host_logger = getattr(ctx, "logger", None)
        if host_logger is not None:
            host_logger.info(message)

    def shutdown(self, ctx: Any) -> None:
        self.started = False
        logger.info("FloofyCrew Loader on_shutdown")

    def register_routes(self, ctx: Any) -> list:
        return []


RUNTIME = LoaderRuntime()


def startup(ctx: Any) -> None:
    RUNTIME.startup(ctx)


def shutdown(ctx: Any) -> None:
    RUNTIME.shutdown(ctx)


def register_routes(ctx: Any) -> list:
    return RUNTIME.register_routes(ctx)
