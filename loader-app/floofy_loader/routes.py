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
| POST | ``/reload`` | re-run the boot sequence (apply ``pending/`` now, pick up ``enabled.json``) — the manager App's "apply now" |
| POST | ``/host/restart`` | restart the KiroCrew gateway through ``kirocrew restart`` (detached), behind the App's ``yes-no`` confirmation — the red "Restart KiroCrew" button; see :mod:`floofy_loader.hostrestart` |
| POST | ``/mods/{id}/fault`` | body ``{message, stack, source: "spa"}``: the SPA host reports a failed module; the mod is disabled with ``Error`` |
| GET | ``/spa/{id}/{file...}`` | an enabled mod's ``spa`` part files from ``<data home>/spa/<id>/`` (``Cache-Control: no-cache``, typed by extension, path-escape safe) |
| POST | ``/cli`` | body ``{"argv": [...]}``: run one **read-only** ``floofy`` command in-process (``status``, ``doctor``, ``search``, ``info``, ``list``, ``audit``, ``which``) |
| GET | ``/registry`` | the registry cache summary, every source's trust settings and last-refresh verdict, the matrix row for this host, the update available for each installed mod (graded like the CLI: ``best_version`` against the matrix) and each installed mod's source tier (``tiers``, Requirement 8.11) |
| GET | ``/banner`` | the FloofyCrew banner cells (``floofy_core/cli/banner.json``) for the App's About card (Requirement 15.5 mirrored) |
| GET | ``/ui/mods/{id}/{file...}`` | an **active** mod's ``ui`` part files (Requirement 16.4), read from the installed mod directory, only files the manifest lists (``text/javascript`` for the entry, ``no-cache``, path-escape safe); what the App ``import()``s same-origin |
| GET / PUT | ``/mods/{id}/config`` | the mod's ``.floofy/config.json`` (``floofy.mod(id).config.get/set``): ``PUT {patch}`` merges key by key, ``null`` deletes, 64 KiB cap |
| * | ``/mods/{id}/api/{path...}`` | the mod's own backend routes (``ctx.routes.add``, :mod:`floofy_loader.modroutes`), one catch-all per method and depth |
| * | the manager App's typed routes | one route per CLI action — ``/mods/install``, ``/mods/{id}/enable`` …, ``/registries``, ``/profiles``, ``/doctor``, ``/audit``, ``/consent`` — each running the same handler as the CLI with ``actor: app`` and the 409 confirmation protocol; see :mod:`floofy_loader.app_routes` |

Every mutation the manager App performs goes through :mod:`floofy_loader.app_routes`
(Requirement 16.2): a typed body, the same ``floofy`` handler, the same audit
row, and every confirmation the CLI would ask — the one-time consent, a typed
governance-altering path, the unlisted-source line — answered by the request or
returned as a ``409`` question. ``POST /cli`` keeps only the read-only commands:
:data:`CLI_ALLOWLIST` names them, and a mutating command is refused with the
route to use instead.

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

from .app_routes import route_table as app_route_table
from .modroutes import METHODS as MOD_API_METHODS, MOD_API_MAX_DEPTH

__all__ = ["CLI_ALLOWLIST", "CONFIG_MAX_BYTES", "ROUTE_TABLE", "SPA_MAX_DEPTH", "banner_response", "build_routes", "cli_response", "config_response", "fault_response", "health_response", "registry_response", "spa_response", "state_response", "ui_file_response"]

SPA_MAX_DEPTH = 4
#: A mod's ``.floofy/config.json`` may not grow past this when written through the App (Requirement 16.4).
CONFIG_MAX_BYTES = 64 * 1024
Responder = tuple[int, dict[str, str], bytes]

#: The ``floofy`` sub-commands ``POST /cli`` may run in-process: read-only ones. Every action that changes anything has a
#: typed route in :mod:`floofy_loader.app_routes` (Requirement 16.2) with the confirmation protocol; ``init``, ``install``,
#: ``enable``… are refused here with a pointer to it.
CLI_ALLOWLIST: frozenset[str] = frozenset({"status", "doctor", "search", "info", "list", "audit", "which"})
#: Where a refused command lives now (for the error message).
CLI_MOVED: dict[str, str] = {
    "init": "POST /consent (the one-time warning; the Loader app and the trigger stay `floofy init`)",
    "install": "POST /mods/install",
    "enable": "POST /mods/{id}/enable",
    "disable": "POST /mods/{id}/disable",
    "uninstall": "POST /mods/{id}/uninstall",
    "update": "POST /mods/{id}/update, /mods/update-check, /mods/update-all",
    "yeet": "POST /mods/{id}/yeet, /yeet, /restore; GET /quarantine",
    "registry": "GET/POST /registries, DELETE /registries/{key}, POST /registries/{key}/refresh|trust, /registries/refresh|defaults",
    "profile": "GET/POST /profiles, POST /profiles/{name}/use|export, /profiles/import",
    "vanilla": "POST /vanilla",
    "apply": "POST /reload (apply now); `floofy apply` at a terminal",
    "self-update": "POST /self-update",
}

