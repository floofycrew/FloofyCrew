"""Shared actions of the mod commands: the Patcher with the enabled set, the gateway reload, kind contexts.

* :func:`planned_patches` — the enabled mods' ``patch`` parts plus the generated
  ``index.html`` descriptor (loader tag + boot activation) — the same set the
  Loader hands the Patcher at boot (``floofy_loader.pending.plan_patches`` /
  ``plan_boot``), so ``floofy apply`` and a gateway start agree;
* :func:`reapply` — revert-then-patch every payload with that set (Requirement
  5.4, 6.4), asking the Requirement 5.9 survival question through the console;
* :func:`reload_gateway` — ``POST /api/apps/floofycrew/reload`` when a gateway
  runs (``--now``), or the in-process hook when the CLI runs inside the gateway;
* :func:`kind_context` — the :class:`floofy_core.kinds.KindContext` a seam
  handler gets, wired to the console, the audit log and the gateway session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..boot_script import BootScriptError, boot_mods_from_manifests, build_boot_descriptor, has_spa_parts
from ..governance import LOADER_APP_NAME, CallableConfirmer
from ..kinds import KindContext
from ..modstore import InstalledMod
from ..patcher import ApplyReport, Patcher, PlannedPatch
from ..patches import PatchDescriptor, PatchDescriptorError
from ..triggers import loader_startup_status, trigger_managers
from .context import CliContext

__all__ = ["enabled_mods", "kind_context", "planned_patches", "reapply", "reload_gateway", "triggers_installed"]

RELOAD_ROUTE = f"/api/apps/{LOADER_APP_NAME}/reload"


def enabled_mods(ctx: CliContext) -> list[InstalledMod]:
    """Installed, loadable mods whose ``enabled.json`` flag is on (an unlisted code mod is off — Requirement 11.7)."""
    flags = ctx.enabled()
    return [m for m in ctx.mods(include_broken=False) if flags.get(m.id, not m.has_code)]


def planned_patches(ctx: CliContext, mods: list[InstalledMod] | None = None) -> tuple[list[PlannedPatch], list[str]]:
    """The Patcher's set for the enabled mods: their ``patch`` parts + the generated shell descriptor."""
    mods = enabled_mods(ctx) if mods is None else mods
    planned: list[PlannedPatch] = []
    problems: list[str] = []
    for mod in mods:
        for index, part in enumerate(mod.parts):
            if part.get("kind") != "patch":
                continue
            try:
                planned.append(PlannedPatch(mod.id, str(index), PatchDescriptor.load(mod.dir / str(part.get("path", "")))))
            except (PatchDescriptorError, OSError, ValueError) as exc:
                problems.append(f"{mod.id}#{index}: {exc}")
    triples = [(m.id, m.dir, m.manifest) for m in mods]
    if any(has_spa_parts(m.manifest) for m in mods):
        try:
            descriptor = build_boot_descriptor(boot_mods_from_manifests(triples), host_home=ctx.host_home, loader_tag=True)
        except (BootScriptError, OSError, ValueError) as exc:
            problems.append(f"boot: {exc}")
            descriptor = None
        if descriptor is not None:
            planned.append(PlannedPatch(LOADER_APP_NAME, "boot", descriptor))
    return planned, problems


def triggers_installed(ctx: CliContext) -> bool | None:
    """Whether any re-apply trigger exists (Requirement 5.9); ``None`` when no manager can tell."""
    managers = trigger_managers(ctx.host_home, ctx.adapters(), edition=ctx.edition(), payload=ctx.current_payload())
    statuses = [m.status() for m in managers]
    if loader_startup_status(ctx.host_home).installed:
        return True
    if not statuses:
        return None
    return any(s.installed for s in statuses)


def reapply(ctx: CliContext, *, verify: bool = False, confirmed_governance_targets: list[str] | None = None, payload_ids: list[str] | None = None, quiet: bool = False) -> ApplyReport | None:
    """Revert-then-patch every payload with the enabled set; ``None`` when there is no payload."""
    payloads = ctx.payloads()
    if not payloads:
        ctx.warn("no payload found: nothing to patch (" + ctx.discovery().format_miss().splitlines()[0] + ")")
        return None
    planned, problems = planned_patches(ctx)
    for problem in problems:
        ctx.warn(f"patch descriptor: {problem}")
    say = (lambda _m: None) if (quiet or ctx.json_mode) else ctx.say
    confirmer = CallableConfirmer(lambda prompt, default: ctx.console.confirm(prompt, default=default))
    patcher = Patcher(
        ctx.data_home,
        payloads,
        confirmed_governance_targets=confirmed_governance_targets or [],
        endpoints=None if not ctx.in_gateway else [],
        host_home=ctx.host_home,
        reporter=say,
        governance=ctx.governance(),
        confirmer=confirmer,
        triggers_installed=triggers_installed(ctx),
    )
    return patcher.apply(planned, verify=verify and not ctx.in_gateway, payload_ids=payload_ids)


def reload_gateway(ctx: CliContext, *, why: str) -> dict[str, Any]:
    """Make the running Loader pick up the change now (Requirement 7.6 ``--now``)."""
    if ctx.in_gateway:
        ctx.mutated(why)
        return {"reloaded": True, "via": "in-process"}
    session = ctx.session()
    if session is None:
        return {"reloaded": False, "via": None, "detail": "no running gateway"}
    try:
        reply = session.post(RELOAD_ROUTE)
    except Exception as exc:  # noqa: BLE001 - a reload failure is reported, never fatal
        return {"reloaded": False, "via": session.label, "detail": f"{type(exc).__name__}: {exc}"}
    if reply.status != 200:
        return {"reloaded": False, "via": session.label, "detail": f"HTTP {reply.status}: {reply.body[:200].decode('utf-8', 'replace')}"}
    try:
        state = reply.json()
    except ValueError:
        state = {}
    return {"reloaded": True, "via": session.label, "active": state.get("active"), "loader": state.get("loader")}


def kind_context(ctx: CliContext) -> KindContext:
    return KindContext(
        host_home=ctx.host_home,
        kiro_home=ctx.kiro_home,
        data_home=ctx.data_home,
        launcher=ctx.launcher(),
        host_cli_env=ctx.host_cli_env(),
        session=ctx.session(),
        governance=ctx.governance(),
        confirm=lambda prompt: ctx.console.confirm(prompt, default=False),
        say=ctx.say,
        warn=ctx.warn,
        audit=ctx.audit.record,
        in_gateway=ctx.in_gateway,
    )
