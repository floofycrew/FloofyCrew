"""``floofy doctor`` / ``floofy status`` / ``floofy vanilla`` (Requirement 7.4, 7.1, 2.7).

``doctor`` reports, without changing anything: host edition/version/channel and
every payload (dormant ones flagged, Requirement 6.4), the Loader's state
(installed, enabled, the last published ``loader-state.json`` or the live
``/api/apps/floofycrew/health``, a ``loader-failure.json``), the early shim,
the re-apply triggers, the host's governance **as information** — what its
policy would have said and which installed mods cross it (Requirement 11.2) —
the consent record, drift per payload (the Patcher's status), the last compat
verdict for this host version from ``cache/compat.json`` (Requirement 9.2), and
whether Playwright is available for ``floofy verify --spa``.

``status`` lists the installed mods with state, seam per part, host-compat
badge, typed non-load reason and warnings, from the live Loader state when a
gateway runs, else from ``loader-state.json``; plus staged and quarantined mods.

``vanilla`` (also ``floofy --vanilla``) asks for exactly one gateway boot with
every mod disabled: it writes the ``vanilla-once`` marker the Loader consumes
(``floofy_loader.boot``), and tells the user to restart the gateway — the CLI
never restarts a live gateway itself.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from ..compat import CompatCache
from ..consent import read_consent
from ..governance import LOADER_APP_NAME, warnings_for
from ..loaderapp import early_install_module, installed_meta
from ..selfupdate import Release, up_to_date_reason
from ..semver import InvalidRange, Range
from ..triggers import loader_startup_status, trigger_managers
from .context import CliContext

__all__ = ["doctor", "register", "status", "vanilla"]

STATE_ROUTE = f"/api/apps/{LOADER_APP_NAME}/state"
HEALTH_ROUTE = f"/api/apps/{LOADER_APP_NAME}/health"


def register(sub: argparse._SubParsersAction) -> None:
    doc = sub.add_parser("doctor", help="diagnose the host, the Loader, triggers, governance warnings, consent, drift and the compat verdict")
    doc.add_argument("--no-live", action="store_true", help="do not contact a running gateway (files only)")
    doc.set_defaults(handler=doctor)
    st = sub.add_parser("status", help="installed mods with state, seam, compat, reason and warnings")
    st.add_argument("--no-live", action="store_true", help="do not contact a running gateway (loader-state.json only)")
    st.set_defaults(handler=status)
    van = sub.add_parser("vanilla", help="boot the host with every mod disabled, once (marker consumed by the next gateway start)")
    van.add_argument("--cancel", action="store_true", help="remove a pending vanilla-boot request")
    van.set_defaults(handler=vanilla)


# --- shared readers -----------------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def loader_state(ctx: CliContext, *, live: bool = True) -> tuple[dict[str, Any] | None, str]:
    """The Loader's state document and where it came from (``live`` / ``in-process`` / ``file`` / ``none``)."""
    if ctx.in_gateway and ctx.live_state is not None:
        try:
            return ctx.live_state(), "in-process"
        except Exception as exc:  # noqa: BLE001 - fall back to the file
            ctx.warn(f"live state unavailable ({type(exc).__name__}: {exc})")
    if live and not ctx.in_gateway:
        session = ctx.session()
        if session is not None:
            try:
                reply = session.get(STATE_ROUTE)
                if reply.status == 200:
                    return reply.json(), "live"
                note = f"live state unavailable (HTTP {reply.status})"
            except Exception as exc:  # noqa: BLE001 - fall back to the file
                note = f"live state unavailable ({type(exc).__name__}: {exc})"
            ctx.warn(note)
    document = _read_json(ctx.home.loader_state)
    if isinstance(document, dict):
        return document, "file"
    return None, "none"


