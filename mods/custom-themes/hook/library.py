"""The theme library: one directory per theme under the mod's data dir, and the document the editor edits.

```
<data dir>/library/<slug>/
├── pack/                 # a host theme pack, exactly what the host installs
│   ├── theme.json        #   manifest (slug, name, emoji, level, formatVersion, branding, fonts, loaderIcons)
│   ├── variables.json    #   {"dark": {...}, "light": {...}}
│   ├── styles/overrides.css   (optional; the host's runtime allowlist applies to it)
│   ├── styles/fonts/*.woff2   (optional)
│   └── branding/logo.*, favicon.*, wordmark.*, preview.*   (optional)
├── extra.css             # the FloofyCrew layer: unscoped, any selector; applied by the mod
└── floofy-theme.json     # meta: basedOn, createdAt, updatedAt, notes
```

The **document** is the JSON shape the editor page round-trips (``to_document`` /
``save_document``) and the export format (``export_document`` adds every binary
asset base64-encoded, ``import_document`` writes it back). The pack directory is
the only thing that reaches the host; ``extra.css`` never does.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from floofy_core.themes import (
    THEME_CSS_VARS,
    THEME_FILE_CAPS,
    ThemeValidationError,
    direct_write_theme,
    theme_css,
    validate_overrides_css,
    validate_theme_data,
    validate_theme_dir,
)

from .cssc import CssRefused, compile_theme, extra_css_problems, raise_specificity

__all__ = [
    "Library",
    "LibraryError",
    "SLUG_RE",
    "derive_level",
    "installed_host_themes",
    "read_active_theme",
]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
PACK_DIR = "pack"
EXTRA_CSS = "extra.css"
META_FILE = "floofy-theme.json"
_BRANDING_FILES = {
    "logo.svg": "logo",
    "logo.png": "logo",
    "favicon.ico": "favicon",
    "favicon.png": "favicon",
    "favicon.svg": "favicon",
    "wordmark.svg": "wordmark",
    "wordmark.png": "wordmark",
    "preview.png": "preview",
    "preview.webp": "preview",
}
_FONT_RE = re.compile(r"^[a-z0-9_-]{1,64}\.(woff2|ttf)$", re.I)
_LEVEL2_DIRS = ("overlays", "topbar", "audio")
_MANIFEST_KEYS = ("slug", "name", "emoji", "level", "formatVersion", "branding", "fonts", "loaderIcons")


class LibraryError(ValueError):
    """A request the library refuses (the message is shown to the user as is)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def derive_level(pack: Path) -> int:
    """The lowest level that covers the pack's payload (what the host's validator wants declared)."""
    level = 0
    if (pack / "branding").is_dir() and any((pack / "branding").iterdir()):
        level = 1
    if (pack / "styles" / "fonts").is_dir() and any((pack / "styles" / "fonts").iterdir()):
        level = 1
    if (pack / "styles" / "overrides.css").is_file():
        level = max(level, 1)
    if (pack / "loader").is_dir() and any((pack / "loader").iterdir()):
        level = max(level, 1)
    for name in _LEVEL2_DIRS:
        if (pack / name).is_dir() and any((pack / name).iterdir()):
            level = 2
    if (pack / "persona.md").is_file():
        level = 2
    return level


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_active_theme(host_home: Path) -> str | None:
    """``dashboard.theme_color`` from the host's ``config.json`` (the theme every client falls back to)."""
    document = _read_json(Path(host_home) / "config.json", {})
    dashboard = document.get("dashboard") if isinstance(document, dict) else None
    color = dashboard.get("theme_color") if isinstance(dashboard, dict) else None
    return color if isinstance(color, str) else None


