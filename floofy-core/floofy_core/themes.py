"""Theme packs: the host's install-time validation, ported, and the byte-equivalent direct write.

License note (Apache-2.0 §4(b): this file states its changes). The validation
logic in this module is derived from KiroCrew's theme validator
(``kiro_crew/dashboard/theme_validate.py``), licensed under the Apache License,
Version 2.0 — the upstream copyright attribution and the license text are in
``NOTICE.md`` and ``LICENSES/Apache-2.0.txt`` in the distribution root. It is a
hand-written Python port, restructured for FloofyCrew (dataclasses,
:class:`ThemeValidationError`, the direct-write path); the section below and the
in-line ``L…`` references say exactly what was ported and what was left out.

Requirement 2.1 / 2.8: kind ``theme`` installs through the host's own route
(``POST /api/themes/install``) when it is open, and through a **byte-equivalent
direct write** when the route is closed by governance (``capabilities.theme_install``
denied → 403) or the gateway is down. The host's install is a per-file byte copy
of the pack (meta files skipped, symlinks refused) into a staging directory that
is validated and then renamed over ``<themes>/<slug>/`` (``dashboard/handlers/
themes.py`` ``_copy_installed_theme`` L313–360 and the install flow L440–535), so
the direct write produces the same tree: same files, same bytes, same slug.

The validation is ported from ``kiro_crew/dashboard/theme_validate.py`` (0.7.0.5):

* manifest: ``formatVersion`` (L1211–1226), ``level`` 0–2 (L1227–1233,
  ``_THEME_MAX_LEVEL`` L245), ``name``/``emoji`` (L1234–1239), font roles at
  install (L1252–1275, ``_THEME_FONT_ROLES`` L265);
* structure: allowed directories by level (``_THEME_ALLOWED_DIRS`` L247–255),
  entry and total-byte caps by level (L257–258), per-file caps by category
  (``_THEME_FILE_CAPS`` L300–320), the file classifier (``_classify_theme_file``
  L476–522), meta names skipped (L230–232), symlinks and escaping paths refused
  (L1310–1316), font and overlay counts (L262, L278);
* content: ``overrides.css`` injection denylist with comment/escape
  normalisation and forbidden selectors (L322–360, L840–867), overlay/topbar
  HTML denylist (L365–374, L882–893), persona bounds (L896–905), audio magic
  bytes (L460–473);
* data: ``variables.json`` colour values through the 54-variable allowlist and
  the value sanitiser (``_THEME_CSS_VARS`` L34, ``_sanitize_css_value`` L120–137,
  ``_validate_theme_data`` L139–171), slug derivation (``_slugify_theme_name``
  L189–194).

Not ported (deeper semantic checks): the per-rule layout and font-pin scan of
``overrides.css`` (L747–838), and the optional ``overlays``/``topbar``/``audio``/
``loader`` manifest declarations (L954–1183). The host re-validates every pack on
read (fail-closed), so a pack that fails those is refused by the host at load
time — never silently loaded. ``validate_theme_dir`` reports them as ``unchecked``.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ThemeSummary",
    "ThemeValidationError",
    "direct_write_theme",
    "pack_files",
    "slugify_theme_name",
    "validate_theme_data",
    "validate_theme_dir",
    "theme_css",
    "theme_css_for_pack",
]

# --- constants (theme_validate.py line numbers in the module docstring) --------------

THEME_NAME_MAX_LEN = 60
THEME_SLUG_MAX_LEN = 40
THEME_EMOJI_MAX_LEN = 4
THEME_DEFAULT_EMOJI = "🎨"
THEME_REQUIRED_VARS = ("--bg", "--text", "--accent")
THEME_CSS_VARS: frozenset[str] = frozenset(
    "--bg --bg-accent --bg-elevated --bg-hover --card --card-fg --card-hl --panel --panel-strong --chrome "
    "--text --text-strong --muted --muted-strong --muted-fg --border --border-strong --border-hover "
    "--accent --accent-fg --accent-hover --accent-subtle --accent-glow --ring --ok --ok-fg --ok-subtle "
    "--warn --warn-fg --warn-subtle --danger --danger-fg --danger-subtle --info --info-fg --aim --aim-fg "
    "--aim-subtle --clarify --clarify-subtle --json-key --json-str --json-num --json-bool --diff-add "
    "--diff-add-text --diff-del --diff-del-text --diff-hunk --diff-hunk-text --diff-meta-text --shadow-sm "
    "--shadow-md --shadow-lg --term-magenta --term-cyan".split()
)
_CSS_VALUE_ALLOWED_RE = re.compile(r"^[a-zA-Z0-9#(),.\- %/]+$")
_CSS_DANGEROUS_FUNC_RE = re.compile(r"url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(", re.IGNORECASE)

THEME_MANIFEST_NAME = "theme.json"
THEME_VARIABLES_REL = ("variables.json", "styles/variables.json")
THEME_META_IGNORE = frozenset({".git", ".github", ".gitignore", ".ds_store", "license", "license.md", "license.txt"})
THEME_MAX_FILE_BYTES = 64 * 1024
THEME_FORMAT_VERSION = 1
THEME_MAX_LEVEL = 2
THEME_ALLOWED_DIRS = {"styles": 0, "styles/fonts": 1, "branding": 1, "overlays": 2, "topbar": 2, "audio": 2, "loader": 1}
THEME_ENTRIES_BY_LEVEL = {0: 32, 1: 64, 2: 160}
THEME_TOTAL_BYTES_BY_LEVEL = {0: 256 * 1024, 1: 2 * 1024 * 1024, 2: 5 * 1024 * 1024}
THEME_MAX_FONTS = 6
THEME_FONT_ROLES = frozenset({"sans", "mono"})
THEME_MAX_OVERLAYS = 5
THEME_PERSONA_MAX_CHARS = 2000
THEME_LOADER_IMAGE_EXTS = ("png", "webp", "gif", "svg")
THEME_FILE_CAPS = {
    "manifest": 16 * 1024,
    "variables": 64 * 1024,
    "readme": 32 * 1024,
    "overrides": 100 * 1024,
    "font": 512 * 1024,
    "logo": 100 * 1024,
    "favicon": 50 * 1024,
    "wordmark": 100 * 1024,
    "preview": 512 * 1024,
    "overlay": 200 * 1024,
    "topbar": 100 * 1024,
    "loader_icon": 256 * 1024,
    "audio_manifest": 16 * 1024,
    "audio": 512 * 1024,
    "audio_ambient": 2 * 1024 * 1024,
    "persona": 8 * 1024,
}
_THEME_CSS_DENY_RE = re.compile(r"@import|expression\s*\(|javascript:|-moz-binding|url\s*\(\s*['\"]?\s*(?:https?:)?//", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.DOTALL)
THEME_CSS_FORBIDDEN = ("iframe", "script", "[data-auth]", ".token", ".credential", "#app-root")
_THEME_HTML_DENY_RE = re.compile(
    r"<script[^>]*\bsrc\s*=|document\.cookie|\blocalStorage\b|\bsessionStorage\b|\bXMLHttpRequest\b|\bfetch\s*\(\s*['\"]?(?:https?:)?//",
    re.IGNORECASE,
)


class ThemeValidationError(ValueError):
    """The pack fails a check the host applies at install time (same message shape)."""


@dataclass
class ThemeSummary:
    """What the host registers after validation (``_validate_theme_dir`` L1404–1421)."""

    slug: str
    name: str
    emoji: str
    level: int
    dark: dict[str, str]
    light: dict[str, str]
    files: list[str] = field(default_factory=list)  # relative POSIX paths that the install copies
    unchecked: list[str] = field(default_factory=list)  # host checks this port does not reproduce

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "name": self.name, "emoji": self.emoji, "level": self.level, "files": list(self.files), "unchecked": list(self.unchecked)}


# --- data checks (L107–194) ----------------------------------------------------------


def sanitize_css_value(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 200:
        return None
    trimmed = value.strip()
    if not trimmed or not _CSS_VALUE_ALLOWED_RE.match(trimmed) or _CSS_DANGEROUS_FUNC_RE.search(trimmed):
        return None
    return trimmed


def validate_theme_data(data: Any) -> str | None:
    """``_validate_theme_data`` (L139–171): returns the host's error string or ``None``."""
    if not isinstance(data, dict):
        return "theme must be a JSON object"
    name = data.get("name", "")
    if not isinstance(name, str):
        return "name must be a string"
    if not name.strip():
        return "name is required"
    if len(name.strip()) > THEME_NAME_MAX_LEN:
        return f"name too long (max {THEME_NAME_MAX_LEN} chars)"
    if not isinstance(data.get("emoji", ""), str):
        return "emoji must be a string"
    for mode in ("dark", "light"):
        mode_data = data.get(mode, {})
        if not isinstance(mode_data, dict):
            return f"'{mode}' must be a JSON object"
        for required in THEME_REQUIRED_VARS:
            if required not in mode_data:
                return f"'{mode}' is missing required variable '{required}'"
        for key, value in mode_data.items():
            if key not in THEME_CSS_VARS:
                return f"'{mode}' key '{key}' is not a recognized theme variable"
            if sanitize_css_value(value) is None:
                return f"'{mode}' variable '{key}' has an invalid value"
    return None


