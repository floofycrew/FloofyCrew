"""Activating ``python-hook`` parts inside the gateway (Requirement 2.4, 3.4–3.6).

Each part's module is loaded from the mod directory the way the host loads app
modules (``kiro_crew/apps/module_loader.py`` ``load_app_module``: a file-path
spec, no ``sys.path`` mutation) under the namespaced ``sys.modules`` key
``floofy_mods.<id>.<module>``; the synthetic parents ``floofy_mods`` and
``floofy_mods.<id>`` carry ``__path__`` so relative imports inside the mod
resolve within its own tree. ``activate(ctx)`` runs inside ``try/except``: an
exception disables *that* mod with reason ``Error`` (its modules are dropped,
its hooks unwound by the runtime) and every other mod continues. ``deactivate``
is the same, in reverse order, at shutdown or when the manager disables a mod.

:class:`ModContext` is the ``ctx`` a mod receives. The runtime fills the
collaborators (hook registry, network client, config store, event bus) from
tasks 4.4–4.6; ``host``, ``log``, ``mod_dir`` and ``state`` are always present.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from floofy_core import API_VERSION

from .host import HostFacts

__all__ = ["ActivationError", "MOD_NAMESPACE", "ModContext", "PartHandle", "activate_part", "deactivate_part", "drop_mod_modules", "load_part_module", "module_key"]

logger = logging.getLogger("floofy.loader.activation")

#: Root of the namespaced ``sys.modules`` keys mods are imported under.
MOD_NAMESPACE = "floofy_mods"


class ActivationError(RuntimeError):
    """A part could not be loaded or its ``activate`` raised; ``cause`` keeps the original."""

    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause
        self.traceback = "".join(traceback.format_exception(cause)) if cause is not None else ""


@dataclass
class ModContext:
    """What a mod's ``activate(ctx)`` / ``deactivate(ctx)`` receives (design "Interfaces exposed to mods")."""

    mod_id: str
    version: str
    mod_dir: Path
    host: HostFacts
    log: logging.Logger
    api_version: str = API_VERSION
    #: Filled by the runtime: ``ctx.hooks`` (task 4.4), ``ctx.http`` (4.5), ``ctx.config`` / ``ctx.events`` (4.6),
    #: ``ctx.routes`` (task 11.5: the mod's own backend routes, ``add(method, path, handler)``).
    hooks: Any = None
    http: Any = None
    config: Any = None
    events: Any = None
    routes: Any = None
    #: The mod's private data directory (``<data home>/mods/<id>/.floofy/``) — never a payload root.
    data_dir: Path | None = None
    #: Facts a mod publishes (``ctx.state["marker"] = 1``); exposed as ``exports`` in the state API.
    state: dict[str, Any] = field(default_factory=dict)
    unofficial: bool = True

    def fetch(self, url: str, **kwargs: Any) -> Any:
        """Shorthand for ``ctx.http.fetch``."""
        if self.http is None:
            raise RuntimeError("ctx.http is not available")
        return self.http.fetch(url, **kwargs)


@dataclass
class PartHandle:
    """A loaded ``python-hook`` part: the module and its lifecycle callables."""

    mod_id: str
    part_index: int
    module_name: str
    module: Any
    activate_fn: Callable[[ModContext], Any] | None
    deactivate_fn: Callable[[ModContext], Any] | None
    active: bool = False


def module_key(mod_id: str, module: str) -> str:
    return f"{MOD_NAMESPACE}.{mod_id}.{module}"


def _ensure_namespace(mod_id: str, mod_dir: Path) -> None:
    """Register ``floofy_mods`` and ``floofy_mods.<id>`` as namespace packages rooted in the mod dir."""
    for name, search in ((MOD_NAMESPACE, None), (f"{MOD_NAMESPACE}.{mod_id}", mod_dir)):
        if name in sys.modules:
            continue
        package = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(name, None, is_package=True))
        package.__path__ = [str(search)] if search is not None else []
        sys.modules[name] = package


def mod_module_keys(mod_id: str) -> list[str]:
    prefix = f"{MOD_NAMESPACE}.{mod_id}"
    return [key for key in sys.modules if key == prefix or key.startswith(prefix + ".")]


def drop_mod_modules(mod_id: str) -> int:
    """Forget every module of ``mod_id`` so a re-enable loads fresh code."""
    keys = mod_module_keys(mod_id)
    for key in keys:
        sys.modules.pop(key, None)
    parent = sys.modules.get(MOD_NAMESPACE)
    if parent is not None and hasattr(parent, mod_id):
        delattr(parent, mod_id)
    return len(keys)


def _module_file(mod_dir: Path, part: dict[str, Any]) -> tuple[Path, str, bool]:
    """``(file, dotted module name, is_package)`` for a ``python-hook`` part.

    ``path`` names a package directory (``hook/`` → ``hook/__init__.py``) or a
    module file (``hook.py``); ``module`` (schema: dotted identifier) overrides
    the name derived from the path.
    """
    raw_path = str(part.get("path") or "").strip("/")
    target = (mod_dir / raw_path).resolve()
    try:
        target.relative_to(mod_dir.resolve())
    except ValueError as exc:
        raise ActivationError(f"part path {raw_path!r} escapes the mod directory") from exc
    module = str(part.get("module") or "").strip()
    if target.is_dir():
        return target / "__init__.py", module or target.name, True
    if target.suffix == ".py" and target.is_file():
        return target, module or target.stem, False
    if target.with_suffix(".py").is_file():
        return target.with_suffix(".py"), module or target.name, False
    raise ActivationError(f"python-hook part path {raw_path!r} is neither a package directory nor a module file")


