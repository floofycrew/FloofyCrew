"""Kind ``theme`` (Requirement 2.1, 2.8): the host's theme route, or the byte-equivalent direct write.

A theme part names ``theme/theme.json``; the pack is that file's directory. The
handler installs it into ``<host home>/themes/<slug>/`` **through the host's own
validator** when it can — ``POST /api/themes/install`` with
``{"source": {"type": "local", "path": <pack dir>}}`` (``dashboard/handlers/
themes.py`` L571 ``api_themes_install``; the route copies the pack into a private
staging snapshot, validates, and renames it into place, L440–L535) — and
otherwise does exactly what the route does without the route:
:func:`floofy_core.themes.direct_write_theme`. "Otherwise" is

* no gateway running (the route needs a live gateway), or
* the route answering ``403`` because ``capabilities.theme_install`` is denied
  by the host's policy (L580–L605). That is a **governance warning**
  (``ThemeInstallClosed``) attached to the outcome, never a refusal (DR-5,
  Requirement 11.2).

The pack's level (0–2), caps and CSS allowlist are enforced by the ported
validator either way. Uninstall goes through ``DELETE /api/themes/{slug}``
(L640–L680) when a gateway runs, else removes the directory. The route refuses
a symlinked source root, so a ``floofy dev`` link is resolved first.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..governance import warnings_for
from ..themes import ThemeValidationError, direct_write_theme, validate_theme_dir
from . import SEAMS, KindContext, PartOutcome

__all__ = ["ThemeHandler"]

THEMES_DIR = "themes"


@dataclass
class ThemeHandler:
    kind: str = "theme"
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS["theme"]

    def _pack_dir(self, mod_dir: Path, part: dict[str, Any]) -> Path:
        return (Path(mod_dir) / str(part.get("path", "theme/theme.json"))).resolve().parent

    def _themes_home(self, ctx: KindContext) -> Path:
        return Path(ctx.host_home) / THEMES_DIR

    def _governance(self, ctx: KindContext, mod_id: str) -> list[dict[str, Any]]:
        if ctx.governance is None:
            return []
        return [w.to_dict() for w in warnings_for(ctx.governance, theme_targets=[mod_id]) if w.code == "ThemeInstallClosed"]

    def _route_open(self, ctx: KindContext) -> bool:
        return ctx.session is not None and not ctx.in_gateway and (ctx.governance is None or ctx.governance.theme_install_enabled is not False)

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        pack = self._pack_dir(mod_dir, part)
        outcome = PartOutcome(self.kind, index, self.seam, False, governance=self._governance(ctx, mod_id))
        try:
            summary = validate_theme_dir(pack)
        except ThemeValidationError as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", f"theme pack refused by the host's validation rules: {exc}"
            return outcome
        outcome.extra["slug"] = summary.slug
        outcome.extra["level"] = summary.level
        if self._route_open(ctx):
            reply = None
            try:
                reply = ctx.session.post("/api/themes/install", {"source": {"type": "local", "path": str(pack)}})
            except Exception as exc:  # noqa: BLE001 - fall back to the direct write
                ctx.warn(f"theme route unreachable ({type(exc).__name__}: {exc}); writing the pack directly")
            if reply is not None and reply.status == 200:
                outcome.status = "installed"
                outcome.detail = f"installed through POST /api/themes/install as {summary.slug} (level {summary.level})"
                outcome.files = [str(self._themes_home(ctx) / summary.slug)]
                outcome.extra["via"] = "route"
                ctx.record("theme-install", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
                return outcome
            if reply is not None and reply.status == 403:
                message = "the host's theme route answered 403 (capabilities.theme_install denied): the pack is written directly with the same validation rules applied locally (Requirement 2.8)"
                if not any(g["code"] == "ThemeInstallClosed" for g in outcome.governance):
                    outcome.governance.append({"code": "ThemeInstallClosed", "message": message, "affectedTargets": [mod_id]})
                ctx.warn("governance ThemeInstallClosed: " + message)
            elif reply is not None:
                ctx.warn(f"theme route answered {reply.status}: {reply.body[:200].decode('utf-8', 'replace')}; writing the pack directly")
        elif ctx.governance is not None and ctx.governance.theme_install_enabled is False:
            ctx.warn("governance ThemeInstallClosed: capabilities.theme_install is denied by the host's policy; the pack is written directly (Requirement 2.8)")
        try:
            installed_dir, summary = direct_write_theme(pack, self._themes_home(ctx))
        except ThemeValidationError as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", f"direct theme write refused: {exc}"
            return outcome
        outcome.status = "installed"
        outcome.detail = f"written directly into {installed_dir} (byte-equivalent to the host route; level {summary.level})"
        outcome.files = [str(installed_dir)]
        outcome.extra["via"] = "direct-write"
        ctx.record("theme-install", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail, governanceFlags=[g["code"] for g in outcome.governance])
        return outcome

    def _slug(self, mod_dir: Path, part: dict[str, Any]) -> str | None:
        try:
            return validate_theme_dir(self._pack_dir(mod_dir, part)).slug
        except ThemeValidationError:
            return None

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, status="removed")
        slug = self._slug(mod_dir, part)
        if slug is None:
            outcome.status, outcome.detail = "skipped", "cannot derive the theme slug from the pack; nothing removed"
            return outcome
        target = self._themes_home(ctx) / slug
        if ctx.session is not None and not ctx.in_gateway:
            try:
                reply = ctx.session.request("DELETE", f"/api/themes/{slug}")
                if reply.status in (200, 404):
                    outcome.detail = f"DELETE /api/themes/{slug} -> {reply.status}"
                    outcome.files = [str(target)]
                    ctx.record("theme-uninstall", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
                    return outcome
            except Exception:  # noqa: BLE001 - fall back to the direct removal
                pass
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            outcome.detail = f"removed {target}"
            outcome.files = [str(target)]
        else:
            outcome.status, outcome.detail = "absent", f"{target} was not installed"
        ctx.record("theme-uninstall", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
        return outcome

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, governance=self._governance(ctx, mod_id))
        slug = self._slug(mod_dir, part)
        if slug is None:
            outcome.status, outcome.detail = "error", "the pack does not validate"
            return outcome
        target = self._themes_home(ctx) / slug
        outcome.extra["slug"] = slug
        if target.is_dir():
            outcome.status, outcome.detail, outcome.files = "present", f"installed at {target}", [str(target)]
        else:
            outcome.status, outcome.detail = "absent", f"not installed ({target} missing)"
        return outcome
