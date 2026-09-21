"""custom-themes — the theme layer's backend (a ``python-hook`` part).

The FloofyCrew Loader imports this package inside the gateway and calls
:func:`activate` with the mod's context. It owns

* the **library** (:mod:`.library`): the user's themes under the mod's data dir,
  each a host theme pack plus the mod's unrestricted ``extra.css``;
* the **compiler** (:mod:`.cssc`): pack palette + scoped extra layer → one sheet;
* the **presets** (:mod:`.hostthemes`): the running host's own built-in themes,
  harvested from its stylesheet at request time;
* the **first-frame sheet**: ``<Loader app>/ui/boot/custom-themes/themes.css``,
  regenerated on every change, which the mod's boot hook links render-blocking so
  a cold client paints the installed custom theme (palette and extra layer) before
  the host hydrates;
* the routes the editor page (``ui/page.mjs``) and the runtime part
  (``spa/runtime.mjs``) call, all under the Loader's per-mod api root.

Installing a theme into the host goes through ``floofy_core.themes.direct_write_theme``
— the byte-equivalent of the host's own ``POST /api/themes/install`` (stage, validate
with the ported host validator, rename into place) — so what lands in
``<host home>/themes/<slug>/`` is a pack the host itself accepts and shows in
Settings → Display. Nothing here touches a payload file; the reset-guard patch and
the boot part are separate parts of the manifest applied by the Patcher.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from floofy_core.loaderapp import app_dir

from .cssc import CssRefused, preview_css, raise_specificity
from .hostthemes import harvest
from .library import Library, LibraryError, installed_host_themes, read_active_theme

__all__ = ["activate", "deactivate", "BOOT_SHEET_REL"]

#: Where the first-frame sheet lives under the Loader app's ``ui/`` (served unauthenticated, ``no-cache``).
BOOT_SHEET_REL = ("boot", "custom-themes", "themes.css")
BOOT_SHEET_URL = "/apps/floofycrew/ui/boot/custom-themes/themes.css"
_CSS_HEADERS = {"Content-Type": "text/css; charset=utf-8"}


def _boot_sheet_path(ctx) -> Path:
    return app_dir(Path(ctx.host.host_home)) / "ui" / Path(*BOOT_SHEET_REL)


def _write_boot_sheet(ctx, library: Library) -> dict[str, Any]:
    css, slugs = library.boot_css()
    path = _boot_sheet_path(ctx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(css, encoding="utf-8")
        written = True
    except OSError as exc:  # the Loader app dir may be read-only in odd installs: the runtime part still applies the sheet
        ctx.log.warning("custom-themes: cannot write the first-frame sheet %s: %s", path, exc)
        written = False
    info = {"path": str(path), "url": BOOT_SHEET_URL, "written": written, "bytes": len(css.encode("utf-8")), "themes": slugs}
    ctx.state["bootSheet"] = info
    return info


def _json_body(request) -> dict[str, Any]:
    try:
        body = request.json()
    except ValueError as exc:
        raise LibraryError(f"invalid JSON body: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise LibraryError("the request body must be a JSON object")
    return body


def _error(exc: LibraryError | CssRefused):
    status = getattr(exc, "status", 400)
    return status, {"ok": False, "error": str(exc)}


def activate(ctx):
    library = Library(Path(ctx.data_dir) / "library", Path(ctx.host.host_home))
    library.root.mkdir(parents=True, exist_ok=True)
    payload_root = ctx.host.payload_root

    def guarded(fn):
        def run(request):
            try:
                return fn(request)
            except (LibraryError, CssRefused) as exc:
                return _error(exc)

        run.__qualname__ = getattr(fn, "__qualname__", "route")
        return run

    # -- overview --------------------------------------------------------------------

    @guarded
    def status(request):
        return {
            "ok": True,
            "mod": ctx.mod_id,
            "version": ctx.version,
            "host": {"version": ctx.host.version, "edition": ctx.host.edition},
            "activeTheme": read_active_theme(library.host_home),
            "bootSheet": ctx.state.get("bootSheet"),
            "themesDir": str(Path(library.host_home) / "themes"),
        }

    @guarded
    def list_themes(request):
        _write_boot_sheet(ctx, library)  # cheap; keeps the first-frame sheet honest after a CLI install
        installed = installed_host_themes(library.host_home)
        in_library = set(library.slugs())
        return {
            "ok": True,
            "activeTheme": read_active_theme(library.host_home),
            "library": [library.summary(slug) for slug in library.slugs()],
            "installed": [
                {"slug": t["slug"], "name": t["name"], "emoji": t["emoji"], "level": t["level"], "kind": t["kind"], "inLibrary": t["slug"] in in_library}
                for t in installed
            ],
        }

    # -- presets ---------------------------------------------------------------------

    @guarded
    def presets(request):
        found = harvest(payload_root)
        rows = []
        for theme in found:
            row = theme.to_dict()
            row["swatch"] = {mode: {k: getattr(theme, mode).get(k, "") for k in ("--bg", "--text", "--accent", "--card")} for mode in ("dark", "light")}
            if "full" not in request.query:
                row.pop("dark", None)
                row.pop("light", None)
                row.pop("extraCss", None)
            rows.append(row)
        return {"ok": True, "presets": rows, "source": "the running host's stylesheet"}

    @guarded
    def preset(request):
        slug = request.params["slug"]
        for theme in harvest(payload_root):
            if theme.slug == slug:
                return {"ok": True, "preset": theme.to_dict()}
        raise LibraryError(f"the host has no built-in theme {slug!r}", 404)

    # -- library CRUD -------------------------------------------------------------------

    @guarded
    def get_theme(request):
        return {"ok": True, "theme": library.to_document(request.params["slug"])}

    @guarded
    def put_theme(request):
        body = _json_body(request)
        body["slug"] = request.params["slug"]
        body["manifest"] = {**(body.get("manifest") or {}), "slug": request.params["slug"]}
        saved = library.save_document(body)
        info = _write_boot_sheet(ctx, library)
        return {"ok": True, "theme": saved, "bootSheet": info}

    @guarded
    def delete_theme(request):
        slug = request.params["slug"]
        if not library.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        uninstalled = False
        if request.query.get("uninstall", ["0"])[0] in ("1", "true", "yes"):
            uninstalled = library.uninstall(slug)
        library.delete(slug)
        info = _write_boot_sheet(ctx, library)
        return {"ok": True, "deleted": slug, "uninstalled": uninstalled, "bootSheet": info}

    @guarded
    def create(request):
        body = _json_body(request)
        slug = str(body.get("slug") or "").strip()
        name = str(body.get("name") or "").strip() or slug.replace("-", " ").title()
        source = str(body.get("from") or "blank")
        if library.exists(slug):
            raise LibraryError(f"a theme {slug!r} already exists in the library", 409)
        if source.startswith("preset:"):
            wanted = source[len("preset:") :]
            match = next((t for t in harvest(payload_root) if t.slug == wanted), None)
            if match is None:
                raise LibraryError(f"the host has no built-in theme {wanted!r}", 404)
            document = {
                "slug": slug,
                "manifest": {"slug": slug, "name": name, "emoji": body.get("emoji") or match.emoji or "🎨"},
                "variables": {"dark": dict(match.dark), "light": dict(match.light)},
                "extraCss": match.extra_css if body.get("withDecor", True) else "",
                "overridesCss": "",
            }
            saved = library.save_document(document, based_on=source)
        elif source.startswith("installed:"):
            wanted = source[len("installed:") :]
            installed = next((t for t in installed_host_themes(library.host_home) if t["slug"] == wanted), None)
            if installed is None:
                raise LibraryError(f"the host has no installed custom theme {wanted!r}", 404)
            if installed["kind"] == "pack":
                saved = library.adopt_installed(Path(installed["path"]), slug)
                if name and name != saved["manifest"].get("name"):
                    saved["manifest"]["name"] = name
                    saved = library.save_document(saved, based_on=source)
            else:
                saved = library.save_document({"slug": slug, "manifest": {"slug": slug, "name": name or installed["name"], "emoji": installed["emoji"]}, "variables": installed["variables"], "extraCss": "", "overridesCss": ""}, based_on=source)
        elif source.startswith("library:"):
            saved = library.duplicate(source[len("library:") :], slug, name)
        else:
            document = {
                "slug": slug,
                "manifest": {"slug": slug, "name": name, "emoji": body.get("emoji") or "🎨"},
                "variables": {"dark": {"--bg": "#14161b", "--text": "#e6e6e6", "--accent": "#7aa2f7"}, "light": {"--bg": "#fafafa", "--text": "#1f2328", "--accent": "#2f6feb"}},
                "extraCss": "",
                "overridesCss": "",
            }
            saved = library.save_document(document, based_on="blank")
        info = _write_boot_sheet(ctx, library)
        return {"ok": True, "theme": saved, "bootSheet": info}

    @guarded
    def put_asset(request):
        slug, rel = request.params["slug"], request.params["kind"] + "/" + request.params["name"]
        if request.params["kind"] == "fonts":
            rel = "styles/fonts/" + request.params["name"]
        body = _json_body(request)
        if body.get("remove"):
            return {"ok": True, "theme": library.remove_asset(slug, rel)}
        encoded = body.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise LibraryError("asset uploads carry the file as base64 in 'data'")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise LibraryError("asset data is not base64") from exc
        saved = library.put_asset(slug, rel, data)
        return {"ok": True, "theme": saved}

    # -- host ----------------------------------------------------------------------------

    @guarded
    def install(request):
        slug = request.params["slug"]
        result = library.install(slug)
        info = _write_boot_sheet(ctx, library)
        ctx.log.info("custom-themes: installed %s (level %s) into the host", slug, result["level"])
        ctx.events.publish(f"mod.{ctx.mod_id}.installed", {"slug": slug, "level": result["level"]})
        return {"ok": True, "installed": result, "bootSheet": info}

    @guarded
    def uninstall(request):
        slug = request.params["slug"]
        removed = library.uninstall(slug)
        info = _write_boot_sheet(ctx, library)
        return {"ok": True, "uninstalled": removed, "bootSheet": info}

    # -- CSS --------------------------------------------------------------------------------

    @guarded
    def css_one(request):
        slug = request.params["slug"]
        if library.exists(slug):
            return 200, _CSS_HEADERS, library.compiled_css(slug)
        for theme in installed_host_themes(library.host_home):
            if theme["slug"] == slug:
                from floofy_core.themes import theme_css  # noqa: PLC0415 - the core is on sys.path inside the gateway

                return 200, _CSS_HEADERS, raise_specificity(slug, theme_css(slug, theme["variables"])) + "\n"
        return 404, _CSS_HEADERS, f"/* no custom theme {slug} */\n"

    @guarded
    def css_all(request):
        css, _slugs = library.boot_css()
        return 200, _CSS_HEADERS, css

    @guarded
    def preview(request):
        body = _json_body(request)
        slug = str(body.get("slug") or "preview")
        variables = body.get("variables") if isinstance(body.get("variables"), dict) else {}
        extra = body.get("extraCss") if isinstance(body.get("extraCss"), str) else ""
        mode = str(body.get("mode") or "dark")
        return 200, _CSS_HEADERS, preview_css(slug, variables, extra, mode)

    @guarded
    def refresh(request):
        return {"ok": True, "bootSheet": _write_boot_sheet(ctx, library)}

    # -- export / import -------------------------------------------------------------------

    @guarded
    def export_theme(request):
        document = library.export_document(request.params["slug"])
        return 200, {"Content-Type": "application/json; charset=utf-8", "Content-Disposition": f'attachment; filename="{request.params["slug"]}.floofy-theme.json"'}, json.dumps(document, indent=2, ensure_ascii=False)

    @guarded
    def import_theme(request):
        body = _json_body(request)
        slug = request.query.get("slug", [None])[0]
        overwrite = request.query.get("overwrite", ["0"])[0] in ("1", "true", "yes")
        saved = library.import_document(body, slug=slug, overwrite=overwrite)
        info = _write_boot_sheet(ctx, library)
        return {"ok": True, "theme": saved, "bootSheet": info}

    ctx.routes.add("GET", "status", status)
    ctx.routes.add("GET", "themes", list_themes)
    ctx.routes.add("POST", "themes", create)
    ctx.routes.add("GET", "themes/{slug}", get_theme)
    ctx.routes.add("PUT", "themes/{slug}", put_theme)
    ctx.routes.add("DELETE", "themes/{slug}", delete_theme)
    ctx.routes.add("POST", "themes/{slug}/install", install)
    ctx.routes.add("POST", "themes/{slug}/uninstall", uninstall)
    ctx.routes.add("GET", "themes/{slug}/export", export_theme)
    ctx.routes.add("POST", "assets/{slug}/{kind}/{name}", put_asset)
    ctx.routes.add("POST", "import", import_theme)
    ctx.routes.add("GET", "presets", presets)
    ctx.routes.add("GET", "presets/{slug}", preset)
    ctx.routes.add("GET", "css", css_all)
    ctx.routes.add("GET", "css/{slug}", css_one)
    ctx.routes.add("POST", "preview", preview)
    ctx.routes.add("POST", "refresh", refresh)

    ctx.state["routes"] = ctx.routes.list()
    ctx.state["library"] = library.slugs()
    ctx.state["bootSheetUrl"] = BOOT_SHEET_URL
    info = _write_boot_sheet(ctx, library)
    ctx.log.info("custom-themes active on host %s: %d theme(s) in the library, first-frame sheet covers %s", ctx.host.version, len(ctx.state["library"]), ", ".join(info["themes"]) or "nothing")


def deactivate(ctx):
    """Remove the first-frame sheet: with the mod off, no boot link should find CSS to apply."""
    try:
        path = _boot_sheet_path(ctx)
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    ctx.log.info("custom-themes deactivated")
