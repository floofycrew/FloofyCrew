"""The custom-themes CSS compiler: a library theme → the stylesheet the dashboard receives.

Two layers make up one FloofyCrew theme:

* the **host pack** — ``theme.json`` + ``variables.json`` (+ branding, fonts,
  ``styles/overrides.css``…), installed into the host's own ``themes/`` directory
  through the host's validator, so the theme is a normal entry of Settings →
  Display and survives without FloofyCrew;
* the **extra CSS** — the part the host's runtime allowlist would drop: any
  selector, any rule. The mod scopes every rule to the theme's own
  ``[data-theme="custom-<slug>-dark|light"]`` and applies it itself (runtime part
  + first-frame boot link), so it can never leak onto another theme.

Extra CSS is written **unscoped** by the author (``.topbar-glass{…}``,
``body::before{…}``); :func:`scope_css` adds the prefixes. Two conveniences:

* ``@floofy-mode dark { … }`` / ``@floofy-mode light { … }`` scope the inner
  rules to one mode only (the host's edition themes have mode-specific decor);
* ``url(pack:branding/logo.png)`` is rewritten to the installed pack's asset
  route, so a theme may reference the files it ships.

Safety: the sheet lands in a ``<style>`` on the dashboard document, so it may
not close the element (``</style``), may not ``@import``, may not run
``expression()`` / ``javascript:`` / ``-moz-binding``, and — FloofyCrew's
encrypted-only network rule with loopback as the only exception — may not load
from another origin: ``url()`` is same-origin (``/…``), ``data:``, or ``pack:``.
The compiler refuses the sheet on any of those; a plain CSS syntax slip is not
its business (the browser drops a broken rule on its own).
"""
from __future__ import annotations

import re
from typing import Any

from floofy_core.themes import theme_css

__all__ = [
    "CssRefused",
    "compile_theme",
    "extra_css_problems",
    "iter_rules",
    "host_scope",
    "preview_css",
    "raise_specificity",
    "scope_css",
    "theme_scope",
]

MODES = ("dark", "light")
MODE_AT_RULE = "@floofy-mode"
_MODE_AT_RE = re.compile(r"^@floofy-mode\s+(dark|light)\s*$", re.I)
_DENY_RE = re.compile(r"@import|expression\s*\(|javascript:|-moz-binding|behavior\s*:|</style", re.I)
_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]*)\1\s*\)", re.I)
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.S)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_EXTRA_CSS_BYTES = 256 * 1024


class CssRefused(ValueError):
    """The extra CSS breaks a safety rule (a message the editor shows verbatim)."""


def host_scope(slug: str, mode: str) -> str:
    """The selector the host's own generated CSS uses for a custom pack in one mode."""
    return f'[data-theme="custom-{slug}-{mode}"]'


def theme_scope(slug: str, mode: str) -> str:
    """The scope the compiler emits: ``html[data-theme="custom-<slug>-<mode>"]``.

    One type selector more than the host's ``[data-theme="…"]`` (0,1,1 against 0,1,0): the
    sheet then wins over the host's ``:root`` / ``[data-theme=…]`` tokens wherever it sits in
    the document — the boot hook writes it at the TOP of ``<head>``, before the host's own
    stylesheets, where an equal-specificity rule would lose on source order.
    """
    return f'html{host_scope(slug, mode)}'


# --- scanning ---------------------------------------------------------------------


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return i


