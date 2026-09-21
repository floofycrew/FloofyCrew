"""``floofy profile {list,save,use,export,import}`` (Requirement 7.5)."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import __version__ as FRAMEWORK_VERSION
from ..installer import InstallError, ResolvedSource, resolve_source
from ..profiles import Profile, ProfileError, list_profiles, plan_use, snapshot
from .actions import reapply, reload_gateway
from .context import CliContext, CliError

__all__ = ["profile", "register"]


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("profile", help="named mod sets with pinned versions (lockfiles); export/import as one file")
    actions = p.add_subparsers(dest="profile_action", required=True)
    actions.add_parser("list", help="list saved profiles")
    save = actions.add_parser("save", help="snapshot the installed set as a profile")
    save.add_argument("name")
    use = actions.add_parser("use", help="make the installed set match a profile (staged like installs; extras are disabled, never removed)")
    use.add_argument("name")
    use.add_argument("--now", action="store_true", help="install missing mods immediately and reload a running gateway")
    use.add_argument("--check", action="store_true", help="only show the plan")
    use.add_argument("--confirm-governance-target", action="append", default=[], metavar="PATH")
    use.add_argument("--accept-unlisted-source", action="store_true", help="accept the unlisted-source line of a git reference the profile pins (recorded; --yes never accepts it)")
    export = actions.add_parser("export", help="write a profile to a single file")
    export.add_argument("name")
    export.add_argument("file")
    imp = actions.add_parser("import", help="read a profile from a file into profiles/")
    imp.add_argument("file")
    imp.add_argument("--name", default=None, help="store under this name (default: the file's own)")
    p.set_defaults(handler=profile)


def profile(ctx: CliContext, args: argparse.Namespace) -> int:
    action = args.profile_action
    if action == "list":
        profiles = list_profiles(ctx.home)
        ctx.set_result(profiles=profiles)
        if not profiles:
            ctx.say("no profiles saved (floofy profile save <name>)")
        for entry in profiles:
            ctx.say(f"{entry['name']}: {entry.get('mods', '?')} mod(s), saved {entry.get('savedAt')} on host {entry.get('hostVersion')}" + (f" — {entry['error']}" if entry.get("error") else ""))
        return 0
    if action == "save":
        current = ctx.current_payload()
        try:
            snap = snapshot(args.name, ctx.mods(), ctx.enabled(), host_version=current.host_version.text if current else None, edition=ctx.edition(), floofycrew=FRAMEWORK_VERSION)
        except ProfileError as exc:
            raise CliError(str(exc)) from exc
        path = snap.save(ctx.home.profile(snap.name))
        ctx.audit.record("profile-save", result="ok", files=[str(path)], detail=f"{len(snap.mods)} mod(s)")
        ctx.set_result(**snap.to_dict(), path=str(path))
        ctx.say(f"saved profile {snap.name} with {len(snap.mods)} mod(s) at {path}")
        return 0
    if action == "export":
        try:
            prof = Profile.load(ctx.home.profile(args.name), name=args.name)
        except ProfileError as exc:
            raise CliError(str(exc)) from exc
        target = prof.save(Path(args.file).expanduser())
        ctx.set_result(name=prof.name, file=str(target), mods=len(prof.mods))
        ctx.say(f"exported profile {prof.name} to {target}")
        return 0
    if action == "import":
        try:
            prof = Profile.load(Path(args.file).expanduser(), name=args.name)
        except ProfileError as exc:
            raise CliError(str(exc)) from exc
        path = prof.save(ctx.home.profile(prof.name))
        ctx.audit.record("profile-import", result="ok", files=[str(path)], detail=f"from {args.file}")
        ctx.set_result(name=prof.name, path=str(path), mods=len(prof.mods))
        ctx.say(f"imported profile {prof.name} ({len(prof.mods)} mod(s)) to {path}")
        return 0
    if action == "use":
        return _use(ctx, args)
    raise CliError(f"unknown profile action {action!r}", exit_code=2)


def _use(ctx: CliContext, args: argparse.Namespace) -> int:
    from .cmd_mods import install_source  # noqa: PLC0415

    try:
        prof = Profile.load(ctx.home.profile(args.name), name=args.name)
    except ProfileError as exc:
        raise CliError(str(exc)) from exc
    steps = plan_use(prof, ctx.mods(), ctx.enabled())
    ctx.set_result(name=prof.name, plan=steps, applied=[])
    for step in steps:
        ctx.say(f"{step['id']}: {step['action']} {step.get('version', '')}".rstrip() + (f" (installed {step['installed']})" if step.get("installed") else "") + (f" enabled={step['enabled']}" if "enabled" in step else ""))
    if args.check:
        return 0
    ctx.require_consent("apply a profile")
    current = ctx.current_payload()
    base = current.host_version.base if current else "0.0.0"
    host_version = current.host_version.text if current else None
    changed = False
    problems = 0
    for step in steps:
        if step["action"] == "set-flag":
            if step["changed"]:
                ctx.set_enabled(step["id"], step["enabled"])
                changed = True
            ctx.result["applied"].append({"id": step["id"], "action": "set-flag", "enabled": step["enabled"]})
        elif step["action"] == "disable-extra":
            ctx.set_enabled(step["id"], False)
            changed = True
            ctx.result["applied"].append({"id": step["id"], "action": "disabled"})
        else:
            source: ResolvedSource | None = None
            try:
                source = resolve_source(step["ref"], home=ctx.home, base_version=base, edition=ctx.edition(), host_version=host_version, requested_version=step["version"] if "@" not in step["ref"] else None, opener_for=ctx.url_opener)
                outcome = install_source(ctx, source, now=args.now, enable_code=step["enabled"], pre_confirmed=list(args.confirm_governance_target), accept_flags=ctx.assume_yes, accept_unlisted=bool(getattr(args, "accept_unlisted_source", False)))
                ctx.set_enabled(step["id"], step["enabled"])
                changed = True
                ctx.result["applied"].append({"id": step["id"], "action": step["action"], "version": step["version"], "how": outcome["placed"]["how"]})
            except InstallError as exc:
                problems += 1
                ctx.warn(f"{step['id']}: {exc}")
                ctx.result["applied"].append({"id": step["id"], "action": step["action"], "error": str(exc)})
            finally:
                if source is not None:
                    source.cleanup()
    ctx.audit.record("profile-use", result="ok" if not problems else "partial", detail=f"{prof.name}: {len(steps)} step(s), {problems} problem(s)")
    if changed and (ctx.gateway_running() or ctx.in_gateway):
        ctx.result["reload"] = reload_gateway(ctx, why=f"profile use {prof.name}")
    if changed and not ctx.in_gateway:
        applied = reapply(ctx, quiet=True)
        ctx.result["patches"] = applied.to_dict() if applied else None
    ctx.say(f"profile {prof.name} applied: {len(steps)} step(s), {problems} problem(s)")
    return 0 if not problems else 1