def compat_row(ctx: CliContext) -> dict[str, Any]:
    """The cached compat verdict for the current host (Requirement 9.2).

    The edition comes from the CLI context (adapters, else the payload's stamp
    normalised) rather than the raw payload stamp, so an unstamped or adapter-less
    run still finds the row the registry published for this edition.
    """
    cache = CompatCache.load(ctx.home.compat_cache)
    current = ctx.current_payload()
    row = cache.row_for(ctx.edition(), current.channel, current.host_version.text) if current is not None else None
    return {"cached": bool(cache.rows) or not cache.notes, "source": cache.source, "generatedAt": cache.generated_at, "rows": len(cache.rows), "notes": list(cache.notes), "row": row.to_dict() if row else None}


def governance_report(ctx: CliContext) -> dict[str, Any]:
    """The host's policy as information, and which installed mods cross it (Requirement 11.2, 7.4)."""
    mods = ctx.mods(include_broken=False)
    theme_targets = [m.id for m in mods if "theme" in m.kinds]
    app_targets = [m.id for m in mods if "app" in m.kinds]
    snapshot = ctx.governance()
    warnings = warnings_for(snapshot, theme_targets=theme_targets, app_targets=app_targets)
    crossing: dict[str, list[str]] = {}
    for warning in warnings:
        crossing[warning.code] = [t for t in warning.affected_targets if t != LOADER_APP_NAME]
    return {"snapshot": snapshot.to_dict(), "warnings": [w.to_dict() for w in warnings], "crossing": crossing, "note": "governance is information: FloofyCrew proceeds on your consent and never treats it as a gate (DR-5)"}


def host_compat_badge(manifest: dict[str, Any], base_version: str, compat: dict[str, Any] | None, mod_id: str, version: str) -> str:
    """``in-range`` / ``out-of-range(strict)`` / ``out-of-range`` plus the matrix verdict when known."""
    kirocrew = manifest.get("kirocrew") if isinstance(manifest.get("kirocrew"), dict) else {}
    text = str(kirocrew.get("version") or "*")
    try:
        in_range = Range.parse(text).contains(base_version)
    except (InvalidRange, ValueError):
        in_range = False
    badge = "in-range" if in_range else ("out-of-range(strict)" if kirocrew.get("strict") else "out-of-range")
    verdict = (compat or {}).get("mods", {}).get(f"{mod_id}@{version}") if compat else None
    return f"{badge}, matrix: {verdict}" if verdict else badge


# --- doctor -----------------------------------------------------------------------------------------


