"""Kind ``app`` (Requirement 2.3): the host App Kit install plus the offered ``agent.apps_trusted`` grant.

The part names ``…/app.json``; the app is that file's directory. Install goes
through the host's own seam — ``kirocrew app install <dir>`` (``kiro_crew/cli.py``
L2784; ``apps/manager.py`` L578 ``install_app`` copies the tree into
``<host home>/apps/<name>/``) or ``POST /api/apps/install {"source": <dir>}``
(``apps/routes.py`` L616 ``handle_install_app``) when only a gateway is
reachable. A fresh install lands ``enabled: false``, and the host's execution
gate refuses ``kirocrew app enable`` for a third-party app until the operator
granted it (``apps/execution.py`` L623–L686; measured in design "Spike outcomes"
1.3 — even a UI-only app needs the grant). FloofyCrew treats that verdict as
**information**: it is shown as a warning, and the manager **offers** to record
the per-app ``agent.apps_trusted`` grant (an operator-writable config key), with
the user's confirmation, then enables the app (DR-5, Requirement 2.3, 11.2). An
already-installed app is refreshed through ``uninstall`` (data kept) + ``install``.
Uninstall is ``kirocrew app uninstall <name>`` / ``POST /api/apps/{name}/uninstall``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..governance import warnings_for
from ..hostcli import grant_app_trust, run_host_cli
from . import SEAMS, KindContext, PartOutcome

__all__ = ["AppHandler"]


@dataclass
class AppHandler:
    kind: str = "app"
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS["app"]

    def _app(self, mod_dir: Path, part: dict[str, Any]) -> tuple[Path, str]:
        manifest_path = (Path(mod_dir) / str(part.get("path", ""))).resolve()
        if not manifest_path.is_file():
            raise ValueError(f"app manifest missing: {part.get('path')}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("app.json has no name")
        return manifest_path.parent, name

    def _installed_meta(self, ctx: KindContext, name: str) -> dict[str, Any] | None:
        try:
            record = json.loads((Path(ctx.host_home) / "apps" / name / "installed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def _host(self, ctx: KindContext, args: list[str], route: tuple[str, str, Any] | None) -> tuple[bool, str]:
        """Run a host verb through the launcher, else the route; returns (ok, output)."""
        if ctx.launcher is not None and not ctx.in_gateway:
            result = run_host_cli(ctx.launcher, args, host_home=ctx.host_home, extra_env=ctx.host_cli_env)
            return result.ok, result.output
        if route is not None and ctx.session is not None and not ctx.in_gateway:
            method, path, body = route
            try:
                reply = ctx.session.request(method, path, body)
            except Exception as exc:  # noqa: BLE001
                return False, f"{path}: {type(exc).__name__}: {exc}"
            return reply.status == 200, reply.body[:300].decode("utf-8", "replace")
        return False, "no kirocrew launcher (--kirocrew) and no reachable gateway"

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            app_dir, name = self._app(mod_dir, part)
        except (OSError, ValueError) as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", str(exc)
            return outcome
        outcome.extra["appName"] = name
        if ctx.governance is not None:
            outcome.governance = [w.to_dict() for w in warnings_for(ctx.governance, app_targets=[name]) if w.code in ("ThirdPartyAppsDisabled", "AppAdmissionEnforced", "AppAdmissionBanned")]
            for warning in outcome.governance:
                ctx.warn(f"host says {warning['code']}: {warning['message']}")
        if self._installed_meta(ctx, name) is not None:
            ok, output = self._host(ctx, ["app", "uninstall", name], ("POST", f"/api/apps/{name}/uninstall", {}))
            outcome.extra["refresh"] = {"uninstall": ok, "output": output[-200:]}
        ok, output = self._host(ctx, ["app", "install", str(app_dir)], ("POST", "/api/apps/install", {"source": str(app_dir)}))
        if not ok:
            outcome.ok, outcome.status, outcome.detail = False, "error", f"the host refused to install app {name!r}: {output[-300:]}"
            return outcome
        outcome.files = [str(Path(ctx.host_home) / "apps" / name)]
        allowed = ctx.governance.app_execution_allowed(name) if ctx.governance is not None else None
        grant = "not-needed"
        if allowed is not True:
            ctx.say(f"host verdict: the App Kit execution gate would refuse to run app {name!r} (agent.apps_allow_third_party is off and it is not in agent.apps_trusted).")
            if ctx.confirm(f"Record the per-app grant agent.apps_trusted += [\"{name}\"] in {Path(ctx.host_home) / 'config.json'}?"):
                edit = grant_app_trust(Path(ctx.host_home) / "config.json", name)
                grant = "written" if edit.written else "already-present"
                ctx.record("grant-apps-trusted", mod=mod_id, files=[str(edit.path)], result="ok" if edit.written else "unchanged", detail=edit.detail, governanceFlags=["ThirdPartyAppsDisabled"])
            else:
                grant = "declined"
                ctx.record("grant-apps-trusted", mod=mod_id, result="declined", detail=f"user declined the grant for app {name!r}")
        outcome.extra["grant"] = grant
        enabled_ok, enable_output = self._host(ctx, ["app", "enable", name], ("POST", f"/api/apps/{name}/enable", {}))
        outcome.extra["enabled"] = enabled_ok
        if enabled_ok:
            outcome.status = "installed"
            outcome.detail = f"installed through the host App Kit and enabled ({name}); grant: {grant}"
        else:
            outcome.status = "installed"
            outcome.detail = f"installed through the host App Kit; the host refused to enable it ({enable_output[-200:].strip() or 'execution policy'}) — shown as a warning, grant: {grant}"
            ctx.warn(f"app {name!r}: {outcome.detail}")
            if not any(g["code"] == "ThirdPartyAppsDisabled" for g in outcome.governance):
                outcome.governance.append({"code": "ThirdPartyAppsDisabled", "message": f"the host refused `kirocrew app enable {name}`: {enable_output[-200:].strip()}", "affectedTargets": [name]})
        ctx.record("app-install", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail, governanceFlags=[g["code"] for g in outcome.governance])
        return outcome

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, status="removed")
        try:
            _app_dir, name = self._app(mod_dir, part)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "skipped", str(exc)
            return outcome
        outcome.extra["appName"] = name
        if self._installed_meta(ctx, name) is None:
            outcome.status, outcome.detail = "absent", f"app {name!r} is not installed"
            return outcome
        ok, output = self._host(ctx, ["app", "uninstall", name], ("POST", f"/api/apps/{name}/uninstall", {}))
        if not ok:
            outcome.ok, outcome.status, outcome.detail = False, "error", f"the host refused to uninstall app {name!r}: {output[-300:]}"
            return outcome
        outcome.files = [str(Path(ctx.host_home) / "apps" / name)]
        outcome.detail = f"uninstalled through the host App Kit ({name}; app data kept by the host)"
        ctx.record("app-uninstall", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
        return outcome

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            _app_dir, name = self._app(mod_dir, part)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "error", str(exc)
            return outcome
        outcome.extra["appName"] = name
        if ctx.governance is not None:
            outcome.governance = [w.to_dict() for w in warnings_for(ctx.governance, app_targets=[name]) if name in w.affected_targets]
        meta = self._installed_meta(ctx, name)
        if meta is None:
            outcome.status, outcome.detail = "absent", f"app {name!r} is not installed"
        else:
            outcome.status = "present"
            outcome.detail = f"installed (v{meta.get('version')}, enabled={meta.get('enabled')})"
            outcome.extra["enabled"] = meta.get("enabled")
        return outcome