def strip_to_allowed_vars(mode_data: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in mode_data.items():
        if key in THEME_CSS_VARS:
            clean = sanitize_css_value(value)
            if clean is not None:
                result[key] = clean
    return result


def slugify_theme_name(name: str) -> str:
    """``_slugify_theme_name`` (L189–194)."""
    slug = re.sub(r"[^a-z0-9\-]", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:THEME_SLUG_MAX_LEN] or "custom"


# --- structure (L230–335, L441–525, L908–930) -------------------------------------------


def classify_theme_file(rel: str) -> tuple[str | None, int]:
    """``_classify_theme_file`` (L476–522): ``(category, min_level)`` or ``(None, 0)``."""
    if rel == "theme.json":
        return "manifest", 0
    if rel in THEME_VARIABLES_REL:
        return "variables", 0
    if rel == "readme.md":
        return "readme", 0
    if rel == "styles/overrides.css":
        return "overrides", 1
    if rel == "persona.md":
        return "persona", 2
    if rel == "audio/manifest.json":
        return "audio_manifest", 2
    parts = rel.split("/")
    ext = rel.rsplit(".", 1)[-1] if "." in rel else ""
    top = parts[0]
    if top == "styles" and len(parts) == 3 and parts[1] == "fonts" and ext in ("woff2", "ttf"):
        return "font", 1
    if top == "branding" and len(parts) == 2:
        stem = parts[1].rsplit(".", 1)[0]
        if stem == "logo" and ext in ("svg", "png"):
            return "logo", 1
        if stem == "favicon" and ext in ("ico", "png", "svg"):
            return "favicon", 1
        if stem == "wordmark" and ext in ("svg", "png"):
            return "wordmark", 1
        if stem == "preview" and ext in ("png", "webp"):
            return "preview", 1
    if top == "overlays" and len(parts) == 2 and ext == "html":
        return "overlay", 2
    if top == "topbar" and len(parts) == 2 and parts[1] in ("dark.html", "light.html"):
        return "topbar", 2
    if top == "loader" and len(parts) == 2 and ext in THEME_LOADER_IMAGE_EXTS:
        return "loader_icon", 1
    if top == "audio" and len(parts) == 2 and ext in ("mp3", "ogg", "wav"):
        return ("audio_ambient" if parts[1].rsplit(".", 1)[0] == "ambient" else "audio"), 2
    return None, 0


def _decode_css_escapes(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return match.group(2)

    return _CSS_ESCAPE_RE.sub(_sub, text)


def _css_denylist_normalize(text: str) -> str:
    return _decode_css_escapes(_CSS_COMMENT_RE.sub("", text))


def validate_overrides_css(text: str) -> str | None:
    """Layer 1 of ``_validate_overrides_css`` (L840–867): injection denylist + forbidden selectors."""
    if _THEME_CSS_DENY_RE.search(text) or _THEME_CSS_DENY_RE.search(_css_denylist_normalize(text)):
        return "overrides.css uses a forbidden pattern (@import / external url() / expression() / javascript: / -moz-binding)"
    low = text.lower()
    for selector in THEME_CSS_FORBIDDEN:
        if selector in low:
            return f"overrides.css targets a forbidden selector: {selector}"
    return None


def validate_overlay_html(text: str, name: str) -> str | None:
    if _THEME_HTML_DENY_RE.search(text):
        return f"'{name}' uses a forbidden pattern (external <script src>, fetch/XHR to a URL, or cookie/localStorage/sessionStorage access)"
    return None


def validate_persona(text: str) -> str | None:
    if len(text) > THEME_PERSONA_MAX_CHARS:
        return f"persona.md too long (max {THEME_PERSONA_MAX_CHARS} chars)"
    low = text.lower()
    if "drop" not in low or "persona" not in low:
        return "persona.md must include an explicit 'drop persona on request' instruction"
    if "security" not in low and "accuracy" not in low:
        return "persona.md must state that security/accuracy override the persona"
    return None


def sniff_audio(head: bytes) -> bool:
    if len(head) < 4:
        return False
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return True
    if head[:4] == b"OggS":
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _read_json_capped(path: Path, max_bytes: int) -> tuple[Any, str | None]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read {path.name}: {exc}"
    if len(raw) > max_bytes:
        return None, f"{path.name} too large (max {max_bytes} bytes)"
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{path.name} is not valid JSON: {exc}"


def _walk_entries(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            rel = (here / name).relative_to(root).as_posix().lower()
            if rel.split("/", 1)[0] in THEME_META_IGNORE or rel in THEME_META_IGNORE:
                continue
            kept.append(name)
            yield here / name
        dirnames[:] = kept
        for name in sorted(filenames):
            yield here / name


def validate_theme_dir(path: Path) -> ThemeSummary:
    """``_validate_theme_dir(installing=True)`` (L1183–1421) as far as ported; raises :class:`ThemeValidationError`."""
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ThemeValidationError("theme path is not a directory")
    root = path.resolve()
    manifest_path = path / THEME_MANIFEST_NAME
    if not manifest_path.is_file():
        raise ThemeValidationError("missing theme.json manifest")
    manifest, error = _read_json_capped(manifest_path, THEME_FILE_CAPS["manifest"])
    if error:
        raise ThemeValidationError(error)
    if not isinstance(manifest, dict):
        raise ThemeValidationError("theme.json must be a JSON object")
    fmt = manifest.get("formatVersion")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise ThemeValidationError(f'theme.json must declare "formatVersion" (integer; current version is {THEME_FORMAT_VERSION})')
    if fmt > THEME_FORMAT_VERSION:
        raise ThemeValidationError(f"this pack requires a newer version of Kiro Crew (pack formatVersion {fmt}, supported {THEME_FORMAT_VERSION})")
    if fmt < 1:
        raise ThemeValidationError('theme.json "formatVersion" must be a positive integer (>= 1)')
    level = manifest.get("level", 0)
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= THEME_MAX_LEVEL:
        raise ThemeValidationError(f"theme.json 'level' must be 0, 1, or 2 (got {level!r})")
    name = manifest.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ThemeValidationError("theme.json 'name' is required")
    emoji = manifest.get("emoji", THEME_DEFAULT_EMOJI)
    if not isinstance(emoji, str):
        raise ThemeValidationError("theme.json 'emoji' must be a string")
    fonts_manifest = manifest.get("fonts")
    if isinstance(fonts_manifest, list):
        for entry in fonts_manifest:
            if not isinstance(entry, dict) or "role" not in entry:
                continue
            role = entry["role"]
            if isinstance(role, str) and role in THEME_FONT_ROLES:
                continue
            family = entry.get("family")
            label = family if isinstance(family, str) and family else "<unnamed>"
            raise ThemeValidationError(f"font entry '{label}' has role {role!r}; valid roles are 'sans' and 'mono'")

    max_entries = THEME_ENTRIES_BY_LEVEL.get(level, 160)
    max_total = THEME_TOTAL_BYTES_BY_LEVEL.get(level, 5 * 1024 * 1024)
    total = entries = fonts = overlays = 0
    files: list[str] = []
    unchecked: list[str] = []
    for entry in _walk_entries(path):
        rel = entry.relative_to(path).as_posix()
        lowered = rel.lower()
        if lowered.split("/", 1)[0] in THEME_META_IGNORE or lowered in THEME_META_IGNORE:
            continue
        entries += 1
        if entries > max_entries:
            raise ThemeValidationError(f"too many files in theme (max {max_entries})")
        if entry.is_symlink():
            raise ThemeValidationError(f"symlinks are not allowed: {entry.name}")
        try:
            entry.resolve().relative_to(root)
        except (OSError, ValueError):
            raise ThemeValidationError("path escapes theme directory") from None
        if entry.is_dir():
            if lowered not in THEME_ALLOWED_DIRS:
                raise ThemeValidationError(f"unexpected directory in theme: '{lowered}'")
            if THEME_ALLOWED_DIRS[lowered] > level:
                raise ThemeValidationError(f"'{lowered}/' requires level {THEME_ALLOWED_DIRS[lowered]}; theme declares level {level}")
            continue
        category, min_level = classify_theme_file(lowered)
        if category is None:
            raise ThemeValidationError(f"unexpected file in theme: '{lowered}'")
        if min_level > level:
            raise ThemeValidationError(f"'{lowered}' is a Level {min_level} asset; theme declares level {level}")
        cap = THEME_FILE_CAPS.get(category, THEME_MAX_FILE_BYTES)
        size = entry.stat().st_size
        if size > cap:
            raise ThemeValidationError(f"'{lowered}' too large (max {cap} bytes)")
        total += size
        if category == "font":
            fonts += 1
            if fonts > THEME_MAX_FONTS:
                raise ThemeValidationError(f"too many fonts (max {THEME_MAX_FONTS})")
        elif category == "overlay":
            overlays += 1
            if overlays > THEME_MAX_OVERLAYS:
                raise ThemeValidationError(f"too many overlays (max {THEME_MAX_OVERLAYS})")
        if category == "overrides":
            problem = validate_overrides_css(entry.read_text(encoding="utf-8", errors="replace"))
            if problem:
                raise ThemeValidationError(problem)
            unchecked.append("overrides.css layout and font-pin rules")
        elif category in ("overlay", "topbar"):
            problem = validate_overlay_html(entry.read_text(encoding="utf-8", errors="replace"), lowered)
            if problem:
                raise ThemeValidationError(problem)
            unchecked.append(f"{category} manifest declarations")
        elif category == "persona":
            problem = validate_persona(entry.read_text(encoding="utf-8", errors="replace"))
            if problem:
                raise ThemeValidationError(problem)
        elif category in ("audio", "audio_ambient", "audio_manifest"):
            if category != "audio_manifest":
                with entry.open("rb") as handle:
                    if not sniff_audio(handle.read(16)):
                        raise ThemeValidationError(f"'{lowered}' is not a valid audio file")
            unchecked.append("audio manifest declarations")
        elif category == "loader_icon":
            unchecked.append("loader image declarations")
        files.append(rel)
    if total > max_total:
        raise ThemeValidationError(f"theme too large (max {max_total} bytes total)")

    variables_path = next((path / rel for rel in THEME_VARIABLES_REL if (path / rel).is_file()), None)
    if variables_path is None:
        raise ThemeValidationError("missing variables.json (top-level or under styles/)")
    variables, error = _read_json_capped(variables_path, THEME_FILE_CAPS["variables"])
    if error:
        raise ThemeValidationError(error)
    if not isinstance(variables, dict):
        raise ThemeValidationError("variables.json must be a JSON object")
    theme_data = {"name": name.strip()[:THEME_NAME_MAX_LEN], "emoji": emoji, "dark": variables.get("dark", {}), "light": variables.get("light", {})}
    problem = validate_theme_data(theme_data)
    if problem:
        raise ThemeValidationError(problem)
    raw_slug = manifest.get("slug")
    slug = slugify_theme_name(raw_slug if isinstance(raw_slug, str) and raw_slug.strip() else name)
    return ThemeSummary(
        slug=slug,
        name=theme_data["name"],
        emoji=emoji.strip()[:THEME_EMOJI_MAX_LEN] or THEME_DEFAULT_EMOJI,
        level=level,
        dark=strip_to_allowed_vars(variables.get("dark", {})),
        light=strip_to_allowed_vars(variables.get("light", {})),
        files=files,
        unchecked=sorted(set(unchecked)),
    )


def pack_files(source: Path) -> list[str]:
    """The relative files the host's install copies: regular files, meta names skipped, links refused."""
    source = Path(source)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                raise ThemeValidationError(f"refusing to install symlinked directory: {os.path.relpath(os.path.join(dirpath, name), source)}")
        dirnames[:] = [d for d in dirnames if d.lower() not in THEME_META_IGNORE]
        for name in filenames:
            if name.lower() in THEME_META_IGNORE:
                continue
            full = os.path.join(dirpath, name)
            if not stat.S_ISREG(os.lstat(full).st_mode):
                raise ThemeValidationError(f"refusing to install non-regular file: {os.path.relpath(full, source)}")
            found.append(Path(os.path.relpath(full, source)).as_posix())
    return sorted(found)


def direct_write_theme(source: Path, themes_home: Path) -> tuple[Path, ThemeSummary]:
    """Install a pack the way the host's route does, without the route (Requirement 2.1, 2.8).

    Stage-first, exactly like ``_install_theme`` (L458–524): copy the pack's files
    byte for byte into a private staging directory under ``themes_home``, validate
    the staged snapshot, then rename it over ``<themes_home>/<slug>/`` (an existing
    installed directory is replaced — the update path; an editor-created
    ``<slug>.json`` record is a hard collision). Returns the installed directory
    and the summary the host would have registered.
    """
    source = Path(source)
    themes_home = Path(themes_home)
    themes_home.mkdir(parents=True, exist_ok=True)
    resolved_home = themes_home.resolve()
    resolved_source = source.resolve()
    if resolved_source == resolved_home or resolved_source in resolved_home.parents:
        raise ThemeValidationError("source directory must not contain the themes directory")
    token = uuid.uuid4().hex[:12]
    stage = themes_home / f".install-staging-{token}"
    try:
        budget = max(THEME_TOTAL_BYTES_BY_LEVEL.values())
        copied = 0
        for rel in pack_files(source):
            data = (source / rel).read_bytes()
            copied += len(data)
            if copied > budget:
                raise ThemeValidationError(f"theme exceeds the maximum install size ({budget} bytes) at: {rel}")
            destination = stage / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)  # a plain byte write, like the host's (handlers/themes.py L376–378)
        summary = validate_theme_dir(stage)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    destination_dir = themes_home / summary.slug
    if (themes_home / f"{summary.slug}.json").exists():
        shutil.rmtree(stage, ignore_errors=True)
        raise ThemeValidationError(f"a custom theme named '{summary.slug}' already exists")
    old = themes_home / f".{summary.slug}.old-{token}"
    try:
        if destination_dir.exists():
            destination_dir.replace(old)
        stage.replace(destination_dir)
    except OSError:
        if not destination_dir.exists() and old.exists():
            old.rename(destination_dir)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    return destination_dir, summary




# --- the dashboard's custom-pack CSS generator, ported (task 5.4/5.5) ------------------------
#
# The SPA builds a pack's CSS at runtime (``useTheme`` chunk: ``ne(slug, variables)``
# with the derived-token helper ``y()``, the colour mixer ``te()`` and the WCAG
# luminance ``v()`` from ``iconContrast``) into ``<style id="mc-custom-theme-<slug>">``
# — after auth, after React mounted. Baking the same string into ``index.html``
# (a boot part's ``css``) makes it present at first paint for every client state.
# This is a byte-exact port of the 0.7.0.5 generator; the boot script's drift
# guard and the Playwright parity test compare it with the host's own output.

_THEME_CSS_VALUE_OK_RE = re.compile(r"^[a-zA-Z0-9#(),.\- %/]+$")
_THEME_CSS_VALUE_BAD_RE = re.compile(r"url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(|paint\s*\(|element\s*\(", re.IGNORECASE)
_THEME_CODE_TOKENS: tuple[tuple[str, str, str], ...] = (
    # key, value on a bright --bg, value on a dark --bg
    ("--json-key", "#001080", "#9CDCFE"),
    ("--json-str", "#A31515", "#CE9178"),
    ("--json-num", "#098658", "#B5CEA8"),
    ("--json-bool", "#0000FF", "#569CD6"),
    ("--diff-add", "rgba(22,163,74,.12)", "rgba(46,160,67,.15)"),
    ("--diff-add-text", "#1a7f37", "#7ee787"),
    ("--diff-del", "rgba(220,38,38,.12)", "rgba(248,81,73,.15)"),
    ("--diff-del-text", "#cf222e", "#ffa198"),
    ("--diff-hunk", "rgba(4,117,88,.12)", "rgba(4,117,88,.2)"),
    ("--diff-hunk-text", "#065f46", "#6ee7b7"),
    ("--diff-meta-text", "#1f2328", "#e6edf3"),
)
_THEME_MIX_TOKENS: tuple[tuple[str, int], ...] = (
    ("--card", 4), ("--bg-elevated", 6), ("--chrome", 6), ("--bg-accent", 8), ("--panel", 8), ("--bg-hover", 10), ("--card-hl", 10),
    ("--panel-strong", 12), ("--border", 14), ("--border-strong", 22), ("--border-hover", 30), ("--muted", 75), ("--muted-strong", 85),
)
_THEME_FG_PAIRS: tuple[tuple[str, str], ...] = (
    ("--accent-fg", "--accent"), ("--ok-fg", "--ok"), ("--warn-fg", "--warn"), ("--danger-fg", "--danger"), ("--info-fg", "--info"), ("--aim-fg", "--aim"),
)
_THEME_FONT_TAIL = (
    "--font-body:var(--theme-font-sans, var(--script-fallbacks),'Space Grotesk',-apple-system,BlinkMacSystemFont,sans-serif);"
    "--mono:var(--theme-font-mono, var(--script-fallbacks-mono),'JetBrains Mono',ui-monospace,SFMono-Regular,monospace);"
    "--radius-sm:6px;--radius-md:8px;--radius-lg:12px;--radius-xl:16px;"
)
_RGB_FN_RE = re.compile(r"^rgba?\(([^)]*)\)$", re.IGNORECASE)
_SRGB_FN_RE = re.compile(r"^color\(\s*srgb\s+([^)]*)\)$", re.IGNORECASE)
_HEX_RE = re.compile(r"^#([0-9a-f]{3,8})$", re.IGNORECASE)


def _js_round(value: float) -> int:
    """JavaScript ``Math.round`` (half toward +infinity), not Python's banker's rounding."""
    return math.floor(value + 0.5)


def _generator_sanitize(value: Any) -> str:
    """The generator's ``p()``: a CSS value or ``""``."""
    if not isinstance(value, str) or len(value) > 200:
        return ""
    trimmed = value.strip()
    if not trimmed or not _THEME_CSS_VALUE_OK_RE.match(trimmed) or _THEME_CSS_VALUE_BAD_RE.search(trimmed):
        return ""
    return trimmed


def _parse_color(value: str) -> dict[str, float] | None:
    """``iconContrast`` ``d()``: ``{r, g, b, a}`` or ``None``."""
    text = value.strip().lower()
    if not text:
        return None
    if text == "transparent":
        return {"r": 0, "g": 0, "b": 0, "a": 0}

    def numbers(source: str) -> list[float]:
        out: list[float] = []
        for token in re.split(r"[\s,/]+", source):
            if token:
                try:
                    out.append(float(token))
                except ValueError:
                    return [float("nan")]
        return out

    match = _RGB_FN_RE.match(text)
    if match:
        parts = numbers(match.group(1))
        if len(parts) < 3 or any(p != p for p in parts):
            return None
        return {"r": parts[0], "g": parts[1], "b": parts[2], "a": parts[3] if len(parts) > 3 else 1}
    match = _SRGB_FN_RE.match(text)
    if match:
        parts = numbers(match.group(1))
        if len(parts) < 3 or any(p != p for p in parts):
            return None
        return {"r": parts[0] * 255, "g": parts[1] * 255, "b": parts[2] * 255, "a": parts[3] if len(parts) > 3 else 1}
    match = _HEX_RE.match(text)
    if match:
        digits = match.group(1)
        long_form = len(digits) > 4
        step = 2 if long_form else 1
        components: list[int] = []
        index = 0
        while index + step <= len(digits):
            chunk = digits[index : index + step]
            components.append(int(chunk if long_form else chunk + chunk, 16))
            index += step
        if len(components) < 3:
            return None
        return {"r": components[0], "g": components[1], "b": components[2], "a": components[3] / 255 if len(components) > 3 else 1}
    return None


def _opaque_rgb(value: str) -> dict[str, float] | None:
    color = _parse_color(value)
    if not color or color["a"] < 1:
        return None
    return {k: min(255, max(0, color[k])) for k in ("r", "g", "b")}


def _luminance(value: str) -> float | None:
    color = _opaque_rgb(value)
    if not color:
        return None

    def linear(channel: float) -> float:
        t = channel / 255
        return t / 12.92 if t <= 0.04045 else ((t + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(color["r"]) + 0.7152 * linear(color["g"]) + 0.0722 * linear(color["b"])


def _mix(foreground: str, background: str, percent: float) -> str | None:
    fg, bg = _opaque_rgb(foreground), _opaque_rgb(background)
    if not fg or not bg:
        return None
    k = percent / 100

    def channel(a: float, b: float) -> str:
        return format(_js_round(a * k + b * (1 - k)), "02x")

    return f"#{channel(fg['r'], bg['r'])}{channel(fg['g'], bg['g'])}{channel(fg['b'], bg['b'])}"


def _derived_tokens(block: dict[str, Any]) -> str:
    """The generator's ``y()``: tokens the pack did not declare, derived from ``--bg`` / ``--text`` / the accents."""

    def declared(key: str) -> bool:
        return isinstance(block.get(key), str) and _generator_sanitize(block[key]) != ""

    out: list[str] = []

    def put(key: str, value: str | None) -> None:
        if value and not declared(key):
            out.append(f"{key}:{value}")

    bg = _generator_sanitize(block.get("--bg", ""))
    text = _generator_sanitize(block.get("--text", ""))
    bg_ok = bg if _opaque_rgb(bg) else ""
    text_ok = text if _opaque_rgb(text) else ""
    for key, percent in _THEME_MIX_TOKENS:
        put(key, _mix(text, bg, percent))
    put("--text-strong", text_ok or None)
    put("--card-fg", text_ok or None)
    put("--muted-fg", (text_ok and bg_ok) or None)
    for fg_key, base_key in _THEME_FG_PAIRS:
        if declared(fg_key) or not declared(base_key):
            continue
        lum = _luminance(_generator_sanitize(block[base_key]))
        if lum is not None:
            out.append(f"{fg_key}:{'#000' if lum > 0.179 else '#fff'}")
    lum = _luminance(bg) if text_ok else None
    if lum is not None:
        bright = lum > 0.179
        for key, on_bright, on_dark in _THEME_CODE_TOKENS:
            put(key, on_bright if bright else on_dark)
    return ";".join(out) + ";" if out else ""


def theme_css(slug: str, variables: dict[str, Any]) -> str:
    """The dashboard's generated CSS for a custom pack (``ne(slug, variables)``), scoped to ``[data-theme="custom-<slug>-dark|light"]``."""
    if not slug:
        return ""

    def declared_vars(block: dict[str, Any]) -> str:
        pairs = []
        for key, value in block.items():
            if key in THEME_CSS_VARS:
                clean = _generator_sanitize(value)
                if clean != "":
                    pairs.append(f"{key}:{clean}")
        return ";".join(pairs)

    dark = variables.get("dark") if isinstance(variables.get("dark"), dict) else {}
    light = variables.get("light") if isinstance(variables.get("light"), dict) else {}
    return (
        f'[data-theme="custom-{slug}-dark"]{{{declared_vars(dark)};{_derived_tokens(dark)}{_THEME_FONT_TAIL}color-scheme:dark;}}\n'
        f'[data-theme="custom-{slug}-light"]{{{declared_vars(light)};{_derived_tokens(light)}{_THEME_FONT_TAIL}color-scheme:light;}}'
    )


def theme_css_for_pack(pack_dir: Path) -> str:
    """``theme_css`` for an on-disk pack (``theme.json`` slug + ``variables.json``)."""
    pack_dir = Path(pack_dir)
    manifest = json.loads((pack_dir / THEME_MANIFEST_NAME).read_text(encoding="utf-8"))
    variables_path = next((pack_dir / rel for rel in THEME_VARIABLES_REL if (pack_dir / rel).is_file()), None)
    if variables_path is None:
        raise ThemeValidationError("variables.json missing")
    variables = json.loads(variables_path.read_text(encoding="utf-8"))
    slug = manifest.get("slug") if isinstance(manifest, dict) else None
    if not isinstance(slug, str):
        raise ThemeValidationError("theme.json has no slug")
    return theme_css(re.sub(r"[^a-z0-9-]", "", slug), variables)
