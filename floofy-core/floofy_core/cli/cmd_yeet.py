"""``floofy yeet`` and ``floofy hold`` (Requirement 6.2, 6.3, 6.6).

* ``yeet <id>…`` parks mods under ``quarantine/<current host version>/`` (seam
  parts uninstalled, flags preserved) — the manual form of what the
  host-version-change handler does; ``yeet --list`` shows the quarantine;
  ``yeet --restore <ver> [ids…]`` brings a parked set back (what a rollback does
  automatically when the current host version names a quarantine directory).
* ``hold [--duration 7d] [--release]`` exposes the **host's own** update pause
  as an explicit, logged user action: the edition adapter's ``update_hold()`` runs the
  distribution's pause command (or explains that the edition has none and how to
  pin instead). FloofyCrew never pauses updates by default; every call writes an
  audit row (Requirement 6.6).
"""
from __future__ import annotations

import argparse
from typing import Any

from ..hostchange import list_quarantine, restore_quarantine, yeet
from .actions import reapply, reload_gateway
from .context import CliContext, CliError

__all__ = ["hold", "register", "yeet_cmd"]


def register(sub: argparse._SubParsersAction) -> None:
    y = sub.add_parser("yeet", help="park mods in the per-host-version quarantine, list it, or restore a parked set")
    y.add_argument("ids", nargs="*", help="mod ids to park (or to restore with --restore)")
    y.add_argument("--restore", metavar="HOSTVER", default=None, help="restore the set parked under quarantine/<HOSTVER>/")
    y.add_argument("--list", action="store_true", help="show the quarantine")
    y.add_argument("--reason", default="manual yeet", help="why (recorded)")
    y.set_defaults(handler=yeet_cmd)

    h = sub.add_parser("hold", help="pause the host's own auto-update (explicit, logged; never a default)")
    h.add_argument("--duration", default="7d", help="how long (the host's own syntax, e.g. 7d)")
    h.add_argument("--release", action="store_true", help="resume auto-updates")
    h.set_defaults(handler=hold)


def yeet_cmd(ctx: CliContext, args: argparse.Namespace) -> int:
    if args.list or (not args.ids and not args.restore):
        listing = list_quarantine(ctx.home)
        ctx.set_result(quarantine=listing)
        if not listing:
            ctx.say("quarantine is empty")
        for version, mods in listing.items():
            ctx.say(f"quarantine/{version}: {', '.join(mods) or '-'}")
        return 0
    if args.restore:
        ctx.require_consent("restore quarantined mods")
        outcome = restore_quarantine(ctx, args.restore, only=args.ids or None)
        ctx.set_result(**outcome)
        if outcome.get("detail"):
            ctx.say(outcome["detail"])
        for entry in outcome["restored"]:
            ctx.say(f"restored {entry['id']} {entry['version']} (enabled={entry['enabled']})")
        for entry in outcome["skipped"]:
            ctx.say(f"skipped {entry['id']}: {entry['detail']}")
        if outcome["restored"]:
            if ctx.gateway_running() or ctx.in_gateway:
                outcome["reload"] = reload_gateway(ctx, why=f"yeet restore {args.restore}")
            if not ctx.in_gateway:
                applied = reapply(ctx, quiet=True)
                outcome["patches"] = applied.to_dict() if applied else None
        return 0
    current = ctx.current_payload()
    host_version = current.host_version.text if current else "unknown"
    parked: list[dict[str, Any]] = []
    for mod_id in args.ids:
        mod = ctx.mod(mod_id)
        parked.append(yeet(ctx, mod, host_version, reasons=[args.reason]))
        ctx.say(f"yeeted {mod.id} {mod.version} -> quarantine/{host_version}/ ({args.reason})")
    ctx.set_result(yeeted=parked, hostVersion=host_version)
    if parked:
        if ctx.gateway_running() or ctx.in_gateway:
            ctx.result["reload"] = reload_gateway(ctx, why=f"yeet {', '.join(args.ids)}")
        if not ctx.in_gateway:
            applied = reapply(ctx, quiet=True)
            ctx.result["patches"] = applied.to_dict() if applied else None
    return 0


def hold(ctx: CliContext, args: argparse.Namespace) -> int:
    adapter = ctx.adapter_for(ctx.edition())
    hook = getattr(adapter, "update_hold", None) if adapter is not None else None
    action = "release" if args.release else f"hold {args.duration}"
    if not callable(hook):
        detail = f"the {ctx.edition()} edition offers no update pause through FloofyCrew; pin the host yourself (pip/pipx pin, or your package manager) and run `floofy apply` after each update"
        ctx.audit.record("hold", result="unavailable", detail=f"{action}: {detail}")
        ctx.set_result(action=action, available=False, detail=detail)
        ctx.say(detail)
        return 0
    if not ctx.console.confirm(f"{'Resume' if args.release else 'Pause'} the host's own auto-update ({action}) through its update tool? This is the host's mechanism, run on your explicit request.", default=False):
        ctx.audit.record("hold", result="declined", detail=action)
        raise CliError("hold cancelled")
    try:
        outcome = hook(ctx.host_home, duration=None if args.release else args.duration, release=args.release)
    except Exception as exc:  # noqa: BLE001 - the adapter's tool failing is reported, audited
        ctx.audit.record("hold", result="error", detail=f"{action}: {exc}")
        raise CliError(f"hold failed: {exc}") from exc
    ok = bool(outcome.get("ok", True))
    ctx.audit.record("hold", result="ok" if ok else "error", detail=f"{action}: {outcome.get('detail', '')}", hostCommand=outcome.get("command"))
    ctx.set_result(action=action, available=True, hostCommand=outcome.get("command"), detail=outcome.get("detail"), ok=ok)
    ctx.say(f"{action}: {outcome.get('detail', 'done')}")
    return 0 if ok else 1