def doctor(ctx: CliContext, args: argparse.Namespace) -> int:
    live = not args.no_live
    report: dict[str, Any] = {"floofycrew": ctx.about(), "problems": []}
    problems: list[str] = report["problems"]

    discovery = ctx.discovery()
    current = ctx.current_payload()
    served_version: str | None = None
    gateway: dict[str, Any] = {"running": False, "servedVersion": None, "endpoint": None, "via": None, "detail": "not probed (--no-live)" if not live else "no dashboard socket in the host home"}
    if live:
        from ..gateway import find_endpoints, probe_version  # noqa: PLC0415

        for endpoint in find_endpoints(ctx.host_home):
            # task 10.8: over the socket the host withholds its version; probe_version retries over loopback TCP
            probe = probe_version(endpoint)
            if not probe.reachable:
                gateway["detail"] = probe.detail
                continue
            served_version = probe.version
            gateway = {"running": True, "servedVersion": probe.version, "endpoint": endpoint.label, "via": probe.via, "detail": probe.detail}
            break
    report["host"] = {
        "edition": ctx.edition(),
        "version": current.host_version.text if current else None,
        "channel": current.channel if current else None,
        "hostHome": str(ctx.host_home),
        "gateway": gateway,
        "payloads": [
            {"id": p.id, "version": p.host_version.text, "edition": p.edition, "channel": p.channel, "current": p.current, "dormant": (served_version != p.host_version.text) if served_version else not p.current, "root": str(p.root), "interpreter": str(p.interpreter) if p.interpreter else None, "source": p.source}
            for p in discovery.payloads
        ],
        "searchedRoots": [str(r) for r in discovery.searched_roots],
        "notes": list(discovery.notes),
    }
    if not discovery.payloads:
        problems.append("no KiroCrew payload found: " + discovery.format_miss().splitlines()[0])

    meta = installed_meta(ctx.host_home)
    state, state_source = loader_state(ctx, live=live)
    failure = _read_json(ctx.home.loader_failure)
    report["loader"] = {
        **meta,
        "stateSource": state_source,
        "state": None if state is None else {k: state.get(k) for k in ("loader", "bootedAt", "active", "order", "errors", "durationMs", "loaderVersion", "api_version", "vanilla") if k in state},
        "failure": failure if isinstance(failure, dict) else None,
    }
    if not meta["installed"]:
        problems.append("the Loader app is not installed (floofy init)")
    elif meta["enabled"] is False:
        problems.append("the Loader app is installed but disabled: the host's execution gate needs the agent.apps_trusted grant (floofy init offers it)")
    if isinstance(failure, dict):
        problems.append(f"the Loader failed during {failure.get('phase')}: {failure.get('error')} (the gateway ran vanilla)")

    consent = read_consent(ctx.home.consent)
    report["consent"] = consent.to_dict()
    if consent.required:
        problems.append(f"consent: {consent.detail}")

    report["earlyShim"] = _early_shim_report(ctx)
    report["triggers"] = _trigger_report(ctx)
    if not any(t.get("installed") for t in report["triggers"]["managers"]):
        problems.append("no re-apply trigger installed: patches will not survive the next host update (floofy init installs it)")

    report["governance"] = governance_report(ctx)
    report["drift"] = _drift_report(ctx, live=live)
    report["compat"] = compat_row(ctx)
    report["playwright"] = {"available": importlib.util.find_spec("playwright") is not None, "note": "needed by `floofy verify --spa`; `pip install playwright && playwright install chromium`"}
    # Requirement 7.7: the daily FloofyCrew release check (cached, never blocking; skipped --offline / updates.check false)
    from .cmd_selfupdate import summary_for  # noqa: PLC0415

    report["selfUpdate"] = summary_for(ctx)
    mods = ctx.mods()
    report["mods"] = {"installed": len([m for m in mods if not m.problem]), "broken": [m.to_dict() for m in mods if m.problem], "pending": ctx.home.staged_mods(), "quarantine": _quarantine_summary(ctx)}
    report["vanillaRequested"] = ctx.home.vanilla_marker.is_file()
    ctx.set_result(**report)
    _print_doctor(ctx, report)
    return 0


def _early_shim_report(ctx: CliContext) -> dict[str, Any]:
    module = early_install_module(ctx.host_home)
    entries: list[dict[str, Any]] = []
    if module is None:
        return {"available": False, "detail": "Loader app not installed (the shim ships inside it)", "payloads": entries}
    for payload in ctx.payloads()[:4]:
        if payload.interpreter is None:
            entries.append({"payload": payload.id, "status": None, "note": "no interpreter"})
            continue
        kind = "venv-site" if (payload.root / "pyvenv.cfg").is_file() else "user-site"
        adapter = ctx.adapter_for(payload.edition)
        chooser = getattr(adapter, "early_shim_kind", None)
        if callable(chooser):
            try:
                kind = str(chooser(payload) or kind)
            except Exception:  # noqa: BLE001
                pass
        try:
            status = module.status(payload.interpreter, kind)
            entries.append({"payload": payload.id, "kind": kind, "status": status.to_dict()})
        except Exception as exc:  # noqa: BLE001
            entries.append({"payload": payload.id, "kind": kind, "status": None, "note": f"{type(exc).__name__}: {exc}"})
    return {"available": True, "earlyList": _read_json(ctx.home.early), "payloads": entries}


def _trigger_report(ctx: CliContext) -> dict[str, Any]:
    managers = trigger_managers(ctx.host_home, ctx.adapters(), edition=ctx.edition(), payload=ctx.current_payload())
    statuses = [m.status().to_dict() for m in managers]
    return {"managers": statuses, "loaderStartup": loader_startup_status(ctx.host_home).to_dict()}


def _drift_report(ctx: CliContext, *, live: bool) -> dict[str, Any]:
    payloads = ctx.payloads()
    if not payloads:
        return {"payloads": []}
    from ..patcher import Patcher  # noqa: PLC0415

    patcher = Patcher(ctx.data_home, payloads, host_home=ctx.host_home, endpoints=None if live else [])
    return patcher.status().to_dict()


