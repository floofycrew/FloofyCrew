"""The Loader's gateway routes (Requirement 3.7, 3.8, 4.1) — mounted under ``/api/apps/floofycrew/``.

The host registers what ``backend.hooks.routes`` returns: a ``list`` of
``kiro_crew.apps.route_registry.AppRoute(method, path, handler)`` with
``async def handler(request, ctx)`` (``route_registry.py`` L29–L34, L125–L195
``register_app_routes``) and dispatches them from its catch-all
``/api/apps/{app_name}/{path:.*}`` (L117–L123), behind the dashboard's
token/cookie auth. A ``{param}`` matches one segment (``([^/]+)``, L47–L67), so
the SPA file route is declared once per depth (1–4 segments below the mod id).

| Method | Path (under ``/api/apps/floofycrew``) | Purpose |
|---|---|---|
| GET | ``/state`` | the :class:`LoaderState` JSON plus ``api_version``, ``unofficial: true`` |
| GET | ``/health`` | Loader liveness |
| POST | ``/reload`` | re-run the boot sequence (apply ``pending/`` now, pick up ``enabled.json``) |
| POST | ``/mods/{id}/fault`` | body ``{message, stack, source: "spa"}``: the SPA host reports a failed module; the mod is disabled with ``Error`` |
| GET | ``/spa/{id}/{file...}`` | an enabled mod's ``spa`` part files from ``<data home>/spa/<id>/`` (``Cache-Control: no-cache``, typed by extension, path-escape safe) |
| POST | ``/cli`` | body ``{"argv": [...]}``: run one **allowlisted** ``floofy`` command in-process (the manager UI's actions — task 6.7) and reload the Loader when it mutated the mod set |
| GET | ``/registry`` | the registry cache summary and the update available for each installed mod |

The manager UI never gets more than the CLI offers, and less where a question
needs a human at a keyboard: ``init`` (the consent acknowledgement) and any
governance-altering per-file confirmation stay CLI-only — :data:`CLI_ALLOWLIST`
names what the UI may run, and the in-process run is non-interactive, so a typed
confirmation is refused rather than assumed.

The pure responders (:func:`state_response` …) return ``(status, headers, body)``
so they are testable without aiohttp; :func:`build_routes` adapts them for the
host and imports ``aiohttp.web`` only inside the gateway.
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable

__all__ = ["CLI_ALLOWLIST", "ROUTE_TABLE", "SPA_MAX_DEPTH", "build_routes", "cli_response", "fault_response", "health_response", "registry_response", "spa_response", "state_response"]

SPA_MAX_DEPTH = 4
Responder = tuple[int, dict[str, str], bytes]

#: The ``floofy`` sub-commands the manager UI may run in-process (Requirement 7.3, design "Manager (CLI + UI)").
#: ``init`` (the consent acknowledgement), ``install`` of arbitrary sources and anything needing a typed
#: governance confirmation stay CLI-only.
CLI_ALLOWLIST: frozenset[str] = frozenset({"enable", "disable", "uninstall", "update", "yeet", "apply", "status", "doctor", "profile", "search", "audit", "which", "vanilla"})
#: Commands after which the Loader reloads (the mod set or its flags changed).
CLI_MUTATING: frozenset[str] = frozenset({"enable", "disable", "uninstall", "update", "yeet", "apply", "profile", "vanilla"})

#: Declared for the manifest test and the manager UI; ``build_routes`` produces exactly these.
ROUTE_TABLE: tuple[tuple[str, str], ...] = (
    ("GET", "/state"),
    ("GET", "/health"),
    ("POST", "/reload"),
    ("POST", "/mods/{id}/fault"),
    ("POST", "/cli"),
    ("GET", "/registry"),
    *(("GET", "/spa/{id}/" + "/".join(f"{{p{i}}}" for i in range(1, depth + 1))) for depth in range(1, SPA_MAX_DEPTH + 1)),
)

_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
}


def _json(status: int, payload: Any) -> Responder:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, body


def content_type_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


# --- pure responders ---------------------------------------------------------------------------------


def state_response(runtime: Any) -> Responder:
    return _json(200, runtime.state_dict())


def health_response(runtime: Any) -> Responder:
    state = runtime.result.state if runtime.result is not None else None
    return _json(
        200,
        {
            "ok": True,
            "loader": state.loader if state is not None else "inert",
            "active": state.active if state is not None else [],
            "bootedAt": state.booted_at if state is not None else None,
            "unofficial": True,
            "api_version": runtime.api_version(),
            "loaderVersion": runtime.loader_version(),
        },
    )


def reload_response(runtime: Any) -> Responder:
    try:
        runtime.reload()
    except Exception as exc:  # noqa: BLE001 - the route reports, the host continues
        return _json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return _json(200, {"ok": True, **runtime.state_dict()})


def fault_response(runtime: Any, mod_id: str, body: Any) -> Responder:
    """``POST /mods/{id}/fault`` — the SPA host's error boundary reports a failed module (Requirement 4.1)."""
    if not isinstance(body, dict):
        return _json(400, {"ok": False, "error": "body must be a JSON object {message, stack, source}"})
    message = str(body.get("message") or "SPA module failed")
    stack = str(body.get("stack") or "")
    source = str(body.get("source") or "spa")
    if runtime.result is None or mod_id not in runtime.result.state.mods:
        return _json(404, {"ok": False, "error": f"unknown mod {mod_id!r}"})
    faulted = runtime.fault_mod(mod_id, message, source=source, traceback_text=stack)
    mod_state = runtime.result.state.mods[mod_id]
    return _json(200, {"ok": True, "faulted": faulted, "mod": mod_state.to_dict()})


