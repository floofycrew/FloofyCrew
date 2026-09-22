"""The hook registry: ``before`` / ``after`` / ``replace`` around host functions and routes (Requirement 3.4).

Targets:

* ``"pkg.module:attr"`` — an import path. The attribute (``attr`` may be dotted,
  ``Class.method``) on the owning module or class is swapped for one wrapper per
  target; the original is kept and restored **by identity** when the last
  registration on that target is unwound. Async originals get an async wrapper.
* ``"route:GET /api/status"`` — a live gateway route. aiohttp keeps the handler on
  the ``ResourceRoute`` object (``aiohttp/web_urldispatcher.py`` ``AbstractRoute``
  L224 ``self._handler = handler``, read per request through the ``handler``
  property L233), and the dispatcher looks it up at request time, so swapping
  ``route._handler`` takes effect immediately even though the router is frozen
  after startup. The application object comes from the host's App SDK wiring:
  ``kiro_crew.apps.hooks_integration.get_route_registry()`` (L295) returns the
  ``RouteRegistry`` built by ``init_hooks_system`` (L265–L284) whose ``_app`` is
  the dashboard ``web.Application``. Patching a route handler's *module
  attribute* would not reach dispatch (the router holds the function object), so
  route targets are the supported form for handlers; the import-path form covers
  every function the host calls by name.

Semantics (design "Interfaces exposed to mods"): every registration carries the
owning ``mod_id``; ``before(fn)`` runs ``fn(*args, **kwargs)`` first,
``replace(fn)`` runs ``fn(call_next, *args, **kwargs)`` where ``call_next`` is the
rest of the chain ending in the original, ``after(fn)`` runs
``fn(result, *args, **kwargs)`` and its return value becomes the result. Every
mod callback runs inside ``try/except``: an exception is logged with attribution,
reported through ``on_fault(mod_id, target, exc)`` (the runtime marks the mod
``Error``), **all of that mod's registrations are removed** (fail-open → original
behaviour) and the call falls through to the original. Unwinding
(``deactivate(mod_id)`` / ``shutdown()``) walks registrations in reverse order.
"""
from __future__ import annotations

import asyncio
import functools
import importlib
import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["HookRegistry", "HookTargetError", "ModHooks", "Registration", "parse_target"]

logger = logging.getLogger("floofy.loader.hooks")

ROUTE_PREFIX = "route:"
KINDS = ("before", "after", "replace")


class HookTargetError(LookupError):
    """The target does not resolve to a patchable attribute or a live route."""


@dataclass(frozen=True)
class Registration:
    """One hook a mod registered (``registry.registrations()`` lists them for the state API)."""

    mod_id: str
    target: str
    kind: str
    fn: Callable[..., Any]
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {"mod": self.mod_id, "target": self.target, "kind": self.kind, "fn": getattr(self.fn, "__qualname__", repr(self.fn)), "sequence": self.sequence}


def parse_target(target: str) -> tuple[str, str, str]:
    """``("route", "GET", "/api/status")`` or ``("attr", "pkg.module", "Class.method")``."""
    text = target.strip()
    if text.startswith(ROUTE_PREFIX):
        method, _, path = text[len(ROUTE_PREFIX):].strip().partition(" ")
        if not method or not path.startswith("/"):
            raise HookTargetError(f"route target must be 'route:METHOD /path', got {target!r}")
        return "route", method.upper(), path.strip()
    module, sep, attr = text.partition(":")
    if not sep or not module or not attr:
        raise HookTargetError(f"target must be 'pkg.module:attr' or 'route:METHOD /path', got {target!r}")
    return "attr", module, attr


# --- one patched target ------------------------------------------------------------------------