def _quarantine_summary(ctx: CliContext) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    if not ctx.home.quarantine.is_dir():
        return summary
    for version_dir in sorted(p for p in ctx.home.quarantine.iterdir() if p.is_dir() and p.name != "requests"):
        summary[version_dir.name] = sorted(p.name for p in version_dir.iterdir() if p.is_dir())
    requests = ctx.home.quarantine_requests
    if requests.is_dir():
        summary["requests"] = sorted(p.stem for p in requests.glob("*.json"))
    return summary


def _print_doctor(ctx: CliContext, report: dict[str, Any]) -> None:
    about, host, loader = report["floofycrew"], report["host"], report["loader"]
    # The banner sits to the LEFT of the header block (this line through the loader state); the
    # column closes before the detail sections so their long paths are not pushed right.
    ctx.console.open_banner_column()
    ctx.say(f"{ctx.paint('floofy doctor', 'heading')} — FloofyCrew {about['version']} (api {about['apiVersion']}, {ctx.paint('unofficial', 'italic')}) on Python {about['python']}")
    ctx.say(f"{ctx.paint('host:', 'accent')} edition={host['edition']} version={host['version']} channel={host['channel']} home={host['hostHome']}")
    gateway = host["gateway"]
    if gateway["running"] and gateway["servedVersion"]:
        via = f" (version via {gateway['via']})" if gateway.get("via") else ""
        ctx.say(f"{ctx.paint('gateway:', 'accent')} {ctx.paint('running', 'ok')}, serving {gateway['servedVersion']} at {gateway['endpoint']}{via}")
    elif gateway["running"]:
        ctx.say(f"{ctx.paint('gateway:', 'accent')} {ctx.paint('running', 'ok')} at {gateway['endpoint']}, {ctx.paint('served version unknown', 'warn')} — {gateway.get('detail') or 'the host withheld it'}")
    else:
        ctx.say(f"{ctx.paint('gateway:', 'accent')} {ctx.paint('not running (or not reachable from here)', 'muted')}" + (f" — {gateway['detail']}" if gateway.get("detail") else ""))
    for payload in host["payloads"]:
        flags = ", ".join(f for f, on in (("current", payload["current"]), ("dormant", payload["dormant"])) if on) or "-"
        ctx.say(f"  payload {payload['id']} [{payload['edition']}, {payload['channel'] or '?'}; {flags}] {payload['root']}")
    if not host["payloads"]:
        for root in host["searchedRoots"]:
            ctx.say(f"  searched root: {root}")
    ctx.say(f"{ctx.paint('loader app:', 'accent')} installed={loader['installed']} enabled={loader['enabled']} version={loader['version']} ({loader['appDir']})")
    if loader["state"]:
        state = loader["state"]
        ctx.say(f"  last boot ({loader['stateSource']}): loader={state.get('loader')} active={state.get('active')} at {state.get('bootedAt')}" + (" [vanilla boot]" if state.get("vanilla") else ""))
        for error in state.get("errors") or []:
            ctx.say(f"  loader error: {error}", tone="danger")
    if loader["failure"]:
        ctx.say(f"  LOADER FAILURE during {loader['failure'].get('phase')}: {loader['failure'].get('error')}", tone="danger")
    ctx.console.close_banner_column()
    consent = report["consent"]
    ctx.say(f"{ctx.paint('consent:', 'accent')} {ctx.paint(consent['status'], 'ok' if consent['status'] == 'ok' else 'warn')}" + (f" (v{consent['warningVersion']} by {consent['by']} at {consent['acknowledgedAt']}, {consent.get('how') or 'unknown'})" if consent["status"] == "ok" else f" — {consent['message']}"))
    shim = report["earlyShim"]
    if shim.get("available"):
        for entry in shim["payloads"]:
            status = entry.get("status") or {}
            ctx.say(f"early shim [{entry.get('kind')}] {entry['payload']}: {'installed' if status.get('installed') else 'not installed'}" + (f" ({status.get('site_dir')})" if status.get("site_dir") else "") + (f" — {entry['note']}" if entry.get("note") else ""))
            for note in status.get("notes") or []:
                ctx.say(f"    note: {note}")
    else:
        ctx.say(f"early shim: {shim.get('detail')}")
    for trigger in report["triggers"]["managers"]:
        ctx.say(f"trigger [{trigger['kind']}]: {ctx.paint('installed', 'ok') if trigger['installed'] else ctx.paint('missing', 'warn')}" + (f", active={trigger['active']}" if trigger.get("active") is not None else "") + (f" — {trigger['detail']}" if trigger.get("detail") else ""))
    startup = report["triggers"]["loaderStartup"]
    ctx.say(f"trigger [loader-startup]: {ctx.paint('present', 'ok') if startup['installed'] else ctx.paint('missing', 'warn')} — {startup['detail']}")
    governance = report["governance"]
    if governance["warnings"]:
        ctx.say("governance (information only — never a gate):", tone="warn")
        for warning in governance["warnings"]:
            who = f" [{', '.join(warning['affectedTargets'])}]" if warning["affectedTargets"] else ""
            ctx.say(f"  {warning['code']}{who}: {warning['message']}")
    else:
        ctx.say("governance: nothing in the host's policy would close a seam FloofyCrew uses")
    for payload in report["drift"].get("payloads", []):
        drift = {k: v for k, v in payload["drift"].items() if v}
        ctx.say(f"drift {payload['payload']}: patched {payload['patched']}, added {payload['added']}, backups {payload['backups_present']}" + (f", drift {drift}" if drift else "") + (" (dormant)" if payload["dormant"] else ""))
    compat = report["compat"]
    if compat["row"]:
        framework = compat["row"].get("framework") or {}
        ctx.say(f"compat verdict for {compat['row']['hostVersion']} ({compat['row']['edition']}/{compat['row']['channel']}): loader={framework.get('loader')} spaFingerprints={framework.get('spaFingerprints')} mods={compat['row'].get('mods')}")
    else:
        ctx.say("compat verdict: none cached for this host version (floofy registry refresh fetches compat.json)" + (f" — {'; '.join(compat['notes'])}" if compat["notes"] else ""))
    ctx.say(f"playwright: {'available' if report['playwright']['available'] else 'missing'} ({report['playwright']['note']})")
    _say_self_update(ctx, report["selfUpdate"])
    mods = report["mods"]
    ctx.say(f"mods: {mods['installed']} installed, {len(mods['broken'])} broken, pending {mods['pending'] or '-'}, quarantine {mods['quarantine'] or '-'}")
    if report["vanillaRequested"]:
        ctx.say("vanilla boot requested: the next gateway start disables every mod once")
    if report["problems"]:
        ctx.say("problems:", tone="danger")
        for problem in report["problems"]:
            ctx.say(f"  - {problem}")
    else:
        ctx.say("no problems found", tone="ok")