#: Declared for the manifest test and the manager UI; ``build_routes`` produces exactly these.
ROUTE_TABLE: tuple[tuple[str, str], ...] = (
    ("GET", "/state"),
    ("GET", "/health"),
    ("POST", "/reload"),
    ("POST", "/host/restart"),
    ("POST", "/mods/{id}/fault"),
    ("POST", "/cli"),
    ("GET", "/registry"),
    ("GET", "/banner"),
    ("GET", "/mods/{id}/config"),
    ("PUT", "/mods/{id}/config"),
    *(("GET", "/spa/{id}/" + "/".join(f"{{p{i}}}" for i in range(1, depth + 1))) for depth in range(1, SPA_MAX_DEPTH + 1)),
    *(("GET", "/ui/mods/{id}/" + "/".join(f"{{p{i}}}" for i in range(1, depth + 1))) for depth in range(1, SPA_MAX_DEPTH + 1)),
    *((method, "/mods/{id}/api/" + "/".join(f"{{p{i}}}" for i in range(1, depth + 1))) for method in MOD_API_METHODS for depth in range(1, MOD_API_MAX_DEPTH + 1)),
    *app_route_table(),
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
    """``POST /cli`` — run one read-only ``floofy`` command in-process (``reload`` is kept for the signature; nothing here mutates)."""
    if not isinstance(body, dict) or not isinstance(body.get("argv"), list) or not all(isinstance(a, str) for a in body["argv"]):
        return _json(400, {"ok": False, "error": "body must be {\"argv\": [\"<command>\", ...]}"})
    argv = [str(a) for a in body["argv"]]
    command = next((a for a in argv if not a.startswith("-")), None)
    if command not in CLI_ALLOWLIST:
        moved = CLI_MOVED.get(str(command))
        hint = f"; use {moved}" if moved else ""
        return _json(403, {"ok": False, "error": f"`floofy {command}` is not available through /cli (read-only commands only: {', '.join(sorted(CLI_ALLOWLIST))}){hint} — every mutation has its own route with the confirmation protocol (Requirement 16.2, 16.3)", "route": moved})
    if runtime.facts is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    from floofy_core.cli.main import run as cli_run  # noqa: PLC0415

    full = ["--json", *argv] if "--json" not in argv else argv
    if runtime.facts.package_dir is not None and "--root" not in full:
        full = ["--root", str(runtime.facts.package_dir.parent), *full]
    result = cli_run(full, home=runtime.facts.host_home, in_gateway=True, actor="ui", allow=CLI_ALLOWLIST, live_state=runtime.state_dict)
    payload = result.to_dict()
    payload.update({"ok": result.exit == 0, "command": command, "reloaded": False})
    return _json(200, payload)


def registry_response(runtime: Any) -> Responder:
    """``GET /registry`` — the registry cache summary, per-source verdicts and the update available per installed mod (Requirement 7.3, 9.2).

    ``sources[]`` is what ``floofy registry list`` shows: every configured source
    with its trust settings and the ``meta.json`` verdict of its last refresh
    (signature status, usable, refusal). ``updates`` grades the candidates the
    way the CLI does — :meth:`floofy_core.registry.IndexCache.best_version`
    against the cached matrix row for this host's edition × channel × version.
    ``selfUpdate`` is the FloofyCrew release notice (Requirement 7.7) from the
    CLI's cached daily check — the App's Updates card shows it; the Loader never
    contacts the release endpoint itself.
    """
    if runtime.paths is None or runtime.facts is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    import json as _json_module  # noqa: PLC0415

    from floofy_core.compat import CompatCache  # noqa: PLC0415
    from floofy_core.modstore import installed_mods, read_source  # noqa: PLC0415
    from floofy_core.registry import IndexCache  # noqa: PLC0415
    from floofy_core.registry_sources import SourceStore  # noqa: PLC0415
    from floofy_core.semver import InvalidVersion, Version  # noqa: PLC0415
    from floofy_core.tier import tier_for_candidate, tier_of  # noqa: PLC0415

    paths = runtime.paths
    cache = IndexCache.load(paths)
    compat = CompatCache.load(paths.compat_cache)
    facts = runtime.facts
    row = compat.row_for(facts.edition, facts.channel, facts.version)
    updates: dict[str, Any] = {}
    tiers: dict[str, str] = {}
    for mod in installed_mods(paths, include_broken=False):
        source = read_source(mod.dir)
        key = source.get("registryKey") or mod.id
        best = cache.best_version(str(key), base_version=facts.base_version, edition=facts.edition, host_version=facts.version, channel=facts.channel, compat=compat)
        picked = best.version
        try:
            newer = picked is not None and Version.parse(picked.version) > Version.parse(mod.version)
        except InvalidVersion:
            newer = False
        tiers[mod.id] = tier_of(source, mod_id=mod.id, version=mod.version, compat_row=row)
        updates[mod.id] = {
            "installed": mod.version,
            "candidate": picked.version if picked else None,
            "verdict": best.verdict,
            "update": newer,
            "inRegistry": best.entry is not None,
            "why": best.why,
            "installedVerdict": row.verdict(mod.id, mod.version) if row is not None else None,
            "tier": tiers[mod.id],
            "candidateTier": tier_for_candidate(best.verdict) if picked is not None else None,
            # Requirement 16.6: the candidate's release notes (the record's changelog URL, else the mod's repository)
            "changelog": (picked.changelog if picked is not None else None) or (best.entry.repo if best.entry is not None else None),
            "repo": best.entry.repo if best.entry is not None else None,
            "key": str(key),
        }
    sources: list[dict[str, Any]] = []
    try:
        store = SourceStore.load(paths)
    except Exception as exc:  # noqa: BLE001 - an unreadable registries.json is reported, not raised
        store = None
        sources.append({"error": f"registries.json unreadable: {exc}"})
    for source in (store.sources if store is not None else []):
        try:
            meta = _json_module.loads((paths.cache_sources / source.key / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None
        sources.append({**source.to_dict(), "cache": meta})
    return _json(200, {"ok": True, "cache": cache.to_dict(), "compat": compat.to_dict(row), "sources": sources, "updates": updates, "tiers": tiers, "selfUpdate": runtime.self_update_summary()})


def banner_response() -> Responder:
    """``GET /banner`` — the banner cells (``{width, height, rows: [[char, [r, g, b]], …]}``) for the App's About card."""
    from floofy_core.cli.banner import load  # noqa: PLC0415

    banner = load()
    return _json(200, {"name": banner.name, "width": banner.width, "height": banner.height, "rows": [[[ch, list(rgb)] for ch, rgb in row] for row in banner.rows]})


def _not_found() -> Responder:
    return 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, b"not found"


def ui_file_response(runtime: Any, mod_id: str, segments: list[str]) -> Responder:
    """``GET /ui/mods/{id}/<path>`` — a manifest-listed file of an ACTIVE mod with a ``ui`` part, from the installed mod directory.

    Same-origin, behind the dashboard auth, ``no-cache``: this is what the App
    ``import()``s (spike 1.4: a same-origin dynamic import is admitted by the
    served CSP). Only files the manifest's ``files[]`` names are served — the
    manifest, dot-directories (``.floofy/`` runtime state) and anything else are
    404 — and only while the mod is active, so a disabled or faulted mod's page
    cannot be reached.
    """
    if runtime.result is None or runtime.paths is None or not mod_id or not segments or any(not s or s in (".", "..") or s.startswith(".") or "\\" in s for s in [mod_id, *segments]):
        return _not_found()
    mod_state = runtime.result.state.mods.get(mod_id)
    if mod_state is None or not mod_state.active or not any(p.kind == "ui" for p in mod_state.parts):
        return _not_found()
    mod_dir = Path(mod_state.mod_dir) if mod_state.mod_dir else runtime.paths.mod_dir(mod_id)
    rel = "/".join(segments)
    try:
        manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _not_found()
    listed = {str(f.get("path")) for f in (manifest.get("files") or []) if isinstance(f, dict) and isinstance(f.get("path"), str)}
    if rel not in listed:
        return _not_found()
    base = mod_dir.resolve()
    try:
        resolved = (mod_dir / rel).resolve(strict=True)
    except OSError:
        return _not_found()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return _not_found()
    return 200, {"Content-Type": content_type_for(resolved.name), "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}, resolved.read_bytes()


def config_response(runtime: Any, mod_id: str, method: str, body: Any = None) -> Responder:
    """``GET`` / ``PUT /mods/{id}/config`` — the mod's ``.floofy/config.json`` for ``floofy.mod(id).config`` (Requirement 16.4).

    ``PUT {"patch": {…}}`` (or the object itself) merges key by key — ``null``
    deletes a key — and refuses a document that would exceed
    :data:`CONFIG_MAX_BYTES` serialised (413). The store is
    :class:`floofy_loader.storage.ModConfig`, the same one the mod's Python side
    reads as ``ctx.config``; an installed mod's config may be edited whether or
    not it is active (its page needs it active).
    """
    if runtime.paths is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", mod_id or ""):
        return _json(404, {"ok": False, "error": f"not a mod id: {mod_id!r}"})
    mod_dir = runtime.paths.mod_dir(mod_id)
    if not (mod_dir / "floofy.json").is_file():
        return _json(404, {"ok": False, "error": f"mod {mod_id!r} is not installed"})
    from .storage import ModConfig  # noqa: PLC0415

    payload_root = runtime.facts.payload_root if runtime.facts is not None else None
    config = ModConfig(runtime.paths.data_home, mod_id, payload_root=payload_root)
    if method == "GET":
        return _json(200, {"ok": True, "mod": mod_id, "config": config.to_dict()})
    if method != "PUT":
        return _json(405, {"ok": False, "error": "GET or PUT"})
    patch = body.get("patch") if isinstance(body, dict) and isinstance(body.get("patch"), dict) else body
    if not isinstance(patch, dict):
        return _json(400, {"ok": False, "error": "the body must be a JSON object (or {\"patch\": {…}}) of keys to set; null deletes a key"})
    merged = config.to_dict()
    for key, value in patch.items():
        if not isinstance(key, str) or not key:
            return _json(400, {"ok": False, "error": "config keys must be non-empty strings"})
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    encoded = json.dumps(merged, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) > CONFIG_MAX_BYTES:
        return _json(413, {"ok": False, "error": f"the mod's config would exceed {CONFIG_MAX_BYTES} bytes serialised"})
    config.clear()
    if merged:
        config.update(merged)
    return _json(200, {"ok": True, "mod": mod_id, "config": config.to_dict()})


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
        # the CLI does file work (payload discovery, the registry cache): off the event loop
        import asyncio  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        return respond(await loop.run_in_executor(None, lambda: cli_response(runtime, body, reload=False)))

    def segments_of(request: Any) -> list[str]:
        keys = sorted((k for k in request.match_info if re.fullmatch(r"p\d+", k)), key=lambda k: int(k[1:]))
        return [request.match_info[key] for key in keys]

    async def ui_file(request: Any, ctx: Any) -> Any:
        return respond(ui_file_response(runtime, request.match_info.get("id", ""), segments_of(request)))

    async def config(request: Any, ctx: Any) -> Any:
        body = None
        if request.method == "PUT":
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return respond(_json(400, {"ok": False, "error": "the body must be JSON"}))
        return respond(config_response(runtime, request.match_info.get("id", ""), request.method, body))

    async def mod_api(request: Any, ctx: Any) -> Any:
        mod_id = request.match_info.get("id", "")
        if runtime.result is None or mod_id not in runtime.result.state.mods or not runtime.result.state.mods[mod_id].active:
            return respond(_json(404, {"ok": False, "error": f"mod {mod_id!r} is not active"}))
        query: dict[str, list[str]] = {}
        for key, value in request.query.items():
            query.setdefault(key, []).append(value)
        raw = await request.read() if request.can_read_body else b""
        status, headers, payload = await runtime.mod_routes.dispatch(mod_id, request.method, "/".join(segments_of(request)), query=query, headers={k: v for k, v in request.headers.items()}, body=raw)
        return respond((status, headers, payload))

    async def reload(request: Any, ctx: Any) -> Any:
        # the boot stays on the loop (python-hook activation needs it); the Patcher pass runs in a worker thread
        try:
            await runtime.reload_async()
        except Exception as exc:  # noqa: BLE001 - the route reports, the host continues
            return respond(_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return respond(_json(200, {"ok": True, **runtime.state_dict()}))

    async def host_restart(request: Any, ctx: Any) -> Any:
        from .hostrestart import restart_response  # noqa: PLC0415

        body: Any = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 - a bad body is a 400, not a 500
                return respond(_json(400, {"ok": False, "error": "the body must be JSON"}))
        return respond(restart_response(runtime, body))

    routes = [
        AppRoute("GET", "/state", make(lambda request: state_response(runtime))),
        AppRoute("GET", "/health", make(lambda request: health_response(runtime))),
        AppRoute("POST", "/reload", reload),
        AppRoute("POST", "/host/restart", host_restart),
        AppRoute("POST", "/mods/{id}/fault", fault),
        AppRoute("POST", "/cli", cli),
        AppRoute("GET", "/registry", make(lambda request: registry_response(runtime))),
        AppRoute("GET", "/banner", make(lambda request: banner_response())),
    ]
    routes.append(AppRoute("GET", "/mods/{id}/config", config))
    routes.append(AppRoute("PUT", "/mods/{id}/config", config))
    for method, path in ROUTE_TABLE:
        if path.startswith("/spa/"):
            routes.append(AppRoute(method, path, spa))
        elif path.startswith("/ui/mods/"):
            routes.append(AppRoute(method, path, ui_file))
        elif "/api/" in path and path.startswith("/mods/"):
            routes.append(AppRoute(method, path, mod_api))
    from .app_routes import build_app_routes  # noqa: PLC0415

    routes.extend(build_app_routes(runtime))
    return routes