def cli_response(runtime: Any, body: Any, *, reload: bool = True) -> Responder:
    """``POST /cli`` — run one allowlisted ``floofy`` command in-process; reload the Loader after a mutation."""
    if not isinstance(body, dict) or not isinstance(body.get("argv"), list) or not all(isinstance(a, str) for a in body["argv"]):
        return _json(400, {"ok": False, "error": "body must be {\"argv\": [\"<command>\", ...]}"})
    argv = [str(a) for a in body["argv"]]
    command = next((a for a in argv if not a.startswith("-")), None)
    if command not in CLI_ALLOWLIST:
        return _json(403, {"ok": False, "error": f"`floofy {command}` is not available from the manager UI (allowed: {', '.join(sorted(CLI_ALLOWLIST))}); init and governance-altering confirmations stay CLI-only"})
    if runtime.facts is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    from floofy_core.cli.main import run as cli_run  # noqa: PLC0415

    mutated: list[str] = []
    full = ["--json", *argv] if "--json" not in argv else argv
    if runtime.facts.package_dir is not None and "--root" not in full:
        full = ["--root", str(runtime.facts.package_dir.parent), *full]
    result = cli_run(full, home=runtime.facts.host_home, in_gateway=True, actor="ui", allow=CLI_ALLOWLIST, on_mutation=mutated.append, assume_yes=True, live_state=runtime.state_dict)
    reloaded = False
    if reload and (mutated or command in CLI_MUTATING) and result.exit == 0:
        try:
            runtime.reload()
            reloaded = True
        except Exception as exc:  # noqa: BLE001 - the command ran; the reload failure is reported
            result.stderr += f"\nreload failed: {type(exc).__name__}: {exc}"
    payload = result.to_dict()
    payload.update({"ok": result.exit == 0, "command": command, "reloaded": reloaded})
    return _json(200, payload)


def registry_response(runtime: Any) -> Responder:
    """``GET /registry`` — the registry cache summary and update availability per installed mod (Requirement 7.3)."""
    if runtime.paths is None or runtime.facts is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    from floofy_core.modstore import installed_mods, read_source  # noqa: PLC0415
    from floofy_core.registry import IndexCache  # noqa: PLC0415
    from floofy_core.semver import InvalidVersion, Version  # noqa: PLC0415

    cache = IndexCache.load(runtime.paths)
    facts = runtime.facts
    updates: dict[str, Any] = {}
    for mod in installed_mods(runtime.paths, include_broken=False):
        key = read_source(mod.dir).get("registryKey") or mod.id
        entry, picked, why = cache.pick(str(key), base_version=facts.base_version, edition=facts.edition, host_version=facts.version)
        try:
            newer = picked is not None and Version.parse(picked.version) > Version.parse(mod.version)
        except InvalidVersion:
            newer = False
        updates[mod.id] = {"installed": mod.version, "candidate": picked.version if picked else None, "verdict": picked.verdict_for(facts.version) if picked else None, "update": newer, "inRegistry": entry is not None, "why": why}
    return _json(200, {"ok": True, "cache": cache.to_dict(), "updates": updates})