def _block_end(text: str, start: int) -> int:
    """Index just past the ``}`` closing the block opened at ``text[start] == '{'``."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c in "'\"":
            i = _skip_string(text, i)
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def iter_rules(text: str):
    """Yield ``(prelude, body)`` for every top-level rule; ``body`` excludes the braces.

    A statement at-rule (``@charset …;``) is yielded with ``body=None``.
    """
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        j = i
        while j < n and text[j] not in "{;":
            if text[j] in "'\"":
                j = _skip_string(text, j)
                continue
            j += 1
        if j >= n:
            return
        if text[j] == ";":
            yield text[i:j].strip(), None
            i = j + 1
            continue
        end = _block_end(text, j)
        yield text[i:j].strip(), text[j + 1 : end - 1]
        i = end


def _normalized(text: str) -> str:
    """What the browser tokenizes: comments stripped, ``\\``-escapes decoded."""
    stripped = _COMMENT_RE.sub("", text)

    def decode(match: re.Match[str]) -> str:
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return match.group(2)

    return _ESCAPE_RE.sub(decode, stripped)


def extra_css_problems(text: str) -> list[str]:
    """Every safety rule the sheet breaks (empty when it may be applied)."""
    problems: list[str] = []
    if len(text.encode("utf-8")) > MAX_EXTRA_CSS_BYTES:
        problems.append(f"extra CSS is {len(text.encode('utf-8'))} bytes; the cap is {MAX_EXTRA_CSS_BYTES}")
    normalized = _normalized(text)
    hit = _DENY_RE.search(normalized)
    if hit:
        problems.append(f"extra CSS uses a forbidden construct: {hit.group(0).strip()!r}")
    for match in _URL_RE.finditer(normalized):
        target = match.group(2).strip()
        lowered = target.lower()
        if lowered.startswith(("data:", "pack:")) or (target.startswith("/") and not target.startswith("//")):
            continue
        if lowered.startswith("#"):
            continue
        problems.append(f"extra CSS loads from another origin: url({target!r}) — only same-origin (/…), data: and pack: URLs are allowed")
    return problems


# --- scoping --------------------------------------------------------------------------


def _split_selectors(prelude: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    i = 0
    while i < len(prelude):
        c = prelude[i]
        if c in "'\"":
            end = _skip_string(prelude, i)
            current.append(prelude[i:end])
            i = end
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


_ROOT_RE = re.compile(r"^(html|:root)(?=$|[\s.:#\[>+~])", re.I)
_BODY_RE = re.compile(r"^body(?=$|[\s.:#\[>+~])", re.I)


def _scope_selector(selector: str, scopes: list[str]) -> str:
    """One selector under each scope: ``html``/``:root`` fuse with the scope, everything else descends from it."""
    out: list[str] = []
    for scope in scopes:
        if _ROOT_RE.match(selector):
            out.append(scope + _ROOT_RE.sub("", selector, count=1))
        elif _BODY_RE.match(selector):
            out.append(f"{scope} {selector}")
        else:
            out.append(f"{scope} {selector}")
    return ",".join(out)


def _rewrite_urls(body: str, slug: str) -> str:
    def rewrite(match: re.Match[str]) -> str:
        target = match.group(2).strip()
        if target.lower().startswith("pack:"):
            rel = target[5:].lstrip("/")
            return f"url(/api/theme/{slug}/assets/{rel})"
        return match.group(0)

    return _URL_RE.sub(rewrite, body)


def _scope_block(text: str, scopes_for: dict[str, str], active_modes: tuple[str, ...], slug: str) -> str:
    """Scope every rule of ``text`` (already inside an at-rule or at the top) to the active modes."""
    out: list[str] = []
    for prelude, body in iter_rules(text):
        if body is None:
            continue  # a statement at-rule inside the sheet: dropped (nothing to scope)
        lowered = prelude.lower()
        mode_match = _MODE_AT_RE.match(prelude)
        if mode_match:
            mode = mode_match.group(1).lower()
            if mode in active_modes:
                out.append(_scope_block(body, scopes_for, (mode,), slug))
            continue
        if lowered.startswith(("@media", "@supports", "@container", "@layer")):
            out.append(f"{prelude}{{{_scope_block(body, scopes_for, active_modes, slug)}}}")
            continue
        if lowered.startswith(("@keyframes", "@-webkit-keyframes", "@font-face", "@property", "@font-feature-values", "@counter-style", "@page")):
            out.append(f"{prelude}{{{_rewrite_urls(body, slug)}}}")
            continue
        if lowered.startswith("@"):
            continue  # an at-rule the compiler does not know: dropped
        scopes = [scopes_for[m] for m in active_modes]
        selectors = [_scope_selector(s, scopes) for s in _split_selectors(prelude)]
        out.append(f"{','.join(selectors)}{{{_rewrite_urls(body.strip(), slug)}}}")
    return "\n".join(out)


def scope_css(slug: str, extra_css: str, *, scopes: dict[str, str] | None = None, modes: tuple[str, ...] = MODES) -> str:
    """The extra CSS with every rule scoped to the theme (``scopes`` overrides the per-mode prefix — the preview uses it)."""
    if not extra_css or not extra_css.strip():
        return ""
    problems = extra_css_problems(extra_css)
    if problems:
        raise CssRefused("; ".join(problems))
    scopes_for = scopes or {mode: theme_scope(slug, mode) for mode in MODES}
    return _scope_block(extra_css, scopes_for, tuple(modes), slug)


# --- the whole sheet ------------------------------------------------------------------


def raise_specificity(slug: str, generated: str) -> str:
    """The host's generated palette CSS with each block re-scoped to :func:`theme_scope` (values untouched)."""
    for mode in MODES:
        generated = generated.replace(host_scope(slug, mode) + "{", theme_scope(slug, mode) + "{")
    return generated


def compile_theme(slug: str, variables: dict[str, Any], extra_css: str, *, header: bool = True) -> str:
    """Variables (the host's own generated CSS, ported) + the scoped extra layer."""
    if not _SLUG_RE.match(slug or ""):
        raise CssRefused(f"{slug!r} is not a theme slug")
    parts = []
    if header:
        parts.append(f"/* floofy custom-themes: {slug} */")
    parts.append(raise_specificity(slug, theme_css(slug, variables)))
    scoped = scope_css(slug, extra_css)
    if scoped:
        parts.append(scoped)
    return "\n".join(parts) + "\n"


def preview_css(slug: str, variables: dict[str, Any], extra_css: str, mode: str) -> str:
    """The sheet for ONE mode re-scoped to ``html[data-theme]`` — it overrides whatever theme is active while the editor previews.

    ``html[data-theme]`` (specificity 0,1,1) beats the host's ``[data-theme="…"]`` (0,1,0) on
    every token, so the live document shows the draft without touching ``data-theme``.
    """
    mode = mode if mode in MODES else "dark"
    if not _SLUG_RE.match(slug or ""):
        slug = "preview"
    scope = "html[data-theme]"
    generated = theme_css(slug, variables)
    own = host_scope(slug, mode)
    other = host_scope(slug, "light" if mode == "dark" else "dark")
    kept = [line.replace(own, scope, 1) for line in generated.split("\n") if line.startswith(own) and not line.startswith(other)]
    out = ["/* floofy custom-themes preview */", *kept]
    scoped = scope_css(slug, extra_css, scopes={mode: scope, ("light" if mode == "dark" else "dark"): scope}, modes=(mode,))
    if scoped:
        out.append(scoped)
    return "\n".join(out) + "\n"