def installed_host_themes(host_home: Path) -> list[dict[str, Any]]:
    """Every custom theme the host knows: installed pack directories and editor-created ``<slug>.json`` records."""
    themes_dir = Path(host_home) / "themes"
    found: list[dict[str, Any]] = []
    if not themes_dir.is_dir():
        return found
    for entry in sorted(themes_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() and (entry / "theme.json").is_file():
            manifest = _read_json(entry / "theme.json", {})
            variables = _read_json(entry / "variables.json", None)
            if variables is None:
                variables = _read_json(entry / "styles" / "variables.json", {})
            slug = manifest.get("slug") if isinstance(manifest.get("slug"), str) else entry.name
            found.append({"slug": slug, "name": manifest.get("name", slug), "emoji": manifest.get("emoji", "🎨"), "level": manifest.get("level", 0), "kind": "pack", "path": str(entry), "variables": variables if isinstance(variables, dict) else {}})
        elif entry.is_file() and entry.suffix == ".json":
            record = _read_json(entry, {})
            if isinstance(record, dict) and isinstance(record.get("dark"), dict):
                found.append({"slug": entry.stem, "name": record.get("name", entry.stem), "emoji": record.get("emoji", "🎨"), "level": 0, "kind": "record", "path": str(entry), "variables": {"dark": record.get("dark", {}), "light": record.get("light", {})}})
    return found


@dataclass
class Library:
    root: Path
    host_home: Path

    # -- paths -----------------------------------------------------------------------

    def dir(self, slug: str) -> Path:
        if not SLUG_RE.match(slug):
            raise LibraryError(f"{slug!r} is not a theme slug (lower-case letters, digits and dashes, 1–40 characters)")
        return self.root / slug

    def pack(self, slug: str) -> Path:
        return self.dir(slug) / PACK_DIR

    def exists(self, slug: str) -> bool:
        return SLUG_RE.match(slug) is not None and (self.pack(slug) / "theme.json").is_file()

    def slugs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / PACK_DIR / "theme.json").is_file() and SLUG_RE.match(p.name))

    # -- reading -----------------------------------------------------------------------

    def meta(self, slug: str) -> dict[str, Any]:
        meta = _read_json(self.dir(slug) / META_FILE, {})
        return meta if isinstance(meta, dict) else {}

    def manifest(self, slug: str) -> dict[str, Any]:
        manifest = _read_json(self.pack(slug) / "theme.json", {})
        return manifest if isinstance(manifest, dict) else {}

    def variables(self, slug: str) -> dict[str, Any]:
        pack = self.pack(slug)
        for rel in ("variables.json", "styles/variables.json"):
            if (pack / rel).is_file():
                data = _read_json(pack / rel, {})
                return data if isinstance(data, dict) else {}
        return {"dark": {}, "light": {}}

    def extra_css(self, slug: str) -> str:
        path = self.dir(slug) / EXTRA_CSS
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def overrides_css(self, slug: str) -> str:
        path = self.pack(slug) / "styles" / "overrides.css"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def assets(self, slug: str) -> list[dict[str, Any]]:
        """The binary files the pack ships (branding, fonts), relative to the pack."""
        pack = self.pack(slug)
        out: list[dict[str, Any]] = []
        for sub in ("branding", "styles/fonts", "loader"):
            directory = pack / sub
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and not path.is_symlink():
                    out.append({"path": f"{sub}/{path.name}", "bytes": path.stat().st_size})
        return out

    def summary(self, slug: str) -> dict[str, Any]:
        manifest = self.manifest(slug)
        meta = self.meta(slug)
        variables = self.variables(slug)
        return {
            "slug": slug,
            "name": manifest.get("name", slug),
            "emoji": manifest.get("emoji", "🎨"),
            "level": manifest.get("level", 0),
            "botName": (manifest.get("branding") or {}).get("botName") if isinstance(manifest.get("branding"), dict) else None,
            "hasExtraCss": bool(self.extra_css(slug).strip()),
            "hasOverridesCss": bool(self.overrides_css(slug).strip()),
            "assets": [a["path"] for a in self.assets(slug)],
            "swatch": _swatch(variables),
            "basedOn": meta.get("basedOn"),
            "createdAt": meta.get("createdAt"),
            "updatedAt": meta.get("updatedAt"),
            "installed": self.installed_dir(slug).is_dir(),
        }

    def to_document(self, slug: str) -> dict[str, Any]:
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        manifest = self.manifest(slug)
        return {
            "slug": slug,
            "manifest": {k: manifest[k] for k in _MANIFEST_KEYS if k in manifest},
            "variables": self.variables(slug),
            "overridesCss": self.overrides_css(slug),
            "extraCss": self.extra_css(slug),
            "assets": self.assets(slug),
            "meta": self.meta(slug),
            "installed": self.installed_dir(slug).is_dir(),
        }

    # -- validation ----------------------------------------------------------------------

    def check_document(self, document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        """Normalise and validate a document; returns ``(manifest, variables, overrides_css, extra_css)`` or raises."""
        if not isinstance(document, dict):
            raise LibraryError("the theme document must be a JSON object")
        manifest_in = document.get("manifest") if isinstance(document.get("manifest"), dict) else {}
        slug = str(document.get("slug") or manifest_in.get("slug") or "").strip()
        if not SLUG_RE.match(slug):
            raise LibraryError(f"{slug!r} is not a theme slug (lower-case letters, digits and dashes, 1–40 characters)")
        name = str(manifest_in.get("name") or "").strip()
        if not name:
            raise LibraryError("the theme needs a name")
        if len(name) > 60:
            raise LibraryError("the theme name is longer than 60 characters")
        emoji = str(manifest_in.get("emoji") or "🎨").strip()[:4] or "🎨"
        variables_in = document.get("variables") if isinstance(document.get("variables"), dict) else {}
        variables = {}
        for mode in ("dark", "light"):
            block = variables_in.get(mode) if isinstance(variables_in.get(mode), dict) else {}
            variables[mode] = {str(k): str(v).strip() for k, v in block.items() if str(v).strip() != ""}
            unknown = sorted(k for k in variables[mode] if k not in THEME_CSS_VARS)
            if unknown:
                raise LibraryError(f"{mode}: {', '.join(unknown[:5])} {'is' if len(unknown) == 1 else 'are'} not a theme variable the host accepts")
        problem = validate_theme_data({"name": name, "emoji": emoji, "dark": variables["dark"], "light": variables["light"]})
        if problem:
            raise LibraryError(problem)
        overrides = document.get("overridesCss")
        overrides = overrides if isinstance(overrides, str) else ""
        if overrides.strip():
            if len(overrides.encode("utf-8")) > THEME_FILE_CAPS["overrides"]:
                raise LibraryError(f"overrides.css is larger than the host's {THEME_FILE_CAPS['overrides']}-byte cap")
            problem = validate_overrides_css(overrides)
            if problem:
                raise LibraryError(problem)
        extra = document.get("extraCss")
        extra = extra if isinstance(extra, str) else ""
        problems = extra_css_problems(extra)
        if problems:
            raise LibraryError("; ".join(problems))
        manifest: dict[str, Any] = {"slug": slug, "name": name, "emoji": emoji, "formatVersion": 1}
        branding = manifest_in.get("branding")
        if isinstance(branding, dict):
            bot = branding.get("botName")
            if isinstance(bot, str) and bot.strip():
                if len(bot.strip()) > 48:
                    raise LibraryError("the bot name is longer than 48 characters")
                manifest["branding"] = {"botName": bot.strip()}
        fonts = manifest_in.get("fonts")
        if isinstance(fonts, list) and fonts:
            clean_fonts = []
            for entry in fonts:
                if not isinstance(entry, dict) or not isinstance(entry.get("family"), str) or not isinstance(entry.get("file"), str):
                    raise LibraryError("every font entry needs a family and a file")
                if not _FONT_RE.match(entry["file"]):
                    raise LibraryError(f"font file {entry['file']!r} must be a .woff2 or .ttf name")
                role = entry.get("role", "sans")
                if role not in ("sans", "mono"):
                    raise LibraryError(f"font {entry['family']!r}: role must be sans or mono")
                row: dict[str, Any] = {"family": entry["family"].strip(), "file": entry["file"], "role": role}
                weight = entry.get("weight", 400)
                if isinstance(weight, int) and 100 <= weight <= 900:
                    row["weight"] = weight
                if entry.get("style") in ("normal", "italic"):
                    row["style"] = entry["style"]
                clean_fonts.append(row)
            if len(clean_fonts) > 6:
                raise LibraryError("a pack may ship at most 6 font faces")
            manifest["fonts"] = clean_fonts
        icons = manifest_in.get("loaderIcons")
        if isinstance(icons, list) and icons:
            allowed = {"cloud", "flower", "heart", "moon", "sparkles", "star", "sun", "zap"}
            names = [str(i) for i in icons]
            if not (4 <= len(names) <= 8) or len(set(names)) != len(names) or not set(names) <= allowed:
                raise LibraryError("loaderIcons must be 4–8 distinct names from cloud, flower, heart, moon, sparkles, star, sun, zap")
            manifest["loaderIcons"] = names
        return manifest, variables, overrides, extra

    # -- writing -------------------------------------------------------------------------

    def save_document(self, document: dict[str, Any], *, based_on: str | None = None) -> dict[str, Any]:
        manifest, variables, overrides, extra = self.check_document(document)
        slug = manifest["slug"]
        directory = self.dir(slug)
        pack = self.pack(slug)
        created = not self.exists(slug)
        (pack / "styles").mkdir(parents=True, exist_ok=True)
        _write_json(pack / "variables.json", variables)
        overrides_path = pack / "styles" / "overrides.css"
        if overrides.strip():
            overrides_path.write_text(overrides.rstrip() + "\n", encoding="utf-8")
        elif overrides_path.exists():
            overrides_path.unlink()
        if not any((pack / "styles").iterdir()):
            (pack / "styles").rmdir()
        extra_path = directory / EXTRA_CSS
        if extra.strip():
            extra_path.write_text(extra.rstrip() + "\n", encoding="utf-8")
        elif extra_path.exists():
            extra_path.unlink()
        manifest["level"] = max(derive_level(pack), 1 if (manifest.get("branding") or manifest.get("fonts") or manifest.get("loaderIcons")) else 0)
        _write_json(pack / "theme.json", manifest)
        meta = self.meta(slug) if not created else {}
        incoming_meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        if isinstance(incoming_meta.get("notes"), str):
            meta["notes"] = incoming_meta["notes"][:4000]
        if based_on or incoming_meta.get("basedOn"):
            meta["basedOn"] = based_on or incoming_meta.get("basedOn")
        meta.setdefault("createdAt", _now())
        meta["updatedAt"] = _now()
        _write_json(directory / META_FILE, meta)
        try:
            validate_theme_dir(pack)
        except ThemeValidationError as exc:
            raise LibraryError(f"the host would refuse this pack: {exc}") from exc
        return self.to_document(slug)

    def put_asset(self, slug: str, rel: str, data: bytes) -> dict[str, Any]:
        """Write a branding image or a font file into the pack (``rel`` is ``branding/<name>`` or ``styles/fonts/<name>``)."""
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        target, cap = self._asset_target(slug, rel)
        if len(data) > cap:
            raise LibraryError(f"{rel} is {len(data)} bytes; the host's cap for it is {cap}")
        if len(data) == 0:
            raise LibraryError(f"{rel} is empty")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self._relevel(slug)
        return self.to_document(slug)

    def remove_asset(self, slug: str, rel: str) -> dict[str, Any]:
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        target, _ = self._asset_target(slug, rel)
        if target.is_file():
            target.unlink()
            parent = target.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        self._relevel(slug)
        return self.to_document(slug)

    def _asset_target(self, slug: str, rel: str) -> tuple[Path, int]:
        rel = rel.strip().lstrip("/")
        if rel.startswith("branding/"):
            name = rel[len("branding/") :].lower()
            if name not in _BRANDING_FILES or "/" in name:
                raise LibraryError(f"{rel!r} is not a branding file the host recognises ({', '.join(sorted(_BRANDING_FILES))})")
            return self.pack(slug) / "branding" / name, THEME_FILE_CAPS[_BRANDING_FILES[name]]
        if rel.startswith("styles/fonts/"):
            name = rel[len("styles/fonts/") :]
            if not _FONT_RE.match(name) or "/" in name:
                raise LibraryError(f"{rel!r} is not a font file name (.woff2 / .ttf)")
            return self.pack(slug) / "styles" / "fonts" / name, THEME_FILE_CAPS["font"]
        raise LibraryError(f"{rel!r}: assets live under branding/ or styles/fonts/")

    def _relevel(self, slug: str) -> None:
        pack = self.pack(slug)
        manifest = self.manifest(slug)
        manifest["level"] = max(derive_level(pack), 1 if (manifest.get("branding") or manifest.get("fonts") or manifest.get("loaderIcons")) else 0)
        _write_json(pack / "theme.json", manifest)
        meta = self.meta(slug)
        meta["updatedAt"] = _now()
        _write_json(self.dir(slug) / META_FILE, meta)

    def delete(self, slug: str) -> None:
        directory = self.dir(slug)
        if directory.is_dir():
            shutil.rmtree(directory)

    def duplicate(self, slug: str, new_slug: str, new_name: str | None = None) -> dict[str, Any]:
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        if not SLUG_RE.match(new_slug):
            raise LibraryError(f"{new_slug!r} is not a theme slug")
        if self.exists(new_slug):
            raise LibraryError(f"a theme {new_slug!r} already exists in the library", 409)
        shutil.copytree(self.dir(slug), self.dir(new_slug), symlinks=False)
        manifest = self.manifest(new_slug)
        manifest["slug"] = new_slug
        if new_name:
            manifest["name"] = new_name.strip()[:60]
        _write_json(self.pack(new_slug) / "theme.json", manifest)
        meta = self.meta(new_slug)
        meta["basedOn"] = f"library:{slug}"
        meta["createdAt"] = meta["updatedAt"] = _now()
        _write_json(self.dir(new_slug) / META_FILE, meta)
        return self.to_document(new_slug)

    def adopt_installed(self, installed_dir: Path, new_slug: str | None = None) -> dict[str, Any]:
        """Copy an installed host pack into the library (the way to edit a theme another mod or a GitHub install brought)."""
        try:
            summary = validate_theme_dir(installed_dir)
        except ThemeValidationError as exc:
            raise LibraryError(f"the installed pack does not validate: {exc}") from exc
        slug = new_slug or summary.slug
        if not SLUG_RE.match(slug):
            raise LibraryError(f"{slug!r} is not a theme slug")
        if self.exists(slug):
            raise LibraryError(f"a theme {slug!r} already exists in the library", 409)
        pack = self.pack(slug)
        pack.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(installed_dir, pack, symlinks=False, ignore=shutil.ignore_patterns(".git", ".github"))
        manifest = self.manifest(slug)
        manifest["slug"] = slug
        _write_json(pack / "theme.json", manifest)
        _write_json(self.dir(slug) / META_FILE, {"basedOn": f"installed:{summary.slug}", "createdAt": _now(), "updatedAt": _now()})
        return self.to_document(slug)

    # -- the host ---------------------------------------------------------------------

    def installed_dir(self, slug: str) -> Path:
        return Path(self.host_home) / "themes" / slug

    def install(self, slug: str) -> dict[str, Any]:
        """Write the pack into the host's themes directory the way the host's own install route does."""
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        record = Path(self.host_home) / "themes" / f"{slug}.json"
        if record.is_file():
            raise LibraryError(f"the host has an editor-created theme record {slug}.json; delete it in Settings → Display first", 409)
        try:
            installed, summary = direct_write_theme(self.pack(slug), Path(self.host_home) / "themes")
        except ThemeValidationError as exc:
            raise LibraryError(f"the host refused the pack: {exc}") from exc
        return {"slug": summary.slug, "level": summary.level, "path": str(installed), "files": summary.files}

    def uninstall(self, slug: str) -> bool:
        if not SLUG_RE.match(slug):
            raise LibraryError(f"{slug!r} is not a theme slug")
        target = self.installed_dir(slug)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
            return True
        return False

    # -- CSS ----------------------------------------------------------------------------

    def compiled_css(self, slug: str) -> str:
        if not self.exists(slug):
            raise LibraryError(f"no theme {slug!r} in the library", 404)
        try:
            return compile_theme(slug, self.variables(slug), self.extra_css(slug))
        except CssRefused as exc:
            raise LibraryError(str(exc)) from exc

    def boot_css(self) -> tuple[str, list[str]]:
        """One sheet for every custom theme the host has installed: the palette (so a cold client does not flash)
        plus, for library themes, the extra layer. Returns ``(css, slugs)``."""
        parts: list[str] = ["/* floofy custom-themes: first-frame sheet for every installed custom theme (generated) */"]
        covered: list[str] = []
        for theme in installed_host_themes(self.host_home):
            slug = theme["slug"]
            if not SLUG_RE.match(slug):
                continue
            extra = self.extra_css(slug) if self.exists(slug) else ""
            try:
                parts.append(compile_theme(slug, theme["variables"], extra, header=True))
            except CssRefused:
                parts.append(raise_specificity(slug, theme_css(slug, theme["variables"])))
            covered.append(slug)
        return "\n".join(parts) + "\n", covered

    # -- export / import -----------------------------------------------------------------

    def export_document(self, slug: str) -> dict[str, Any]:
        document = self.to_document(slug)
        pack = self.pack(slug)
        files: dict[str, str] = {}
        for asset in document["assets"]:
            files[asset["path"]] = base64.b64encode((pack / asset["path"]).read_bytes()).decode("ascii")
        document["assets"] = files
        document["format"] = "floofy-theme/1"
        document.pop("installed", None)
        return document

    def import_document(self, document: dict[str, Any], *, slug: str | None = None, overwrite: bool = False) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise LibraryError("an exported theme is a JSON object")
        if document.get("format") not in (None, "floofy-theme/1"):
            raise LibraryError(f"unknown export format {document.get('format')!r}")
        incoming = dict(document)
        if slug:
            incoming["slug"] = slug
            incoming["manifest"] = {**(incoming.get("manifest") or {}), "slug": slug}
        manifest, _variables, _o, _e = self.check_document(incoming)
        if self.exists(manifest["slug"]) and not overwrite:
            raise LibraryError(f"a theme {manifest['slug']!r} already exists in the library (pass overwrite to replace it)", 409)
        assets = incoming.pop("assets", None)
        saved = self.save_document(incoming, based_on=(incoming.get("meta") or {}).get("basedOn") if isinstance(incoming.get("meta"), dict) else None)
        if isinstance(assets, dict):
            for rel, encoded in assets.items():
                if not isinstance(encoded, str):
                    continue
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise LibraryError(f"{rel}: not base64") from exc
                self.put_asset(manifest["slug"], str(rel), data)
            saved = self.to_document(manifest["slug"])
        return saved


def _swatch(variables: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for mode in ("dark", "light"):
        block = variables.get(mode) if isinstance(variables.get(mode), dict) else {}
        out[mode] = {k: str(block.get(k, "")) for k in ("--bg", "--text", "--accent", "--card")}
    return out