def spa_response(spa_root: Path, mod_id: str, segments: list[str]) -> Responder:
    """Serve ``<spa_root>/<id>/<segments...>`` for an enabled mod; refuses escapes and hidden files."""
    if not mod_id or not segments or any(not s or s in (".", "..") or s.startswith(".") or "\\" in s for s in [mod_id, *segments]):
        return 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"
    base = (Path(spa_root) / mod_id).resolve()
    if not base.is_dir() or not base.is_relative_to(Path(spa_root).resolve()):
        return 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"
    target = base.joinpath(*segments)
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        return 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"
    return 200, {"Content-Type": content_type_for(resolved.name), "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}, resolved.read_bytes()


# --- the aiohttp adapter -----------------------------------------------------------------------------


def build_routes(runtime: Any) -> list:
    """``backend.hooks.routes`` payload: the host's ``AppRoute`` objects wrapping the pure responders."""
    from aiohttp import web  # noqa: PLC0415 - only inside the gateway
    from kiro_crew.apps.route_registry import AppRoute  # type: ignore[import-not-found]  # noqa: PLC0415

    def respond(result: Responder) -> Any:
        status, headers, body = result
        return web.Response(status=status, headers=headers, body=body)

    def make(fn: Callable[..., Responder]) -> Callable[..., Any]:
        async def handler(request: Any, ctx: Any) -> Any:
            return respond(fn(request))

        handler.__name__ = getattr(fn, "__name__", "handler")
        return handler

    async def fault(request: Any, ctx: Any) -> Any:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - a bad body is a 400, not a 500
            body = None
        return respond(fault_response(runtime, request.match_info.get("id", ""), body))

    async def spa(request: Any, ctx: Any) -> Any:
        if runtime.paths is None:  # a request before on_startup ran
            return respond((404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"))
        keys = sorted((k for k in request.match_info if re.fullmatch(r"p\d+", k)), key=lambda k: int(k[1:]))
        segments = [request.match_info[key] for key in keys]
        return respond(spa_response(runtime.paths.spa, request.match_info.get("id", ""), segments))

    async def cli(request: Any, ctx: Any) -> Any:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - a bad body is a 400, not a 500
            body = None
        # the CLI does file work (Patcher, downloads): off the event loop; the reload runs on it afterwards
        import asyncio  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: cli_response(runtime, body, reload=False))
        status, headers, raw = result
        if status == 200:
            document = json.loads(raw.decode("utf-8"))
            argv = body.get("argv", []) if isinstance(body, dict) else []
            command = document.get("command")
            if document.get("ok") and (command in CLI_MUTATING) and "--check" not in argv:
                try:
                    runtime.reload()
                    document["reloaded"] = True
                except Exception as exc:  # noqa: BLE001
                    document["stderr"] = f"{document.get('stderr', '')}\nreload failed: {type(exc).__name__}: {exc}"
            return respond(_json(200, document))
        return respond(result)

    routes = [
        AppRoute("GET", "/state", make(lambda request: state_response(runtime))),
        AppRoute("GET", "/health", make(lambda request: health_response(runtime))),
        AppRoute("POST", "/reload", make(lambda request: reload_response(runtime))),
        AppRoute("POST", "/mods/{id}/fault", fault),
        AppRoute("POST", "/cli", cli),
        AppRoute("GET", "/registry", make(lambda request: registry_response(runtime))),
    ]
    for method, path in ROUTE_TABLE:
        if path.startswith("/spa/"):
            routes.append(AppRoute(method, path, spa))
    return routes