class _Slot:
    """The wrapper state for one target: the original and the registrations layered on it."""

    def __init__(self, registry: "HookRegistry", target: str, original: Callable[..., Any], install: Callable[[Any], None]):
        self.registry = registry
        self.target = target
        self.original = original
        self._install = install
        self.registrations: list[Registration] = []
        self.wrapper = self._build()
        self._install(self.wrapper)

    # -- chain building ------------------------------------------------------------------------

    def _build(self) -> Callable[..., Any]:
        slot = self
        is_async = inspect.iscoroutinefunction(self.original)

        if is_async:

            @functools.wraps(self.original)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return await slot._call_async(args, kwargs)

        else:

            @functools.wraps(self.original)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return slot._call_sync(args, kwargs)

        wrapper.__floofy_slot__ = self  # type: ignore[attr-defined]
        return wrapper

    def _kinds(self, kind: str) -> list[Registration]:
        return [r for r in self.registrations if r.kind == kind]

    def _fault(self, registration: Registration, exc: BaseException) -> None:
        self.registry._fault(registration, exc)

    # -- sync path ------------------------------------------------------------------------------

    def _call_sync(self, args: tuple, kwargs: dict) -> Any:
        for registration in list(self._kinds("before")):
            try:
                registration.fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - fail-open
                self._fault(registration, exc)
        result = self._replaced_sync(list(self._kinds("replace")), args, kwargs)
        for registration in list(self._kinds("after")):
            try:
                result = registration.fn(result, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self._fault(registration, exc)
        return result

    def _replaced_sync(self, replacers: list[Registration], args: tuple, kwargs: dict) -> Any:
        if not replacers:
            return self.original(*args, **kwargs)
        registration = replacers[-1]
        rest = replacers[:-1]

        def call_next(*a: Any, **kw: Any) -> Any:
            return self._replaced_sync(rest, a, kw)

        try:
            return registration.fn(call_next, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - fall through to the rest of the chain
            self._fault(registration, exc)
            return self._replaced_sync([r for r in rest if r.mod_id != registration.mod_id], args, kwargs)

    # -- async path -----------------------------------------------------------------------------

    async def _call_async(self, args: tuple, kwargs: dict) -> Any:
        for registration in list(self._kinds("before")):
            try:
                outcome = registration.fn(*args, **kwargs)
                if asyncio.iscoroutine(outcome):
                    await outcome
            except Exception as exc:  # noqa: BLE001
                self._fault(registration, exc)
        result = await self._replaced_async(list(self._kinds("replace")), args, kwargs)
        for registration in list(self._kinds("after")):
            try:
                outcome = registration.fn(result, *args, **kwargs)
                result = await outcome if asyncio.iscoroutine(outcome) else outcome
            except Exception as exc:  # noqa: BLE001
                self._fault(registration, exc)
        return result

    async def _replaced_async(self, replacers: list[Registration], args: tuple, kwargs: dict) -> Any:
        if not replacers:
            return await self.original(*args, **kwargs)
        registration = replacers[-1]
        rest = replacers[:-1]

        async def call_next(*a: Any, **kw: Any) -> Any:
            return await self._replaced_async(rest, a, kw)

        try:
            outcome = registration.fn(call_next, *args, **kwargs)
            return await outcome if asyncio.iscoroutine(outcome) else outcome
        except Exception as exc:  # noqa: BLE001
            self._fault(registration, exc)
            return await self._replaced_async([r for r in rest if r.mod_id != registration.mod_id], args, kwargs)

    # -- lifecycle ------------------------------------------------------------------------------

    def restore(self) -> None:
        """Put the original object back (identity restored)."""
        self._install(self.original)


# --- the registry ---------------------------------------------------------------------------


class HookRegistry:
    """Process-wide registry of hook registrations with per-mod attribution and an unwind stack."""

    def __init__(self, *, on_fault: Callable[[str, str, BaseException], None] | None = None, router: Callable[[], Any] | None = None):
        self._slots: dict[str, _Slot] = {}
        self._sequence = 0
        self._lock = threading.RLock()
        self._on_fault = on_fault
        self._router = router or _default_router
        self.faults: list[dict[str, str]] = []

    # -- registration -----------------------------------------------------------------------------

    def before(self, mod_id: str, target: str, fn: Callable[..., Any]) -> Registration:
        return self._register(mod_id, target, "before", fn)

    def after(self, mod_id: str, target: str, fn: Callable[..., Any]) -> Registration:
        return self._register(mod_id, target, "after", fn)

    def replace(self, mod_id: str, target: str, fn: Callable[..., Any]) -> Registration:
        return self._register(mod_id, target, "replace", fn)

    def for_mod(self, mod_id: str) -> "ModHooks":
        """The ``ctx.hooks`` facade bound to one mod."""
        return ModHooks(self, mod_id)

    def _register(self, mod_id: str, target: str, kind: str, fn: Callable[..., Any]) -> Registration:
        if kind not in KINDS:
            raise ValueError(kind)
        if not callable(fn):
            raise TypeError(f"hook for {target!r} must be callable")
        with self._lock:
            slot = self._slots.get(target)
            if slot is None:
                slot = self._slots[target] = self._open_slot(target)
            self._sequence += 1
            registration = Registration(mod_id, target, kind, fn, self._sequence)
            slot.registrations.append(registration)
            logger.debug("hook %s registered by %s on %s", kind, mod_id, target)
            return registration

    def _open_slot(self, target: str) -> _Slot:
        kind, first, second = parse_target(target)
        if kind == "route":
            route = self._find_route(first, second)
            original = route.handler

            def install(value: Any) -> None:
                route._handler = value  # noqa: SLF001 - the documented seam (see module docstring)

            return _Slot(self, target, original, install)
        owner, name = self._resolve_owner(first, second)
        original = getattr(owner, name)
        if not callable(original):
            raise HookTargetError(f"{target!r} is not callable")

        def install_attr(value: Any) -> None:
            setattr(owner, name, value)

        return _Slot(self, target, original, install_attr)

    @staticmethod
    def _resolve_owner(module_name: str, attr: str) -> tuple[Any, str]:
        try:
            owner: Any = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            raise HookTargetError(f"cannot import {module_name!r}: {exc}") from exc
        parts = attr.split(".")
        for part in parts[:-1]:
            if not hasattr(owner, part):
                raise HookTargetError(f"{module_name}:{attr}: no attribute {part!r}")
            owner = getattr(owner, part)
        if not hasattr(owner, parts[-1]):
            raise HookTargetError(f"{module_name}:{attr}: no attribute {parts[-1]!r}")
        return owner, parts[-1]

    def _find_route(self, method: str, path: str) -> Any:
        try:
            router = self._router()
        except Exception as exc:  # noqa: BLE001
            raise HookTargetError(f"the gateway router is not reachable: {exc}") from exc
        if router is None:
            raise HookTargetError("the gateway router is not reachable from this process")
        for route in router.routes():
            resource = getattr(route, "resource", None)
            canonical = getattr(resource, "canonical", None)
            if route.method == method and canonical == path:
                return route
        raise HookTargetError(f"no route {method} {path} in the gateway router")

    # -- unwinding ---------------------------------------------------------------------------------

    def deactivate(self, mod_id: str) -> int:
        """Remove every registration of ``mod_id`` (reverse order); restore targets that become empty."""
        with self._lock:
            removed = 0
            for target in list(self._slots):
                slot = self._slots[target]
                mine = [r for r in slot.registrations if r.mod_id == mod_id]
                for registration in reversed(mine):
                    slot.registrations.remove(registration)
                    removed += 1
                if not slot.registrations:
                    slot.restore()
                    del self._slots[target]
            return removed

    def shutdown(self) -> int:
        """Unwind everything, most recent registration first; every target is restored to its original."""
        with self._lock:
            order = sorted((r for slot in self._slots.values() for r in slot.registrations), key=lambda r: -r.sequence)
            removed = len(order)
            for registration in order:
                slot = self._slots.get(registration.target)
                if slot is not None and registration in slot.registrations:
                    slot.registrations.remove(registration)
            for target, slot in list(self._slots.items()):
                slot.restore()
                del self._slots[target]
            return removed

    # -- introspection -----------------------------------------------------------------------------

    def registrations(self, mod_id: str | None = None) -> list[Registration]:
        with self._lock:
            found = [r for slot in self._slots.values() for r in slot.registrations if mod_id is None or r.mod_id == mod_id]
        return sorted(found, key=lambda r: r.sequence)

    def targets(self) -> list[str]:
        return sorted(self._slots)

    def original(self, target: str) -> Any:
        slot = self._slots.get(target)
        return slot.original if slot is not None else None

    def _fault(self, registration: Registration, exc: BaseException) -> None:
        """A mod callback raised: log, drop that mod's hooks (fail-open), tell the runtime."""
        logger.exception("hook %s on %s by mod %s raised %s: %s", registration.kind, registration.target, registration.mod_id, type(exc).__name__, exc)
        self.faults.append({"mod": registration.mod_id, "target": registration.target, "kind": registration.kind, "error": f"{type(exc).__name__}: {exc}"})
        self.deactivate(registration.mod_id)
        if self._on_fault is not None:
            try:
                self._on_fault(registration.mod_id, registration.target, exc)
            except Exception:  # noqa: BLE001 - the runtime's own handler must not take the host down
                logger.exception("on_fault handler failed")


@dataclass
class ModHooks:
    """``ctx.hooks`` — the registry bound to one mod (per-mod attribution)."""

    registry: HookRegistry
    mod_id: str
    _own: list[Registration] = field(default_factory=list, repr=False)

    def before(self, target: str, fn: Callable[..., Any]) -> Registration:
        registration = self.registry.before(self.mod_id, target, fn)
        self._own.append(registration)
        return registration

    def after(self, target: str, fn: Callable[..., Any]) -> Registration:
        registration = self.registry.after(self.mod_id, target, fn)
        self._own.append(registration)
        return registration

    def replace(self, target: str, fn: Callable[..., Any]) -> Registration:
        registration = self.registry.replace(self.mod_id, target, fn)
        self._own.append(registration)
        return registration

    def registrations(self) -> list[Registration]:
        return self.registry.registrations(self.mod_id)

    def unwind(self) -> int:
        return self.registry.deactivate(self.mod_id)


def _default_router() -> Any:
    """The dashboard's aiohttp router, through the host's App SDK wiring (``None`` outside a gateway)."""
    try:
        from kiro_crew.apps.hooks_integration import get_route_registry  # type: ignore[import-not-found]  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    registry = get_route_registry()
    app = getattr(registry, "_app", None) if registry is not None else None
    return getattr(app, "router", None)
