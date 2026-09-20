"""The ``index.html`` descriptor for the SPA host: loader tag + boot activation (Requirement 4.3; spike 1.3).

One generated ``kind: patch`` descriptor per apply, target ``kiro_crew/static/dist/index.html``:

* the **loader tag** (design "Spike outcomes" 1.3) — ``<script type="module"
  src="/apps/floofycrew/ui/host.mjs" id="floofy-host">`` inserted before the
  host's ``main-*.js`` module tag, present whenever any active mod has a ``spa``
  part (runtime parts need the always-on host; boot parts get their
  drift-guard/report plumbing from it too);
* the **boot script** — the template function of ``spa-host/src/boot.mjs``
  rendered once with every boot mod's configuration in load order, inserted
  before the first ``<script`` of the shell (``occurrence: first``) so it runs
  before anything can paint; the host config's ``dashboard.theme_color`` /
  ``theme_mode`` are captured at patch time as the cold-client fallback;
* one **baked stylesheet** per boot mod (``<style id="floofy-boot-css-<id>">``)
  appended just before ``</head>``, after the host's stylesheets.

Favicon and logo files a boot mod declares are copied under the Loader app's
``ui/boot/<mod id>/`` (served by the unauthenticated app-UI route, so the cold
``?token=`` page gets them too) as *ui assets* of the descriptor — the Patcher
records them in the deployment manifest like the import-map copies.

The Loader (``floofy_loader.pending.plan_boot``) and the CLI call
:func:`build_boot_descriptor`; the Patcher applies it like any other descriptor
(revert-then-patch, markers, manifest, restore).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .governance import LOADER_APP_NAME
from .patches import PatchDescriptor, UiAsset

__all__ = [
    "BOOT_CSS_ID_PREFIX",
    "BOOT_SCRIPT_ID",
    "BootMod",
    "HOST_MODULE_URL",
    "LOADER_TAG",
    "LOADER_TAG_ID",
    "MAX_BOOT_HOOK_BYTES",
    "MAX_BOOT_CSS_BYTES",
    "boot_mods_from_manifests",
    "boot_template",
    "build_boot_descriptor",
    "read_theme_defaults",
    "render_boot_script",
]

LOADER_TAG_ID = "floofy-host"
HOST_MODULE_URL = f"/apps/{LOADER_APP_NAME}/ui/host.mjs"
LOADER_TAG = f'<script type="module" src="{HOST_MODULE_URL}" id="{LOADER_TAG_ID}"></script>\n  '
BOOT_SCRIPT_ID = "floofy-boot"
BOOT_CSS_ID_PREFIX = "floofy-boot-css-"
#: Where boot assets live under the Loader app's ``ui/`` (``ui/boot/<mod id>/<file>``).
BOOT_ASSETS_DIR = "boot"
MAX_BOOT_HOOK_BYTES = 32 * 1024
MAX_BOOT_CSS_BYTES = 256 * 1024

_TEMPLATE_START = "/* floofy-boot-template-start */"
_TEMPLATE_END = "/* floofy-boot-template-end */"
_MAIN_TAG_FINGERPRINT = r'<script type="module"[^>]*\bsrc="/assets/main-[^"]+\.js"'
_MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class BootScriptError(ValueError):
    """A boot part cannot be rendered (unsafe content, missing file, oversized)."""


@dataclass(frozen=True)
class BootMod:
    """One active mod's ``spa`` part with ``activation: boot``."""

    mod_id: str
    mod_dir: Path
    part: dict[str, Any]
    part_index: int = 0

    @property
    def boot(self) -> dict[str, Any]:
        boot = self.part.get("boot")
        return boot if isinstance(boot, dict) else {}

    @property
    def label(self) -> str:
        return f"{self.mod_id}#{self.part_index}"


