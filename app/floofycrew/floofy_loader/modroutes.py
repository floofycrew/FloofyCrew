"""A mod's own backend routes (Requirement 16.4 ``floofy.mod(id).routes``; design "Manager App").

The host's route registry is static — ``backend.hooks.routes`` runs once at
startup — so a mod cannot add gateway routes of its own. The Loader registers,
at startup, one catch-all per method and depth under
``/api/apps/floofycrew/mods/{id}/api/{p1}[/{p2}[/{p3}[/{p4}]]]`` and dispatches
at request time to whatever the mod's ``python-hook`` registered in
``activate(ctx)`` through ``ctx.routes``::

    def activate(ctx):
        ctx.routes.add("GET", "echo", handle_echo)          # /api/apps/floofycrew/mods/<id>/api/echo
        ctx.routes.add("POST", "items/{key}", handle_item)  # one {param} per segment

    def handle_echo(request):                               # sync or async
        return {"config": request.mod_config, "query": request.query}

``handler(request)`` receives a :class:`ModRequest` (``method``, ``path``,
``params``, ``query``, ``headers``, ``body`` bytes, ``json()``, ``text``,
``mod_id``) and returns a JSON-able object (a 200 JSON reply), ``(status, body)``,
``(status, headers, body)`` or a :class:`ModResponse`. A handler that raises
answers 500 with the exception's name and message, logged to the mod's logger —
never a fault: a bad request must not disable a mod. Registrations belong to the
mod and unwind with it (deactivate, fault, shutdown). The registry is process-
wide and thread-safe for the runtime's use; the UI reads ``mods[id].routes`` in
the state document to know what a mod offers.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["MOD_API_MAX_DEPTH", "METHODS", "ModRequest", "ModResponse", "ModRouteRegistry", "ModRoutes", "RouteRegistration", "normalize_path"]

logger = logging.getLogger("floofy.loader.modroutes")

#: The methods the Loader's catch-alls accept for a mod's routes.
METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "DELETE")
#: Segments below ``/mods/{id}/api/`` the catch-alls cover (the host matches one segment per ``{param}``).
MOD_API_MAX_DEPTH = 4
_PARAM = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")


def normalize_path(path: str) -> str:
    """``/echo/`` → ``echo``; a route path is relative to the mod's api root, one to four segments."""
    text = str(path or "").strip().strip("/")
    if not text:
        raise ValueError("a route path needs at least one segment")
    segments = text.split("/")
    if len(segments) > MOD_API_MAX_DEPTH:
        raise ValueError(f"a route path may have at most {MOD_API_MAX_DEPTH} segments, got {len(segments)}")
    for segment in segments:
        if not (_PARAM.match(segment) or _SEGMENT.match(segment)) or segment.strip(".") == "":
            raise ValueError(f"route segment {segment!r} must be [A-Za-z0-9._~-]+ (not only dots) or a {{param}}")
    return "/".join(segments)


@dataclass
class ModRequest:
    """What a mod's handler receives."""

    mod_id: str
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


@dataclass
class ModResponse:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    def encode(self) -> tuple[int, dict[str, str], bytes]:
        headers = {"Cache-Control": "no-store", **self.headers}
        if isinstance(self.body, (bytes, bytearray)):
            headers.setdefault("Content-Type", "application/octet-stream")
            return self.status, headers, bytes(self.body)
        if isinstance(self.body, str):
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
            return self.status, headers, self.body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        return self.status, headers, json.dumps(self.body, default=str).encode("utf-8")


@dataclass(frozen=True)
class RouteRegistration:
    mod_id: str
    method: str
    path: str
    handler: Callable[..., Any]

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "path": self.path, "handler": getattr(self.handler, "__qualname__", repr(self.handler))}

    def match(self, method: str, path: str) -> dict[str, str] | None:
        if method != self.method:
            return None
        mine, theirs = self.path.split("/"), path.split("/")
        if len(mine) != len(theirs):
            return None
        params: dict[str, str] = {}
        for pattern, actual in zip(mine, theirs):
            named = _PARAM.match(pattern)
            if named:
                params[named.group(1)] = actual
            elif pattern != actual:
                return None
        return params


