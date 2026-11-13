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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..governance import warnings_for
from ..hostcli import grant_app_trust, run_host_cli
from . import SEAMS, KindContext, PartOutcome

__all__ = ["AppHandler"]


def _agent_config(config_path: Path) -> dict[str, Any]:
    """The ``agent`` block of the host's ``config.json`` as it is on disk right now (``{}`` when unreadable)."""
    try:
        doc = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    agent = doc.get("agent") if isinstance(doc, dict) else None
    return agent if isinstance(agent, dict) else {}


def _granted(config_path: Path, name: str) -> bool:
    """Is ``name`` in the operator-writable per-app grant, read fresh from disk?"""
    agent = _agent_config(config_path)
    trusted = agent.get("apps_trusted")
    return isinstance(trusted, list) and name in trusted


def _allowed_now(config_path: Path, name: str, snapshot: Any) -> bool | None:
    """The App Kit verdict for ``name`` against the config on disk (the snapshot may predate a host CLI edit)."""
    agent = _agent_config(config_path)
    third_party = agent.get("apps_allow_third_party")
    trusted = agent.get("apps_trusted") if isinstance(agent.get("apps_trusted"), list) else []
    local = agent.get("apps_trusted_local") if isinstance(agent.get("apps_trusted_local"), list) else []
    if third_party is None and not trusted and not local:
        # nothing on disk says anything: fall back to the snapshot's (policy-aware) reading
        return snapshot.app_execution_allowed(name) if snapshot is not None else None
    if third_party:
        return True
    return name in trusted or name in local


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
        """Run a host verb through the launcher, else the route; returns (ok, output).

        The launcher is used from INSIDE the gateway too (the manager App's install
        path): ``kirocrew app install|enable|uninstall`` is a separate process doing
        file work under ``<host home>/apps/`` (``cli_commands.py`` ``_handle_app``
        calls ``install_app``/``enable_app`` directly, never the gateway), and a
        running gateway reconciles the on-disk change itself (``apps/hook_reconcile.py``).
        Only the HTTP route is off-limits in-gateway — the manager's handler runs on
        the gateway's own executor and a loopback request would wait on itself.
        Measured 2026-09-26 on 0.7.0.5: with the launcher refused in-gateway every
        mod installed from the FloofyCrew App landed with its app part silently
        missing ("App Not Found") while the install itself reported ok.
        """
        if ctx.launcher is not None:
            result = run_host_cli(ctx.launcher, args, host_home=ctx.host_home, extra_env=ctx.host_cli_env)
            return result.ok, result.output
        if route is not None and ctx.session is not None and not ctx.in_gateway:
            method, path, body = route
            try:
                reply = ctx.session.request(method, path, body)
            except Exception as exc:  # noqa: BLE001
                return False, f"{path}: {type(exc).__name__}: {exc}"
            return reply.status == 200, reply.body[:300].decode("utf-8", "replace")
        if ctx.in_gateway:
            return False, "no kirocrew launcher found from inside the gateway (not on PATH, no bin/kirocrew in the running payload)"
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
        config_path = Path(ctx.host_home) / "config.json"
        granted_before = _granted(config_path, name)
        if self._installed_meta(ctx, name) is not None:
            ok, output = self._host(ctx, ["app", "uninstall", name], ("POST", f"/api/apps/{name}/uninstall", {}))
            outcome.extra["refresh"] = {"uninstall": ok, "output": output[-200:]}
            # `kirocrew app uninstall` also drops the app from agent.apps_trusted (apps/manager.py
            # "Remove *name* from agent.apps_trusted"; seen on every mod update on a live 0.7.0.5:
            # the refreshed app came back ungranted and `app enable` was refused while this handler,
            # judging from the snapshot taken BEFORE the uninstall, reported "grant: not-needed").
            # Restoring a grant the user already gave is not a new decision: re-record it without
            # asking, audited as a restore — the same rule `floofy init` applies to the Loader.
            if granted_before and not _granted(config_path, name):
                restored = grant_app_trust(config_path, name)
                outcome.extra["grantRestored"] = restored.to_dict()
                ctx.record("grant-apps-trusted", mod=mod_id, files=[str(restored.path)], result="restored" if restored.written else "unchanged", detail=f"re-recorded after `kirocrew app uninstall` dropped it: {restored.detail}", governanceFlags=["ThirdPartyAppsDisabled"])
                ctx.say(f"grant: restored — `kirocrew app uninstall` had dropped {name!r} from agent.apps_trusted; {restored.detail}")
        ok, output = self._host(ctx, ["app", "install", str(app_dir)], ("POST", "/api/apps/install", {"source": str(app_dir)}))
        if not ok:
            outcome.ok, outcome.status, outcome.detail = False, "error", f"the host refused to install app {name!r}: {output[-300:]}"
            return outcome
        outcome.files = [str(Path(ctx.host_home) / "apps" / name)]
        # Judge from the config as it is NOW (the host CLI may have edited it above), not from
        # the snapshot the command started with.
        allowed = _allowed_now(config_path, name, ctx.governance)
        grant = "restored" if "grantRestored" in outcome.extra else "not-needed"
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
        # The enable can be refused TRANSIENTLY: a `floofy install --now` reloads the
        # Loader in the running gateway at the same moment, and the host refuses app
        # lifecycle ops while a startup hook / reconcile is in flight (observed live:
        # the identical `kirocrew app enable` succeeded by hand seconds later, with
        # both grants already on disk). Distinguish that from a real trust refusal by
        # retrying briefly before reporting.
        enabled_ok, enable_output = self._host(ctx, ["app", "enable", name], ("POST", f"/api/apps/{name}/enable", {}))
        for _attempt in range(3):
            if enabled_ok:
                break
            time.sleep(1.5)
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