def boot_mods_from_manifests(mods: list[tuple[str, Path, dict[str, Any]]]) -> list[BootMod]:
    """Every ``spa`` part with ``activation: boot`` of the given ``(id, dir, manifest)`` mods, in order."""
    found: list[BootMod] = []
    for mod_id, mod_dir, manifest in mods:
        for index, part in enumerate(manifest.get("parts") or []):
            if isinstance(part, dict) and part.get("kind") == "spa" and part.get("activation") == "boot":
                found.append(BootMod(mod_id, Path(mod_dir), part, index))
    return found


def has_spa_parts(manifest: dict[str, Any]) -> bool:
    return any(isinstance(p, dict) and p.get("kind") == "spa" for p in manifest.get("parts") or [])


def read_theme_defaults(host_home: Path) -> dict[str, str]:
    """``dashboard.theme_color`` / ``theme_mode`` from the host's ``config.json`` — the cold-client fallback."""
    defaults = {"color": "kiro", "mode": "system"}
    try:
        document = json.loads((Path(host_home) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    dashboard = document.get("dashboard") if isinstance(document, dict) else None
    if not isinstance(dashboard, dict):
        return defaults
    color = dashboard.get("theme_color")
    mode = dashboard.get("theme_mode")
    if isinstance(color, str) and re.fullmatch(r"[a-z0-9-]{1,72}", color):
        defaults["color"] = color
    if mode in ("dark", "light", "system"):
        defaults["mode"] = mode
    return defaults


def _template_path() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "spa-host" / "src" / "boot.mjs", here.parents[1] / "ui" / "boot.mjs"):
        if candidate.is_file():
            return candidate
    return None


def boot_template(path: Path | None = None) -> str:
    """The ``floofyBoot`` function source between the template markers of ``boot.mjs``."""
    target = path or _template_path()
    if target is None:
        raise BootScriptError("boot.mjs template not found (spa-host/src or the Loader app's ui/)")
    text = Path(target).read_text(encoding="utf-8")
    start, end = text.find(_TEMPLATE_START), text.find(_TEMPLATE_END)
    if start < 0 or end < 0:
        raise BootScriptError(f"{target}: template markers missing")
    body = text[start + len(_TEMPLATE_START) : end].strip()
    _refuse_script_close(body, str(target))
    return body


def _refuse_script_close(text: str, what: str) -> None:
    if re.search(r"</script", text, re.I):
        raise BootScriptError(f"{what} contains a closing script tag; it cannot be inlined into index.html")


def _read_capped(path: Path, cap: int, what: str) -> str:
    if not path.is_file():
        raise BootScriptError(f"{what} {path} is missing")
    data = path.read_bytes()
    if len(data) > cap:
        raise BootScriptError(f"{what} {path} is {len(data)} bytes; the cap is {cap}")
    return data.decode("utf-8")


def _json_literal(value: Any) -> str:
    """JSON that is safe inside an inline script (``<`` escaped, so ``</script`` cannot appear)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _asset_url(mod_id: str, source: str) -> str:
    return f"/apps/{LOADER_APP_NAME}/ui/{BOOT_ASSETS_DIR}/{mod_id}/{Path(source).name}"


def render_boot_script(boot_mods: list[BootMod], defaults: dict[str, str], *, theme_bootstrap: bool = True, template: str | None = None) -> tuple[str, list[UiAsset]]:
    """The inline ``<script id="floofy-boot">`` element and the ui assets it references."""
    assets: list[UiAsset] = []
    rendered_mods: list[str] = []
    for mod in boot_mods:
        if not _MOD_ID_RE.match(mod.mod_id):
            raise BootScriptError(f"{mod.mod_id!r} is not a mod id")
        boot = mod.boot
        entry: dict[str, Any] = {"id": mod.mod_id}
        for key in ("favicon", "logo"):
            source = boot.get(key)
            if isinstance(source, str) and source:
                path = (mod.mod_dir / source).resolve()
                if not path.is_file() or not path.is_relative_to(mod.mod_dir.resolve()):
                    raise BootScriptError(f"{mod.label}: boot.{key} {source!r} is not a file inside the mod")
                entry[key] = _asset_url(mod.mod_id, source)
                assets.append(UiAsset(source=path, dest=f"{BOOT_ASSETS_DIR}/{mod.mod_id}/{path.name}", mod=mod.mod_id))
        if isinstance(boot.get("title"), str):
            entry["title"] = boot["title"]
        if isinstance(boot.get("attributes"), dict):
            entry["attributes"] = {str(k): str(v) for k, v in boot["attributes"].items()}
        if isinstance(boot.get("when"), dict):
            entry["when"] = dict(boot["when"])
        literal = _json_literal(entry)
        hook_path = mod.part.get("path")
        if isinstance(hook_path, str) and hook_path:
            hook = _read_capped(mod.mod_dir / hook_path, MAX_BOOT_HOOK_BYTES, f"{mod.label}: boot hook").strip()
            _refuse_script_close(hook, f"{mod.label}: boot hook {hook_path}")
            if hook:
                literal = literal[:-1] + ',"hook":function(ctx){\n' + hook + "\n}}"
        rendered_mods.append(literal)
    config = f'{{"defaults":{_json_literal(defaults)},"themeBootstrap":{"true" if theme_bootstrap else "false"},"mods":[{",".join(rendered_mods)}]}}'
    script = f'<script id="{BOOT_SCRIPT_ID}">\n{template or boot_template()}\nfloofyBoot({config});\n</script>\n  '
    return script, assets


def _style_for(mod: BootMod) -> str | None:
    source = mod.boot.get("css")
    if not isinstance(source, str) or not source:
        return None
    css = _read_capped(mod.mod_dir / source, MAX_BOOT_CSS_BYTES, f"{mod.label}: boot.css")
    if re.search(r"</style", css, re.I):
        raise BootScriptError(f"{mod.label}: boot.css contains a closing style tag")
    return f'<style id="{BOOT_CSS_ID_PREFIX}{mod.mod_id}">{css.strip()}</style>\n  '


def build_boot_descriptor(
    boot_mods: list[BootMod],
    *,
    host_home: Path,
    loader_tag: bool = True,
    theme_bootstrap: bool = True,
    defaults: dict[str, str] | None = None,
    template: str | None = None,
) -> PatchDescriptor | None:
    """The generated ``index.html`` descriptor (``None`` when there is nothing to inject)."""
    ops: list[dict[str, Any]] = []
    assets: list[UiAsset] = []
    if loader_tag:
        ops.append({"op": "insert-before", "fingerprint": _MAIN_TAG_FINGERPRINT, "regex": True, "content": LOADER_TAG, "marker": f'id="{LOADER_TAG_ID}"', "description": "SPA host loader tag (spike 1.3)"})
    if boot_mods:
        script, assets = render_boot_script(boot_mods, defaults or read_theme_defaults(host_home), theme_bootstrap=theme_bootstrap, template=template)
        ops.append({"op": "insert-before", "fingerprint": "<script", "occurrence": "first", "content": script, "marker": f'id="{BOOT_SCRIPT_ID}"', "description": "first-frame boot script (theme bootstrap + " + ", ".join(m.mod_id for m in boot_mods) + ")"})
        for mod in boot_mods:
            style = _style_for(mod)
            if style is not None:
                ops.append({"op": "append-head", "content": style, "marker": f'id="{BOOT_CSS_ID_PREFIX}{mod.mod_id}"', "description": f"baked CSS of {mod.mod_id}"})
    if not ops:
        return None
    descriptor = PatchDescriptor.from_dict(
        {"schema": 1, "target": "kiro_crew/static/dist/index.html", "description": "FloofyCrew SPA host: loader tag and boot activation (generated)", "ops": ops},
        source="floofycrew:boot",
    )
    return descriptor.with_ui_assets(assets)