class ModRouteRegistry:
    """Every mod's registrations; the runtime dispatches the Loader's catch-alls through it."""

    def __init__(self) -> None:
        self._routes: dict[str, list[RouteRegistration]] = {}
        self._lock = threading.Lock()

    def add(self, mod_id: str, method: str, path: str, handler: Callable[..., Any]) -> RouteRegistration:
        verb = str(method).upper()
        if verb not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}, got {method!r}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        registration = RouteRegistration(mod_id, verb, normalize_path(path), handler)
        with self._lock:
            rows = self._routes.setdefault(mod_id, [])
            rows[:] = [r for r in rows if not (r.method == registration.method and r.path == registration.path)]
            rows.append(registration)
        return registration

    def remove(self, mod_id: str, method: str | None = None, path: str | None = None) -> int:
        with self._lock:
            rows = self._routes.get(mod_id, [])
            if method is None and path is None:
                count = len(rows)
                self._routes.pop(mod_id, None)
                return count
            wanted = (str(method).upper() if method else None, normalize_path(path) if path else None)
            keep = [r for r in rows if not ((wanted[0] is None or r.method == wanted[0]) and (wanted[1] is None or r.path == wanted[1]))]
            removed = len(rows) - len(keep)
            self._routes[mod_id] = keep
            return removed

    def registrations(self, mod_id: str | None = None) -> list[RouteRegistration]:
        with self._lock:
            if mod_id is not None:
                return list(self._routes.get(mod_id, []))
            return [r for rows in self._routes.values() for r in rows]

    def listing(self, mod_id: str) -> list[dict[str, Any]]:
        return [{"method": r.method, "path": r.path} for r in self.registrations(mod_id)]

    def for_mod(self, mod_id: str) -> "ModRoutes":
        return ModRoutes(self, mod_id)

    def resolve(self, mod_id: str, method: str, path: str) -> tuple[RouteRegistration, dict[str, str]] | None:
        try:
            wanted = normalize_path(path)
        except ValueError:
            return None
        for registration in self.registrations(mod_id):
            params = registration.match(method.upper(), wanted)
            if params is not None:
                return registration, params
        return None

    async def dispatch(self, mod_id: str, method: str, path: str, *, query: dict[str, list[str]] | None = None, headers: dict[str, str] | None = None, body: bytes = b"", log: logging.Logger | None = None) -> tuple[int, dict[str, str], bytes]:
        """Run the matching handler (sync or async) fail-open: an exception is a 500 for this request only."""
        found = self.resolve(mod_id, method, path)
        if found is None:
            return ModResponse(404, {"ok": False, "error": f"{mod_id} has no {method.upper()} route {path!r}", "routes": self.listing(mod_id)}).encode()
        registration, params = found
        request = ModRequest(mod_id, method.upper(), normalize_path(path), params, dict(query or {}), dict(headers or {}), body)
        try:
            result = registration.handler(request)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - the mod's bug is this request's 500, never the gateway's
            (log or logger).error("mod %s route %s %s raised %s: %s", mod_id, request.method, request.path, type(exc).__name__, exc)
            return ModResponse(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}", "mod": mod_id, "route": f"{request.method} {request.path}"}).encode()
        return _shape(result).encode()

    def dispatch_sync(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
        """:meth:`dispatch` for callers without an event loop (tests)."""
        return asyncio.run(self.dispatch(*args, **kwargs))


def _shape(result: Any) -> ModResponse:
    if isinstance(result, ModResponse):
        return result
    if isinstance(result, tuple):
        if len(result) == 2:
            return ModResponse(int(result[0]), result[1])
        if len(result) == 3:
            return ModResponse(int(result[0]), result[2], dict(result[1] or {}))
        raise TypeError("a tuple reply is (status, body) or (status, headers, body)")
    return ModResponse(200, result)


@dataclass
class ModRoutes:
    """``ctx.routes`` — a mod's view of the registry, bound to its id."""

    registry: ModRouteRegistry
    mod_id: str

    def add(self, method: str, path: str, handler: Callable[..., Any]) -> RouteRegistration:
        return self.registry.add(self.mod_id, method, path, handler)

    def remove(self, method: str | None = None, path: str | None = None) -> int:
        return self.registry.remove(self.mod_id, method, path)

    def list(self) -> list[dict[str, Any]]:
        return self.registry.listing(self.mod_id)

    @property
    def base_path(self) -> str:
        """Where the App reaches these routes: ``/api/apps/floofycrew/mods/<id>/api/``."""
        return f"/api/apps/floofycrew/mods/{self.mod_id}/api/"