def _same_file(recorded: Any, file: Path) -> bool:
    """Whether a module's ``__file__`` names ``file`` — compared as real paths.

    The early shim records the path ``early.json`` gave it (the data home as the
    user spells it); the Loader resolves the part path (``_module_file``). On a
    host where the home is reached through a symlink (``/home/me`` →
    ``/local/home/me``) the two spellings differ for the same file, and a plain
    string comparison re-imported the module — a second object with fresh globals,
    so ``activate()`` never saw what ``early()`` recorded (owner-verified on the
    test box, 2026-09-21).
    """
    if not isinstance(recorded, str) or not recorded:
        return False
    try:
        return os.path.realpath(recorded) == os.path.realpath(file)
    except OSError:
        return False


def load_part_module(mod_id: str, mod_dir: Path, part: dict[str, Any], part_index: int) -> PartHandle:
    """Import the part under ``floofy_mods.<id>.<module>`` and return its handle (never touches ``sys.path``)."""
    file, module, is_package = _module_file(mod_dir, part)
    if not file.is_file():
        raise ActivationError(f"python-hook module file missing: {file}")
    name = module_key(mod_id, module)
    _ensure_namespace(mod_id, mod_dir)
    existing = sys.modules.get(name)
    if existing is not None and _same_file(getattr(existing, "__file__", None), file):
        # The early shim (floofy_early) imported this very file before platform
        # bootstrap; reuse it so early() and activate() share one module object.
        loaded = existing
    else:
        spec = importlib.util.spec_from_file_location(name, str(file), submodule_search_locations=[str(file.parent)] if is_package else None)
        if spec is None or spec.loader is None:
            raise ActivationError(f"cannot build an import spec for {file}")
        before = set(mod_module_keys(mod_id))
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        try:
            spec.loader.exec_module(loaded)
        except BaseException as exc:  # noqa: BLE001 - fail-open: the mod is disabled, the host continues
            for key in set(mod_module_keys(mod_id)) - before:
                sys.modules.pop(key, None)
            raise ActivationError(f"importing {module} failed: {type(exc).__name__}: {exc}", exc) from exc
    parent = sys.modules.get(f"{MOD_NAMESPACE}.{mod_id}")
    if parent is not None:
        setattr(parent, module.rsplit(".", 1)[-1], loaded)
    activate_fn = getattr(loaded, "activate", None)
    deactivate_fn = getattr(loaded, "deactivate", None)
    if activate_fn is not None and not callable(activate_fn):
        raise ActivationError(f"{module}.activate is not callable")
    return PartHandle(mod_id, part_index, name, loaded, activate_fn if callable(activate_fn) else None, deactivate_fn if callable(deactivate_fn) else None)


def _run_lifecycle(handle: PartHandle, fn: Callable[[ModContext], Any], ctx: ModContext, verb: str) -> None:
    """Call a lifecycle function; a coroutine result is scheduled on the running loop with the same fail-open guard."""
    result = fn(ctx)
    if asyncio.iscoroutine(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            result.close()
            raise ActivationError(f"{handle.module_name}.{verb} returned a coroutine outside a running event loop")

        async def guarded() -> None:
            try:
                await result
            except Exception as exc:  # noqa: BLE001
                ctx.log.exception("%s.%s (async) failed: %s", handle.module_name, verb, exc)
                ctx.state.setdefault("asyncErrors", []).append(f"{verb}: {type(exc).__name__}: {exc}")

        loop.create_task(guarded())


def activate_part(handle: PartHandle, ctx: ModContext) -> None:
    """Run ``activate(ctx)`` fail-open; raises :class:`ActivationError` for the caller to record."""
    if handle.activate_fn is None:
        handle.active = True  # a hook-less module is allowed: importing it is its effect
        return
    try:
        _run_lifecycle(handle, handle.activate_fn, ctx, "activate")
    except ActivationError:
        raise
    except BaseException as exc:  # noqa: BLE001
        raise ActivationError(f"{handle.module_name}.activate raised {type(exc).__name__}: {exc}", exc) from exc
    handle.active = True


def deactivate_part(handle: PartHandle, ctx: ModContext) -> str | None:
    """Run ``deactivate(ctx)`` fail-open; returns an error string instead of raising."""
    error: str | None = None
    if handle.active and handle.deactivate_fn is not None:
        try:
            _run_lifecycle(handle, handle.deactivate_fn, ctx, "deactivate")
        except BaseException as exc:  # noqa: BLE001
            error = f"{handle.module_name}.deactivate raised {type(exc).__name__}: {exc}"
            ctx.log.exception(error)
    handle.active = False
    return error