# --- status -----------------------------------------------------------------------------------------


def _say_self_update(ctx: CliContext, summary: dict[str, Any], *, always: bool = True) -> None:
    """The one-line FloofyCrew update notice (Requirement 7.7); ``doctor`` also reports a quiet check, ``status`` only the notice."""
    if summary.get("available") and summary.get("notice"):
        ctx.say(f"{ctx.paint('update:', 'accent')} {ctx.paint(str(summary['notice']), 'warn')}")
    elif always:
        latest = summary.get("latest") or {}
        if summary.get("status") == "ok" and latest:
            # the same reason `self-update` prints (task 10.12): judged by version order, not by equality with the running version
            why = up_to_date_reason(Release.from_dict(latest), running=str(summary.get("running") or ""), edition=summary.get("edition"), channel=summary.get("channel"), host_version=summary.get("hostVersion"))
            ctx.say(f"{ctx.paint('update:', 'accent')} FloofyCrew {summary.get('running')} is up to date for this host ({why}; checked {summary.get('checkedAt')})")
        else:
            ctx.say(f"{ctx.paint('update:', 'accent')} {ctx.paint('check ' + str(summary.get('status')), 'muted')} — {summary.get('detail')}")
    stage = summary.get("staged") or {}
    if stage and not stage.get("applied") and stage.get("present"):
        ctx.say(f"{ctx.paint('update:', 'accent')} Loader app update to FloofyCrew {stage.get('version')} staged — installed by `floofy apply` the next time no gateway runs, or now with `floofy self-update --now`", tone="warn")


