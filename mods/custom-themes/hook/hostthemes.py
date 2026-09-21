"""Harvest the running host's built-in themes as presets — from the host's own stylesheet, at runtime.

The dashboard bundle carries every built-in theme as ``[data-theme=<slug>-dark]`` /
``[data-theme=<slug>-light]`` blocks in ``static/dist/assets/*.css`` (the stock
palettes in the ``src-*`` sheet, an edition's own themes — the ones its sidebar
lists with an emoji — in its ``main-*`` sheet), plus
the theme's decorative rules (``[data-theme=lumon-dark] body:after{…}``) and their
``@keyframes``. Reading them from the payload at runtime, instead of shipping a
transcription, keeps the mod edition-neutral (a preset exists exactly when the host
has that theme), current across host builds (the block shape is a CSS fact, not a
minified identifier), and free of copied theme data.

What a preset carries: the palette per mode (only allowlisted variables, so the
result installs through the host's validator), the theme's scoped decorative rules
rewritten into the mod's **unscoped** extra-CSS dialect (``@floofy-mode dark { … }``
when a rule is mode-specific), the ``@keyframes`` those rules reference, and the
label/emoji the sidebar shows (found in the JS chunks as ``{value:`<slug>`,label:`…`}``;
the slug title-cased when not found). Decorations driven by the host's own scripts
(elements the edition mounts only while its theme is active) cannot carry over and
are not pretended to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.themes import THEME_CSS_VARS

from .cssc import MODES, iter_rules

__all__ = ["HostTheme", "harvest", "harvest_css", "harvest_labels", "read_host_css"]

_THEME_SEL_RE = re.compile(r"^(?:html)?\[data-theme=(?:\"|')?([a-z0-9-]+?)-(dark|light)(?:\"|')?\](.*)$", re.S)
_LABEL_RE = re.compile(r"\{value:(?P<q>[`'\"])(?P<slug>[a-z0-9-]+)(?P=q),label:(?P<q2>[`'\"])(?P<label>[^`'\"]{1,60})(?P=q2)")
_EMOJI_SPLIT_RE = re.compile(r"^(\S+)\s+(.+)$")
_ANIM_RE = re.compile(r"animation(?:-name)?\s*:\s*([^;]+)", re.I)
_KEYFRAMES_RE = re.compile(r"^@(?:-webkit-)?keyframes\s+([A-Za-z0-9_-]+)\s*$")
MIN_VARS_FOR_A_THEME = 3


@dataclass
class HostTheme:
    slug: str
    label: str = ""
    emoji: str = ""
    dark: dict[str, str] = field(default_factory=dict)
    light: dict[str, str] = field(default_factory=dict)
    extra_css: str = ""
    decorated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "label": self.label or self.slug.replace("-", " ").title(),
            "emoji": self.emoji,
            "dark": dict(self.dark),
            "light": dict(self.light),
            "extraCss": self.extra_css,
            "decorated": self.decorated,
            "variables": len(self.dark) + len(self.light),
        }


def read_host_css(payload_root: Path | None) -> str:
    """The concatenated text of every ``assets/main-*.css`` of the payload ('' when there is none)."""
    if payload_root is None:
        return ""
    assets = Path(payload_root) / "kiro_crew" / "static" / "dist" / "assets"
    if not assets.is_dir():
        assets = Path(payload_root) / "static" / "dist" / "assets"
    if not assets.is_dir():
        return ""
    chunks = []
    for path in sorted(assets.glob("*.css")):
        if path.name.endswith((".br", ".gz")):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def _split_decls(body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    depth = 0
    current: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c in "'\"":
            j = i + 1
            while j < len(body) and body[j] != c:
                j += 2 if body[j] == "\\" else 1
            current.append(body[i : j + 1])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == ";" and depth == 0:
            text = "".join(current)
            current = []
            name, _, value = text.partition(":")
            if _ and name.strip():
                out.append((name.strip(), value.strip()))
        else:
            current.append(c)
        i += 1
    text = "".join(current)
    name, sep, value = text.partition(":")
    if sep and name.strip():
        out.append((name.strip(), value.strip()))
    return out


def _selectors(prelude: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for c in prelude:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(c)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def harvest_css(css: str) -> dict[str, HostTheme]:
    """Every built-in theme in the stylesheet text: palettes, decorative rules, referenced keyframes."""
    themes: dict[str, HostTheme] = {}
    # decorative rules: slug -> mode -> [ (unscoped selector list, body) ]; None mode = both
    decor: dict[str, list[tuple[frozenset[str], str, str]]] = {}
    keyframes: dict[str, str] = {}

    def theme(slug: str) -> HostTheme:
        return themes.setdefault(slug, HostTheme(slug))

    for prelude, body in iter_rules(css):
        if body is None:
            continue
        kf = _KEYFRAMES_RE.match(prelude)
        if kf:
            keyframes[kf.group(1)] = f"{prelude}{{{body}}}"
            continue
        if prelude.startswith("@media"):
            # mode-agnostic media wrappers over scoped rules: keep the inner scoped rules
            inner = harvest_css(body)
            for slug, found in inner.items():
                if found.extra_css:
                    decor.setdefault(slug, []).append((frozenset(MODES), f"@media-wrap:{prelude}", found.extra_css))
            continue
        if "[data-theme=" not in prelude:
            continue
        by_theme: dict[str, dict[str, list[str]]] = {}  # slug -> rest -> modes
        for selector in _selectors(prelude):
            match = _THEME_SEL_RE.match(selector)
            if not match:
                continue
            slug, mode, rest = match.group(1), match.group(2), match.group(3).strip()
            by_theme.setdefault(slug, {}).setdefault(rest, []).append(mode)
        for slug, rests in by_theme.items():
            for rest, modes in rests.items():
                if rest == "":
                    variables = {n: v for n, v in _split_decls(body) if n in THEME_CSS_VARS}
                    if not variables:
                        continue
                    for mode in modes:
                        getattr(theme(slug), mode).update(variables)
                else:
                    decor.setdefault(slug, []).append((frozenset(modes), rest, body.strip()))

    for slug, found in themes.items():
        if len(found.dark) < MIN_VARS_FOR_A_THEME and len(found.light) < MIN_VARS_FOR_A_THEME:
            continue
        if found.dark and not found.light:
            found.light = dict(found.dark)
        elif found.light and not found.dark:
            found.dark = dict(found.light)
        rules = decor.get(slug) or []
        if rules:
            found.extra_css = _render_extra(rules, keyframes)
            found.decorated = True
    return {slug: t for slug, t in themes.items() if len(t.dark) >= MIN_VARS_FOR_A_THEME}


def _render_extra(rules: list[tuple[frozenset[str], str, str]], keyframes: dict[str, str]) -> str:
    both: list[str] = []
    per_mode: dict[str, list[str]] = {"dark": [], "light": []}
    referenced: set[str] = set()
    for modes, selector, body in rules:
        for match in _ANIM_RE.finditer(body):
            for token in re.split(r"[\s,]+", match.group(1)):
                if token in keyframes:
                    referenced.add(token)
        if selector.startswith("@media-wrap:"):
            text = f"{selector[len('@media-wrap:'):]}{{{body}}}"
        else:
            text = f"{selector}{{{body}}}"
        if modes == frozenset(MODES):
            both.append(text)
        else:
            for mode in sorted(modes):
                per_mode[mode].append(text)
    out: list[str] = []
    for name in sorted(referenced):
        out.append(keyframes[name])
    out.extend(both)
    for mode in MODES:
        if per_mode[mode]:
            out.append(f"@floofy-mode {mode} {{\n" + "\n".join(per_mode[mode]) + "\n}")
    return "\n".join(out)


def harvest_labels(payload_root: Path | None) -> dict[str, tuple[str, str]]:
    """``slug -> (emoji, label)`` from the ``{value, label}`` registrations in the JS chunks."""
    labels: dict[str, tuple[str, str]] = {}
    if payload_root is None:
        return labels
    assets = Path(payload_root) / "kiro_crew" / "static" / "dist" / "assets"
    if not assets.is_dir():
        assets = Path(payload_root) / "static" / "dist" / "assets"
    if not assets.is_dir():
        return labels
    for path in sorted(assets.glob("*.js")):
        if path.name.endswith((".map",)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "label:" not in text:
            continue
        for match in _LABEL_RE.finditer(text):
            slug, label = match.group("slug"), match.group("label").strip()
            split = _EMOJI_SPLIT_RE.match(label)
            if split and not split.group(1)[0].isalnum():
                labels.setdefault(slug, (split.group(1), split.group(2)))
            else:
                labels.setdefault(slug, ("", label))
    return labels


def harvest(payload_root: Path | None) -> list[HostTheme]:
    """The host's built-in themes, labelled, sorted by label."""
    found = harvest_css(read_host_css(payload_root))
    labels = harvest_labels(payload_root)
    for slug, theme in found.items():
        emoji, label = labels.get(slug, ("", ""))
        theme.emoji, theme.label = emoji, label
    return sorted(found.values(), key=lambda t: (t.label or t.slug).lower())