def status(ctx: CliContext, args: argparse.Namespace) -> int:
    state, source = loader_state(ctx, live=not args.no_live)
    current = ctx.current_payload()
    base = str(current.host_version.base) if current else "0.0.0"
    compat = compat_row(ctx)["row"]
    enabled = ctx.enabled()
    compat_row_obj = ctx.current_compat_row()
    rows: list[dict[str, Any]] = []
    state_mods = (state or {}).get("mods") or {}
    kctx = None
    for mod in ctx.mods():
        published = state_mods.get(mod.id) or {}
        seam_status: dict[int, dict[str, Any]] = {}
        if not mod.problem and any(k in SEAM_KINDS for k in mod.kinds):
            from ..installer import run_handlers  # noqa: PLC0415
            from .actions import kind_context  # noqa: PLC0415

            kctx = kctx or kind_context(ctx)
            seam_status = {o.index: o.to_dict() for o in run_handlers(kctx, mod.id, mod.dir, mod.manifest, "status") if o.kind in SEAM_KINDS}
        row = {
            "id": mod.id,
            "version": mod.version,
            "name": mod.name,
            "enabled": enabled.get(mod.id, not mod.has_code),
            "active": published.get("active"),
            "reason": published.get("reason") if published else ("Error" if mod.problem else None),
            "detail": published.get("detail") if published else (mod.problem or ""),
            "quarantined": published.get("quarantined", False),
            "hostCompat": host_compat_badge(mod.manifest, base, compat, mod.id, mod.version) if not mod.problem else "unknown",
            "tier": ctx.tier_of(mod, compat_row_obj),
            "parts": _merge_parts(published.get("parts") or [{"index": i, "kind": p.get("kind"), "side": p.get("side"), "path": p.get("path"), "status": "unknown", "seam": _seam(p.get("kind")), "modifiesPayload": p.get("kind") == "patch"} for i, p in enumerate(mod.parts)], seam_status),
            "warnings": published.get("warnings") or [],
            "governance": _merge_governance(published.get("governance") or [], seam_status),
            "link": mod.is_link,
            "stateSource": "published" if published else "manifest",
        }
        rows.append(row)
    pending = ctx.home.staged_mods()
    from .cmd_selfupdate import summary_for  # noqa: PLC0415

    self_update = summary_for(ctx)
    ctx.set_result(loader={"source": source, "state": (state or {}).get("loader"), "bootedAt": (state or {}).get("bootedAt"), "vanilla": (state or {}).get("vanilla")}, mods=rows, pending=pending, quarantine=_quarantine_summary(ctx), host={"version": current.host_version.text if current else None, "edition": ctx.edition()}, selfUpdate=self_update)
    ctx.say(f"{ctx.paint('floofy status', 'heading')} — {len(rows)} mod(s); loader state from {source}" + (f" ({(state or {}).get('loader')}, booted {(state or {}).get('bootedAt')})" if state else ""))
    _say_self_update(ctx, self_update, always=False)
    for row in rows:
        marker = "on " if row["active"] else ("--" if row["enabled"] is False else "off")
        glyph = ctx.paint(f"[{marker}]", "ok" if row["active"] else ("muted" if row["enabled"] is False else "warn"))
        reason = f" reason={row['reason']}" if row["reason"] else ""
        ctx.say(f"  {glyph} {ctx.paint(row['id'], 'bold')} {row['version']}  enabled={row['enabled']} compat={row['hostCompat']} tier={row['tier']}{reason}" + (f" — {row['detail']}" if row["detail"] else "") + (" (dev link)" if row["link"] else ""))
        for part in row["parts"]:
            seam_note = f"; on disk: {part['seamStatus']}" if part.get("seamStatus") else ""
            ctx.say(f"       part {part['index']} {part['kind']}/{part.get('side', '?')} {part.get('path', '')}: {part.get('status', '?')} via {part.get('seam', '')}{seam_note}" + (" [modifies payload files]" if part.get("modifiesPayload") else ""))
        for warning in row["warnings"]:
            ctx.say(f"       {ctx.paint('warning', 'warn')} {warning.get('code')}: {warning.get('message')}")
        for warning in row["governance"]:
            ctx.say(f"       {ctx.paint('governance', 'warn')} {warning.get('code')}: {warning.get('message')}")
    if pending:
        ctx.say(f"pending (applied at the next gateway start — restart to apply): {', '.join(pending)}")
    quarantine = _quarantine_summary(ctx)
    if quarantine:
        ctx.say(f"quarantine: {quarantine}")
    return 0


#: Kinds whose handler can say whether the part is in place on this machine (Requirement 2.7).
SEAM_KINDS = frozenset({"theme", "agent", "skill", "appearance", "config", "app"})


def _merge_parts(parts: list[dict[str, Any]], seam_status: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the kind handler's presence verdict (``seamStatus``) to the Loader's part rows."""
    merged = []
    for part in parts:
        row = dict(part)
        verdict = seam_status.get(int(part.get("index", -1)))
        if verdict is not None:
            row["seamStatus"] = verdict.get("status")
            row["seamDetail"] = verdict.get("detail")
            row["seam"] = verdict.get("seam") or row.get("seam")
            if verdict.get("modifiesPayload"):
                row["modifiesPayload"] = True
        merged.append(row)
    return merged


def _merge_governance(published: list[dict[str, Any]], seam_status: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {(g.get("code"), g.get("message")) for g in published}
    merged = list(published)
    for verdict in seam_status.values():
        for warning in verdict.get("governance") or []:
            key = (warning.get("code"), warning.get("message"))
            if key not in seen:
                seen.add(key)
                merged.append(warning)
    return merged


def _seam(kind: Any) -> str:
    seams = {
        "theme": "themes directory (host validator or byte-equivalent direct write)",
        "agent": "agents directory",
        "skill": "skills directory",
        "appearance": "appearance library",
        "config": "config.json (kirocrew config set semantics)",
        "app": "App Kit (kirocrew app install)",
        "python-hook": "Loader (in-process python-hook activation)",
        "spa": "SPA host (Loader app ui route)",
        "patch": "Patcher overlay (modifies payload files)",
    }
    return seams.get(str(kind), "")


# --- vanilla ----------------------------------------------------------------------------------------


def vanilla(ctx: CliContext, args: argparse.Namespace) -> int:
    marker = ctx.home.vanilla_marker
    if getattr(args, "cancel", False):
        existed = marker.is_file()
        if existed:
            marker.unlink()
        ctx.audit.record("vanilla", result="cancelled" if existed else "unchanged", detail="vanilla-once marker removed" if existed else "no marker")
        ctx.set_result(requested=False, cancelled=existed)
        ctx.say("vanilla boot request cancelled" if existed else "no vanilla boot was requested")
        return 0
    ctx.home.ensure()
    marker.write_text(json.dumps({"requestedAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()), "by": ctx.actor}) + "\n", encoding="utf-8")
    ctx.audit.record("vanilla", result="ok", detail="next gateway boot runs with every mod disabled (marker vanilla-once)")
    running = ctx.gateway_running()
    ctx.set_result(requested=True, marker=str(marker), gatewayRunning=running)
    ctx.say(f"vanilla boot requested: the next gateway start disables every mod once and restores the overlay patches for that boot (marker {marker.name}).")
    if running:
        ctx.say("a gateway is running — restart it yourself (`kirocrew restart`, or the App's Restart KiroCrew button) to take the vanilla boot; the CLI never restarts your gateway by itself.")
    ctx.say("the boot after that runs your mods again; `floofy vanilla --cancel` withdraws the request.")
    return 0
