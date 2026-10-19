"""``floofy search / info / list / install / uninstall / enable / disable / update / dev`` (Requirement 7.1, 7.6, 8.11, 11.3, 11.4, 11.7, 14.2).

The install flow, in the order the user experiences it:

1. resolve the reference (path, archive, HTTPS URL, git reference — shallow clone
   at the tag with the user's own credentials, Requirement 8.8 — or ``id[@version]``
   from the registry cache) and run ``floofy validate`` on the tree — errors refuse;
2. **disclose** (Requirement 11.7): parts with kind/side/seam and whether any
   modifies payload files, declared ``network.hosts[]`` + ``credentials``, the
   validator's governance and network flags, the host's admission verdict for
   ``app`` parts and the theme route's state (warnings, never gates), the source
   tier and — for a git reference — the extra line "unlisted source: no curator
   review, no compatibility data" (Requirement 8.8, 8.11);
3. **confirm**: ``python-hook`` / ``patch`` / ``app`` kinds need a yes (``--yes``
   works); network flags need acceptance (``--yes`` works); the unlisted-source
   line needs the typed phrase or ``--accept-unlisted-source`` (recorded; ``--yes``
   never covers it); every governance-altering target needs its exact path typed —
   ``--confirm-governance-target <path>`` per file for automation, each one
   recorded; ``--yes`` never covers these (Requirement 11.4);
4. **place**: into ``pending/<id>/`` when a gateway runs and ``--now`` is absent
   (applied at the next gateway start — "restart to apply"), else into
   ``mods/<id>/``; the seam kind handlers (theme, agent, skill, appearance,
   config, app) run right away — they act outside the gateway process;
5. ``enabled.json``: a confirmed install lands **enabled** — the step-3
   confirmation is the consent (Requirement 11.7); ``--disabled`` lands it
   switched off for a later ``floofy enable``;
6. audit row; ``--now`` reloads the running Loader and re-applies patches.

``uninstall`` reverses the seam parts, stages a ``pending/<id>.remove`` marker
while a gateway runs (``--now`` removes immediately and reloads), drops the flag
and re-applies the Patcher set. ``enable``/``disable`` flip the flag, reload a
running gateway and re-apply. ``update`` picks the newest version the registry
cache knows to work on this host (Requirement 9.2) and reuses the install flow.
``dev`` symlinks a checkout into ``mods/<id>`` and streams its log.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..consent import ACCEPT_PHRASE
from ..gitsource import UNLISTED_SOURCE_LINE
from ..installer import CONFIRM_KINDS, InstallError, ResolvedSource, disclose, manifest_of, place, resolve_source, run_handlers, validate
from ..modstore import read_source
from ..registry import IndexCache
from ..semver import InvalidVersion, Version
from ..tier import describe, tier_for_candidate
from .actions import kind_context, reapply, reload_gateway
from .context import CliContext, CliError

__all__ = ["register", "install", "uninstall", "enable", "disable", "search", "info", "list_mods", "update", "dev"]


def register(sub: argparse._SubParsersAction) -> None:
    s = sub.add_parser("search", help="search the registry cache (floofy registry refresh fills it); shows the tier an install would have here")
    s.add_argument("query", nargs="?", default="", help="words to match in id, name, description and tags")
    s.set_defaults(handler=search)

    n = sub.add_parser("info", help="everything about one mod: manifest summary, source record, tier, compat verdict, files")
    n.add_argument("id", help="an installed mod id, or one in the registry cache")
    n.set_defaults(handler=info)

    l = sub.add_parser("list", help="installed mods, one line each: version, enabled, tier, source")
    l.set_defaults(handler=list_mods)

    i = sub.add_parser("install", help="install a mod from a path, an archive, an https URL, a git reference (ssh://… or https://…, default branch unless @tag pins one) or the registry (id[@version])")
    i.add_argument("ref", help="mod directory, .zip/.tar.gz, https://… URL, git reference ssh://<host>/<path>[@<tag>][#<subdir>] / https://<host>/<owner>/<repo>[.git][@<tag>] (no @tag = the default branch; version and compatibility come from floofy.json), or registry id[@version]")
    i.add_argument("--now", action="store_true", help="install immediately and reload a running gateway (default: stage for the next gateway start)")
    i.add_argument("--disabled", action="store_true", help="install the mod switched off (enable it later with `floofy enable <id>`); by default a confirmed install lands enabled")
    i.add_argument("--enable", action="store_true", help=argparse.SUPPRESS)  # pre-1.3 flag: enabled is the default now; accepted so old automation keeps working
    i.add_argument("--sha256", default=None, help="expected SHA-256 of a downloaded archive")
    i.add_argument("--ref", dest="ref_name", default=None, metavar="BRANCH|COMMIT", help="git reference only: install an UNPINNED checkout (no @tag) on purpose; the resolved commit is recorded")
    i.add_argument("--confirm-governance-target", action="append", default=[], metavar="PATH", help="pre-confirm one governance-altering target path (repeatable; each recorded in the audit log)")
    i.add_argument("--accept-flags", action="store_true", help="accept the network/unlisted-file flags without asking (--yes implies)")
    i.add_argument("--accept-unlisted-source", action="store_true", help=f"git reference only: accept the extra consent line '{UNLISTED_SOURCE_LINE}' without typing {ACCEPT_PHRASE} (automation; recorded as such; --yes never accepts it)")
    i.set_defaults(handler=install)

    u = sub.add_parser("uninstall", help="remove a mod (staged while a gateway runs unless --now)")
    u.add_argument("id")
    u.add_argument("--now", action="store_true", help="remove immediately and reload a running gateway")
    u.add_argument("--keep-config", action="store_true", help="keep the mod's .floofy/ runtime state (config, log)")
    u.set_defaults(handler=uninstall)

    for name, fn, text in (("enable", enable, "enable a mod (reloads a running gateway, re-applies patches)"), ("disable", disable, "disable a mod (reloads a running gateway, re-applies patches)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("id")
        p.add_argument("--no-reload", action="store_true", help="do not reload a running gateway")
        p.set_defaults(handler=fn)

    up = sub.add_parser("update", help="update installed mods to the newest version the registry cache knows to work here")
    up.add_argument("ids", nargs="*", help="mod ids (default: what --all covers)")
    up.add_argument("--all", action="store_true", help="every installed mod that came from the registry")
    up.add_argument("--now", action="store_true", help="install immediately and reload a running gateway")
    up.add_argument("--check", action="store_true", help="only report what would update")
    up.add_argument("--confirm-governance-target", action="append", default=[], metavar="PATH")
    up.add_argument("--accept-flags", action="store_true")
    up.set_defaults(handler=update)

    d = sub.add_parser("dev", help="symlink a mod checkout into mods/<id>, enable host dev mode where applicable, stream its log")
    d.add_argument("path", help="the mod directory to link")
    d.add_argument("--follow", "-f", action="store_true", help="stream mods/<id>/.floofy/mod.log and Loader faults until Ctrl-C")
    d.add_argument("--unlink", action="store_true", help="remove the dev link again")
    d.add_argument("--no-reload", action="store_true")
    d.set_defaults(handler=dev)


# --- search -----------------------------------------------------------------------------------


def search(ctx: CliContext, args: argparse.Namespace) -> int:
    cache = IndexCache.load(ctx.home)
    hits = cache.search(args.query)
    target = ctx.registry_target()
    installed = {mod.id: mod for mod in ctx.mods(include_broken=False)}
    rows = []
    for entry in hits:
        newest = entry.versions[-1] if entry.versions else None
        best = cache.best_version(entry.key, **target)
        # the tier an install from this record WOULD have here (Requirement 8.11): tested when the graded verdict is tested, else listed
        rows.append({**entry.to_dict(), "newest": newest.version if newest else None, "worksHere": best.version.version if best.version else None, "verdict": best.verdict, "why": best.why, "tier": tier_for_candidate(best.verdict), "record": "link" if best.version is not None and best.version.link is not None else "archive", "installed": installed[entry.id].version if entry.id in installed else None})
    ctx.set_result(query=args.query, cache=cache.to_dict(), mods=rows, compat=target["compat"].to_dict(target["compat"].row_for(target["edition"], target["channel"], target["host_version"]) if target["host_version"] else None))
    if not cache.mods:
        ctx.say("the registry cache is empty: " + ("; ".join(cache.notes) if cache.notes else "no mods listed"))
        return 0
    ctx.say(f"{len(rows)} mod(s) match {args.query!r} in the registry cache ({cache.source}):")
    for row in rows:
        works = f"works here: {row['worksHere']}" + (f" ({row['verdict']})" if row["verdict"] else " (untested)") if row["worksHere"] else f"nothing known to work on this host ({row['why']})"
        ctx.say(f"  {row['key']} — {row['name']}: {row['description'][:80]} [newest {row['newest']}; {works}; tier {row['tier']}]" + (f" (installed {row['installed']})" if row["installed"] else ""))
    return 0


# --- info / list --------------------------------------------------------------------------------------


def info(ctx: CliContext, args: argparse.Namespace) -> int:
    """``floofy info <id>``: the manifest summary, the source record, the tier, the compat verdict and the files (Requirement 8.11)."""
    row = ctx.current_compat_row()
    installed = next((m for m in ctx.mods() if m.id == args.id), None)
    cache = IndexCache.load(ctx.home)
    entry = cache.find(args.id)
    if installed is None and entry is None:
        raise CliError(f"{args.id!r} is neither installed nor in the registry cache (floofy search; floofy registry refresh)")
    result: dict[str, Any] = {"id": args.id, "installed": None, "registry": None}
    if installed is not None:
        manifest = installed.manifest
        source = read_source(installed.dir)
        tier = ctx.tier_of(installed, row)
        result["installed"] = {
            "version": installed.version,
            "name": installed.name,
            "description": str(manifest.get("description") or ""),
            "authors": [str(a) for a in (manifest.get("authors") or [])],
            "license": manifest.get("license"),
            "kirocrew": manifest.get("kirocrew"),
            "dir": str(installed.dir),
            "link": installed.is_link,
            "problem": installed.problem,
            "enabled": ctx.enabled().get(installed.id, not installed.has_code),
            "source": source,
            "tier": tier,
            "tierMeaning": describe(tier),
            "verdict": row.verdict(installed.id, installed.version) if row is not None else None,
            "run": row.runs.get(f"{installed.id}@{installed.version}") if row is not None else None,
            "parts": [{"index": i, "kind": p.get("kind"), "side": p.get("side"), "path": p.get("path")} for i, p in enumerate(installed.parts)],
            "files": [{"path": f.get("path"), "sha256": f.get("sha256")} for f in (manifest.get("files") or []) if isinstance(f, dict)],
        }
    if entry is not None:
        target = ctx.registry_target()
        best = cache.best_version(entry.key, **target)
        result["registry"] = {**entry.to_dict(), "worksHere": best.version.version if best.version else None, "verdict": best.verdict, "why": best.why, "tier": tier_for_candidate(best.verdict)}
    ctx.set_result(**result)
    if installed is not None:
        i = result["installed"]
        ctx.say(f"{i['name']} ({installed.id} {i['version']}) — installed{' (dev link)' if i['link'] else ''}{'; PROBLEM: ' + i['problem'] if i['problem'] else ''}")
        ctx.say(f"  {i['description'][:200]}")
        ctx.say(f"  authors: {', '.join(i['authors']) or '-'}; license: {i['license'] or '-'}; host range: {(i['kirocrew'] or {}).get('version', '*') if isinstance(i['kirocrew'], dict) else '*'}")
        source = i["source"]
        where = f"{source.get('source', 'unknown')} {source.get('ref', '')}".strip()
        if source.get("commit"):
            where += f" at commit {str(source['commit'])[:12]}"
        ctx.say(f"  source: {where or 'no record (copied in by hand)'}; installed {source.get('installedAt', '?')}")
        if source.get("link"):
            link = source["link"]
            ctx.say(f"  link record: {link.get('repo')} at {link.get('tag')} (commit {str(link.get('commit') or '')[:12]}, manifest {str(link.get('manifestSha256') or '')[:12]}…)")
        ctx.say(f"  tier: {i['tier']} — {i['tierMeaning']}")
        ctx.say(f"  compat on this host: {i['verdict'] or 'no matrix cell'}" + (f" (run {i['run']})" if i["run"] else ""))
        ctx.say(f"  enabled: {i['enabled']}; parts: " + ", ".join(f"{p['kind']}/{p['side']} {p['path']}" for p in i["parts"]))
        ctx.say(f"  files ({len(i['files'])}):")
        for f in i["files"]:
            ctx.say(f"    {str(f['sha256'])[:12]}  {f['path']}")
    if entry is not None:
        r = result["registry"]
        newest = entry.versions[-1].version if entry.versions else None
        ctx.say(f"{'also ' if installed is not None else ''}in the registry cache ({entry.source}): {entry.key} newest {newest}; works here: {r['worksHere'] or 'nothing'}" + (f" ({r['verdict']})" if r["verdict"] else "") + f"; tier of an install from here: {r['tier']}")
    return 0


def list_mods(ctx: CliContext, args: argparse.Namespace) -> int:
    """``floofy list``: one line per installed mod — id, version, enabled, tier, source (Requirement 8.11)."""
    row = ctx.current_compat_row()
    enabled = ctx.enabled()
    rows: list[dict[str, Any]] = []
    for mod in ctx.mods():
        source = read_source(mod.dir)
        rows.append({"id": mod.id, "version": mod.version, "name": mod.name, "enabled": enabled.get(mod.id, not mod.has_code), "tier": ctx.tier_of(mod, row), "source": source.get("source"), "ref": source.get("ref"), "commit": source.get("commit"), "link": mod.is_link, "problem": mod.problem})
    ctx.set_result(mods=rows)
    if not rows:
        ctx.say("no mods installed (floofy install <id | path | archive | https URL | git reference>)")
        return 0
    for r in rows:
        ctx.say(f"  {ctx.paint(r['id'], 'bold')} {r['version']}  enabled={ctx.paint(str(r['enabled']), 'ok' if r['enabled'] else 'muted')}  tier={r['tier']}  source={r['source'] or 'none'}{' ' + str(r['ref']) if r['ref'] else ''}" + (f" @{str(r['commit'])[:12]}" if r["commit"] else "") + (" (dev link)" if r["link"] else "") + (f" {ctx.paint('PROBLEM:', 'danger')} {r['problem']}" if r["problem"] else ""))
    return 0


# --- install ----------------------------------------------------------------------------------


def _confirm_install(ctx: CliContext, disclosure, *, pre_confirmed: list[str], accept_flags: bool, accept_unlisted: bool = False, prior_kinds: frozenset[str] = frozenset()) -> tuple[bool, list[str], str, str | None]:
    """Return ``(go, confirmed_targets, refusal, unlisted_how)`` after the Requirement 11.7 / 11.4 / 8.8 questions.

    The unlisted-source line of a git reference is confirmed like the one-time
    warning: the user types :data:`floofy_core.consent.ACCEPT_PHRASE`, or
    automation passes ``--accept-unlisted-source`` (``unlisted_how`` records
    ``typed`` / ``flag``). ``--yes`` never covers it (Requirement 8.8, 11.1).

    ``prior_kinds`` are the code kinds the user already said yes to for this very
    mod (the quarantine heal reads them from the parked copy's manifest): the
    host parked the mod without asking, so bringing it back with the *same*
    kinds asks nothing again; a kind the repaired version grew still needs the
    yes. Governance targets, flags and the unlisted-source line are never
    covered by it (Requirement 6.7).
    """
    asked_kinds = [k for k in disclosure.confirm_kinds if k not in prior_kinds]
    if asked_kinds:
        kinds = ", ".join(asked_kinds)
        if not ctx.console.confirm(f"This mod has {kinds} parts that run with gateway privileges or modify payload files. Install {disclosure.mod_id} {disclosure.version}?", default=False):
            return False, [], f"install of {disclosure.mod_id} declined ({kinds} parts need an explicit yes; --yes for automation)", None
    if disclosure.flags and not accept_flags:
        if not ctx.console.confirm(f"Accept the {len(disclosure.flags)} flag(s) above (network/unlisted files) for {disclosure.mod_id}?", default=False):
            return False, [], f"install of {disclosure.mod_id} declined: flags not accepted (--accept-flags or --yes for automation)", None
    unlisted_how: str | None = None
    if disclosure.unlisted_source:
        if accept_unlisted:
            unlisted_how = "flag"
            ctx.console.transcript.append("unlisted source: accepted with --accept-unlisted-source")
        else:
            where = disclosure.unlisted_source.get("ref") or disclosure.unlisted_source.get("url")
            prompt = f"UNLISTED SOURCE: {UNLISTED_SOURCE_LINE}. Type {ACCEPT_PHRASE} to install {disclosure.mod_id} {disclosure.version} from {where} anyway (anything else aborts): "
            if not ctx.console.typed(prompt, ACCEPT_PHRASE, what="unlisted source"):
                return False, [], f"install of {disclosure.mod_id} refused: the unlisted-source line was not confirmed — type {ACCEPT_PHRASE} at the prompt, or pass --accept-unlisted-source for automation (--yes never accepts it; Requirement 8.8)", None
            unlisted_how = "typed"
    confirmed: list[str] = []
    ctx.console.pre_typed.update(pre_confirmed)
    for target in disclosure.governance_targets:
        prompt = f"GOVERNANCE-ALTERING: {disclosure.mod_id} writes to the host governance file {target}. Type the exact path to confirm this one file: "
        if not ctx.console.typed(prompt, target, what=f"governance target {target}"):
            return False, confirmed, f"install of {disclosure.mod_id} refused: governance-altering target {target} was not confirmed by typing its path (--confirm-governance-target {target} for automation; --yes never covers it)", unlisted_how
        confirmed.append(target)
    # the rows follow the last typed answer: a refusal half-way leaves no confirmation row behind (a keyboard-less
    # surface that stops at a missing answer and asks again would otherwise record the earlier targets twice)
    for target in confirmed:
        ctx.audit.record("governance-target-confirm", mod=disclosure.mod_id, version=disclosure.version, files=[target], governanceFlags=["GovernanceAltering"], result="confirmed", detail="pre-confirmed by flag" if target in pre_confirmed else "typed at the prompt")
    return True, confirmed, "", unlisted_how


def _dependency_plan(ctx: CliContext, manifest: dict[str, Any], *, visiting: frozenset[str]) -> list[dict[str, Any]]:
    """What an install must bring along (Requirement 1.4 at install time): every ``dependsOn`` entry not satisfied by an installed mod.

    Each entry is ``{id, range, reason, status, version?, source?, detail?}``:
    ``present`` (installed and in range — nothing to do), ``install`` (found in
    the registry cache at a version in range; ``source`` is the resolved
    ``ResolvedSource``, cleaned up by the caller), ``unavailable`` (not installed
    and not in the registry cache, or nothing known to work on this host),
    ``out-of-range`` (the registry's best version does not satisfy the range),
    ``cycle`` (already being installed up the chain), ``invalid`` (bad range).
    ``floofycrew`` is the framework, never a mod to install — the resolver grades it.
    """
    from ..resolver import FRAMEWORK_ID  # noqa: PLC0415
    from ..semver import InvalidRange, Range  # noqa: PLC0415

    plan: list[dict[str, Any]] = []
    declared = manifest.get("dependsOn")
    if not isinstance(declared, dict):
        return plan
    installed = {m.id: m for m in ctx.mods()}
    for dep_id, spec in sorted((str(k), v) for k, v in declared.items()):
        if dep_id == FRAMEWORK_ID:
            continue
        range_text = spec.get("range") if isinstance(spec, dict) else spec
        reason = str(spec.get("reason")) if isinstance(spec, dict) and spec.get("reason") else None
        entry: dict[str, Any] = {"id": dep_id, "range": str(range_text) if isinstance(range_text, str) else "*", "reason": reason}
        try:
            wanted = Range.parse(range_text) if isinstance(range_text, str) else Range.ANY
        except InvalidRange as exc:
            plan.append({**entry, "status": "invalid", "detail": str(exc)})
            continue
        present = installed.get(dep_id)
        if present is not None:
            try:
                satisfied = wanted.contains(Version.coerce(present.version))
            except InvalidVersion:
                satisfied = False
            if satisfied:
                plan.append({**entry, "status": "present", "version": present.version})
                continue
        if dep_id in visiting:
            plan.append({**entry, "status": "cycle", "detail": "already being installed further up this chain"})
            continue
        try:
            dep_source = resolve_source(dep_id, home=ctx.home, opener_for=ctx.url_opener, **ctx.registry_target())
        except InstallError as exc:
            plan.append({**entry, "status": "unavailable", "detail": str(exc), "installed": present.version if present else None})
            continue
        picked = str(dep_source.version or manifest_of(dep_source.root).get("version") or "")
        try:
            in_range = bool(picked) and wanted.contains(Version.coerce(picked))
        except InvalidVersion:
            in_range = False
        if not in_range:
            dep_source.cleanup()
            plan.append({**entry, "status": "out-of-range", "version": picked or None, "detail": f"the registry's best version for this host is {picked or '?'}, outside {wanted}", "installed": present.version if present else None})
            continue
        plan.append({**entry, "status": "install", "version": picked, "source": dep_source, "tier": dep_source.tier, "installed": present.version if present else None})
    return plan


def _plan_for_disclosure(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The plan without the live source objects (what the disclosure carries and ``--json`` prints)."""
    return [{k: v for k, v in entry.items() if k != "source"} for entry in plan]


def _install_dependencies(ctx: CliContext, plan: list[dict[str, Any]], *, mod_id: str, now: bool, pre_confirmed: list[str], accept_flags: bool, visiting: frozenset[str]) -> list[dict[str, Any]]:
    """Install every ``install`` entry of the plan (deps first, recursively); the other statuses are reported, never fatal.

    A dependency that cannot be installed (unavailable, out of range, declined,
    failed) leaves the requesting mod installed but inactive with the resolver's
    ``Dependency`` reason — the same state as before this step existed — and the
    summary says so, so nothing is silently different from what the user saw.
    """
    results: list[dict[str, Any]] = []
    for entry in plan:
        result = {k: v for k, v in entry.items() if k != "source"}
        if entry["status"] != "install":
            results.append(result)
            continue
        dep_source: ResolvedSource = entry["source"]
        ctx.say(f"dependency: installing {entry['id']} {entry['version']} first — required by {mod_id} ({entry['range']}{'; ' + entry['reason'] if entry.get('reason') else ''})", tone="accent")
        try:
            sub = install_source(ctx, dep_source, now=now, pre_confirmed=pre_confirmed, accept_flags=accept_flags, dependency_of={"id": mod_id, "range": entry["range"], "reason": entry.get("reason")}, _visiting=visiting, defer_reload=True)
        except InstallError as exc:
            ctx.warn(f"dependency {entry['id']} was not installed ({exc}); {mod_id} stays inactive (Dependency) until it is")
            results.append({**result, "status": "failed", "detail": str(exc)})
            continue
        placed = (sub.get("placed") or {}).get("how")
        results.append({**result, "status": "staged" if placed == "staged" else "installed", "placed": placed, "enabled": sub.get("enabled"), "patchParts": any(p.get("kind") in ("patch", "spa") for p in (sub.get("disclosure") or {}).get("parts") or []), "dependencies": sub.get("dependencies") or []})
    return results


def _say_dependency_summary(ctx: CliContext, mod_id: str, results: list[dict[str, Any]]) -> None:
    """The notification: one line per dependency this install brought along or could not (Requirement 1.4)."""
    brought = [r for r in results if r.get("status") in ("installed", "staged")]
    missing = [r for r in results if r.get("status") in ("unavailable", "out-of-range", "failed", "cycle", "invalid")]
    if brought:
        ctx.say(f"  also {'staged' if all(r['status'] == 'staged' for r in brought) else 'installed'} for {mod_id}: " + ", ".join(f"{r['id']} {r.get('version') or ''}".strip() for r in brought) + " (dependencies)", tone="accent")
    for r in missing:
        ctx.warn(f"  {mod_id} requires {r['id']} {r.get('range') or ''}: {r['status']} — {r.get('detail') or ''}; {mod_id} stays inactive (Dependency) until it is installed")


def install_source(ctx: CliContext, source: ResolvedSource, *, now: bool, disabled: bool = False, pre_confirmed: list[str], accept_flags: bool, keep_enabled: bool = False, accept_unlisted: bool = False, dependency_of: dict[str, Any] | None = None, _visiting: frozenset[str] = frozenset(), defer_reload: bool = False, prior_kinds: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Validate → plan dependencies → disclose → confirm → install dependencies → place → seam handlers → flag → audit (→ reload).

    Shared by install/update/profile. ``dependency_of`` marks a recursive call made
    for a ``dependsOn`` entry (``{id, range, reason}`` of the mod that needs it);
    ``defer_reload`` leaves the gateway reload and the Patcher re-apply to the
    outermost call, so one command reloads once.
    """
    report = validate(source.root)
    manifest = manifest_of(source.root) if report.ok else {}
    mod_id = str(manifest.get("id") or source.root.name)
    # Requirement 1.4 at install time: the plan is part of the disclosure, so the one confirmation covers what
    # will ALSO be installed — the manager App answers a whole route call with one "yes"
    plan = _dependency_plan(ctx, manifest, visiting=_visiting | {mod_id}) if report.ok else []
    disclosure = disclose(source.root, report, ctx.governance(), source=source, dependencies=_plan_for_disclosure(plan), dependency_of=dependency_of)
    for line in disclosure.lines():
        ctx.say(line)
    outcome: dict[str, Any] = {"source": source.to_dict(), "disclosure": disclosure.to_dict(), "validation": report.to_dict()}
    # published before the questions, so a surface that has to stop and ask (the manager App's 409) can show the disclosure
    ctx.set_result(**outcome)
    try:
        if not report.ok:
            raise InstallError(f"{disclosure.mod_id}: `floofy validate` found {len(report.errors)} error(s); not installed")
        go, confirmed, refusal, unlisted_how = _confirm_install(ctx, disclosure, pre_confirmed=pre_confirmed, accept_flags=accept_flags or ctx.assume_yes, accept_unlisted=accept_unlisted, prior_kinds=prior_kinds)
        if not go:
            ctx.audit.record("install", mod=disclosure.mod_id, version=disclosure.version, result="declined", detail=refusal, governanceFlags=[f["code"] for f in disclosure.flags], source=source.to_dict(), commit=source.commit, tier=source.tier)
            raise InstallError(refusal)
        # dependencies first (each with its own disclosure and confirmation on a terminal; the App's one "yes" covers the plan)
        outcome["dependencies"] = _install_dependencies(ctx, plan, mod_id=mod_id, now=now, pre_confirmed=pre_confirmed, accept_flags=accept_flags, visiting=_visiting | {mod_id})
    finally:
        for entry in plan:
            if entry.get("source") is not None:
                entry["source"].cleanup()
    running = ctx.gateway_running()
    # Requirement 7.6 / 16.5: staged for the next gateway start while a gateway runs, unless "apply now" — on every
    # surface, the manager App inside the gateway included (its "apply now" reloads the Loader in-process)
    stage = running and not now
    target, placed = place(ctx.home, source, manifest, stage=stage)
    outcome["placed"] = {"where": str(target), "how": placed}
    outcomes = run_handlers(kind_context(ctx), mod_id, target, manifest, "install")
    outcome["parts"] = [o.to_dict() for o in outcomes]
    for part in outcomes:
        ctx.say(f"  part {part.index} {part.kind}: {part.status} — {part.detail}")
    # A seam that refused is not a footnote: the mod is placed, but that part of it is missing
    # (an `app` part refused = "App Not Found" in the sidebar). Say so where the user looks.
    failed_parts = [p for p in outcomes if not p.ok]
    if failed_parts:
        outcome["partErrors"] = [{"index": p.index, "kind": p.kind, "detail": p.detail} for p in failed_parts]
        for part in failed_parts:
            ctx.warn(f"{mod_id}: the {part.kind} part (#{part.index}) was NOT installed — {part.detail}")
    previous = ctx.enabled().get(mod_id)
    # Requirement 11.7: the confirmation above IS the consent — a confirmed install lands enabled;
    # `--disabled` (the App's "install switched off") lands it off on request.
    lands_disabled = disabled
    if keep_enabled and previous is not None:
        enabled_now = previous
    else:
        enabled_now = not lands_disabled
    ctx.set_enabled(mod_id, enabled_now)
    outcome["enabled"] = enabled_now
    current = ctx.current_payload()
    ctx.audit.record(
        "install",
        mod=mod_id,
        version=disclosure.version,
        payload=current.id if current else None,
        files=[p["path"] for p in disclosure.parts],
        governanceFlags=sorted({f["code"] for f in disclosure.flags} | ({"GovernanceAltering"} if confirmed else set())),
        result="ok" if not failed_parts else "partial",
        detail=f"{placed} into {target}; enabled={enabled_now}; confirmed governance targets: {confirmed or '-'}; source {source.kind} {source.ref}" + (f" at commit {source.commit}" if source.commit else "") + (f"; unlisted source accepted ({unlisted_how})" if unlisted_how else "") + ("; parts refused: " + ", ".join(f"{p.kind}#{p.index} ({p.detail})" for p in failed_parts) if failed_parts else ""),
        source=source.to_dict(),
        commit=source.commit,
        tier=source.tier,
        unlistedSource=({**disclosure.unlisted_source, "how": unlisted_how} if disclosure.unlisted_source else None),
    )
    if stage:
        ctx.say(f"{mod_id} {disclosure.version}: staged in pending/ — restart the gateway to apply (or run with --now).")
    else:
        ctx.say(f"{mod_id} {disclosure.version}: {placed} (enabled={enabled_now}).")
        if defer_reload:
            outcome["reload"] = "deferred to the requesting install"
        else:
            if running or ctx.in_gateway:
                outcome["reload"] = reload_gateway(ctx, why=f"install {mod_id}")
                ctx.say(f"  gateway reload: {outcome['reload']}")
            # the outermost call re-applies for itself AND every dependency it installed in this run
            code_parts = any(p["kind"] in ("patch", "spa") for p in disclosure.parts) or any(d.get("patchParts") for d in outcome.get("dependencies") or [])
            if not ctx.in_gateway and enabled_now and code_parts:
                applied = reapply(ctx, confirmed_governance_targets=confirmed, quiet=True)
                outcome["patches"] = applied.to_dict() if applied else None
                if applied is not None:
                    ctx.say(f"  patches re-applied: {'ok' if applied.ok else 'errors'} on {len(applied.payloads)} payload(s)")
    if lands_disabled and not keep_enabled:
        ctx.say(f"  {mod_id} landed switched off (--disabled): `floofy enable {mod_id}` when you are ready.")
    _say_dependency_summary(ctx, mod_id, outcome.get("dependencies") or [])
    return outcome


def install(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.require_consent("install a mod")
    target = ctx.registry_target()
    try:
        source = resolve_source(args.ref, home=ctx.home, expected_sha256=args.sha256, opener_for=ctx.url_opener, git_ref=args.ref_name, **target)
    except InstallError as exc:
        raise CliError(str(exc)) from exc
    try:
        outcome = install_source(ctx, source, now=args.now, disabled=args.disabled, pre_confirmed=list(args.confirm_governance_target), accept_flags=args.accept_flags, accept_unlisted=args.accept_unlisted_source)
    except InstallError as exc:
        raise CliError(str(exc)) from exc
    finally:
        source.cleanup()
    ctx.set_result(**outcome)
    return 0


# --- uninstall -----------------------------------------------------------------------------------


def uninstall(ctx: CliContext, args: argparse.Namespace) -> int:
    mod = ctx.mod(args.id)
    outcome: dict[str, Any] = {"id": mod.id, "version": mod.version}
    if not ctx.console.confirm(f"Uninstall {mod.id} {mod.version}?", default=True):
        raise CliError(f"uninstall of {mod.id} cancelled")
    outcomes = run_handlers(kind_context(ctx), mod.id, mod.dir, mod.manifest, "uninstall") if not mod.problem else []
    outcome["parts"] = [o.to_dict() for o in outcomes]
    for part in outcomes:
        ctx.say(f"  part {part.index} {part.kind}: {part.status} — {part.detail}")
    running = ctx.gateway_running()
    # staged while a gateway runs (a code mod's modules are live), unless --now — inside the gateway too (Requirement 7.6, 16.5)
    stage = running and not args.now and mod.has_code
    if not args.keep_config and (mod.dir / ".floofy").is_dir() and not mod.dir.is_symlink():
        shutil.rmtree(mod.dir / ".floofy", ignore_errors=True)
    if stage:
        ctx.home.pending.mkdir(parents=True, exist_ok=True)
        (ctx.home.pending / f"{mod.id}.remove").write_text(json.dumps({"requestedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n", encoding="utf-8")
        staged_dir = ctx.home.pending / mod.id
        if staged_dir.exists():
            shutil.rmtree(staged_dir)
        outcome["how"] = "staged-remove"
        ctx.say(f"{mod.id}: removal staged (pending/{mod.id}.remove) — restart the gateway to apply, or run with --now.")
    else:
        if mod.dir.is_symlink():
            mod.dir.unlink()
        elif mod.dir.exists():
            shutil.rmtree(mod.dir)
        for extra in (ctx.home.pending / mod.id, ctx.home.spa_dir(mod.id)):
            if extra.exists():
                shutil.rmtree(extra, ignore_errors=True)
        outcome["how"] = "removed"
        ctx.say(f"{mod.id}: removed.")
    ctx.drop_enabled(mod.id)
    ctx.audit.record("uninstall", mod=mod.id, version=mod.version, result="ok", detail=outcome["how"], files=[str(mod.dir)])
    if not stage and (running or ctx.in_gateway):
        outcome["reload"] = reload_gateway(ctx, why=f"uninstall {mod.id}")
    if not stage and not ctx.in_gateway and any(k in ("patch", "spa") for k in mod.kinds):
        applied = reapply(ctx, quiet=True)
        outcome["patches"] = applied.to_dict() if applied else None
    ctx.set_result(**outcome)
    return 0


# --- enable / disable -------------------------------------------------------------------------------


def _flip(ctx: CliContext, args: argparse.Namespace, value: bool) -> int:
    mod = ctx.mod(args.id)
    if mod.problem:
        raise CliError(f"{mod.id}: {mod.problem}")
    ctx.set_enabled(mod.id, value)
    ctx.audit.record("enable" if value else "disable", mod=mod.id, version=mod.version, result="ok")
    outcome: dict[str, Any] = {"id": mod.id, "enabled": value}
    ctx.say(f"{mod.id}: {'enabled' if value else 'disabled'}.")
    if not args.no_reload and (ctx.gateway_running() or ctx.in_gateway):
        outcome["reload"] = reload_gateway(ctx, why=f"{'enable' if value else 'disable'} {mod.id}")
        ctx.say(f"  gateway reload: {outcome['reload']}")
    elif ctx.gateway_running():
        ctx.say("  (gateway not reloaded; the change applies at the next gateway start)")
    if not ctx.in_gateway and any(k in ("patch", "spa") for k in mod.kinds):
        applied = reapply(ctx, quiet=True)
        outcome["patches"] = applied.to_dict() if applied else None
        if applied is not None:
            ctx.say(f"  patches re-applied: {'ok' if applied.ok else 'errors'} on {len(applied.payloads)} payload(s)")
    ctx.set_result(**outcome)
    return 0


def enable(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.require_consent("enable a mod")
    return _flip(ctx, args, True)


def disable(ctx: CliContext, args: argparse.Namespace) -> int:
    return _flip(ctx, args, False)


# --- update ------------------------------------------------------------------------------------------


def update(ctx: CliContext, args: argparse.Namespace) -> int:
    if not args.check:
        ctx.require_consent("update mods")
    cache = IndexCache.load(ctx.home)
    if not cache.mods:
        raise CliError("the registry cache is empty: " + ("; ".join(cache.notes) if cache.notes else "nothing to update from") + " (floofy registry refresh)")
    current = ctx.current_payload()
    target = ctx.registry_target()
    wanted = set(args.ids)
    plan: list[dict[str, Any]] = []
    for mod in ctx.mods(include_broken=False):
        if wanted and mod.id not in wanted:
            continue
        source = read_source(mod.dir)
        key = source.get("registryKey") or mod.id
        if not wanted and not args.all and source.get("source") != "registry":
            continue
        best = cache.best_version(str(key), **target)
        picked = best.version
        row: dict[str, Any] = {"id": mod.id, "key": str(key), "installed": mod.version, "candidate": picked.version if picked else None, "verdict": best.verdict, "why": best.why, "inRegistry": best.entry is not None}
        try:
            newer = picked is not None and Version.parse(picked.version) > Version.parse(mod.version)
        except InvalidVersion:
            newer = False
        row["update"] = newer
        # the release notes of the candidate (Requirement 16.6): the record's changelog URL, else the mod's repository
        row["changelog"] = (picked.changelog if picked is not None else None) or (best.entry.repo if best.entry is not None else None)
        plan.append(row)
    ctx.set_result(plan=plan, applied=[])
    for row in plan:
        if row["update"]:
            ctx.say(f"{row['id']}: {row['installed']} -> {row['candidate']}" + (f" ({row['verdict']} here)" if row["verdict"] else " (untested here)") + (f" — release notes: {row['changelog']}" if row.get("changelog") else ""))
        else:
            ctx.say(f"{row['id']}: up to date at {row['installed']}" + (f" ({row['why']})" if row["why"] else ""))
    if args.check:
        return 0
    for row in [r for r in plan if r["update"]]:
        source: ResolvedSource | None = None
        try:
            source = resolve_source(f"{row['key']}@{row['candidate']}", home=ctx.home, opener_for=ctx.url_opener, **target)
            outcome = install_source(ctx, source, now=args.now, pre_confirmed=list(args.confirm_governance_target), accept_flags=args.accept_flags, keep_enabled=True)
            ctx.result["applied"].append({"id": row["id"], "version": row["candidate"], "outcome": outcome})
        except InstallError as exc:
            ctx.warn(f"{row['id']}: update failed: {exc}")
            ctx.result["applied"].append({"id": row["id"], "version": row["candidate"], "error": str(exc)})
        finally:
            if source is not None:
                source.cleanup()
    failed = [a for a in ctx.result["applied"] if "error" in a]
    if ctx.result["applied"]:
        # one summary row for the operation; each successful update also wrote its own `install` row
        ctx.audit.record(
            "update",
            payload=current.id if current else None,
            result="ok" if not failed else ("partial" if len(failed) < len(ctx.result["applied"]) else "error"),
            detail="; ".join(f"{a['id']} -> {a['version']}" + (f" failed: {a['error']}" if "error" in a else "") for a in ctx.result["applied"]),
            mods=[{"id": a["id"], "version": a["version"], "ok": "error" not in a} for a in ctx.result["applied"]],
        )
    return 0 if not failed else 1


# --- dev ---------------------------------------------------------------------------------------------


def dev(ctx: CliContext, args: argparse.Namespace) -> int:
    checkout = Path(args.path).expanduser().resolve()
    if not (checkout / "floofy.json").is_file():
        raise CliError(f"{checkout} has no floofy.json")
    manifest = manifest_of(checkout)
    mod_id = str(manifest["id"])
    link = ctx.home.ensure().mod_dir(mod_id)
    outcome: dict[str, Any] = {"id": mod_id, "link": str(link), "target": str(checkout)}
    if args.unlink:
        if link.is_symlink():
            link.unlink()
            ctx.drop_enabled(mod_id)
            ctx.audit.record("dev-unlink", mod=mod_id, result="ok", files=[str(link)])
            ctx.say(f"{mod_id}: dev link removed")
        else:
            ctx.say(f"{mod_id}: no dev link present")
        if not args.no_reload and (ctx.gateway_running() or ctx.in_gateway):
            outcome["reload"] = reload_gateway(ctx, why=f"dev unlink {mod_id}")
        ctx.set_result(**outcome)
        return 0
    ctx.require_consent("link a dev mod")
    if link.exists() and not link.is_symlink():
        raise CliError(f"mods/{mod_id} is a real installed directory; `floofy uninstall {mod_id}` first")
    report = validate(checkout)
    for finding in (*report.errors, *report.warnings):
        ctx.say("  " + finding.format())
    if link.is_symlink():
        link.unlink()
    link.symlink_to(checkout, target_is_directory=True)
    ctx.set_enabled(mod_id, True)
    outcomes = run_handlers(kind_context(ctx), mod_id, link, manifest, "install")
    outcome["parts"] = [o.to_dict() for o in outcomes]
    app_names = [str(o.extra.get("appName")) for o in outcomes if o.kind == "app" and o.extra.get("appName")]
    launcher = ctx.launcher()
    if app_names and launcher is not None:
        from ..hostcli import run_host_cli  # noqa: PLC0415

        for app_name in app_names:
            result = run_host_cli(launcher, ["app", "dev", app_name, "--confirm-out-of-install-root"], host_home=ctx.host_home, extra_env=ctx.host_cli_env())
            outcome.setdefault("hostDevMode", []).append(result.to_dict())
            ctx.say(f"  host dev mode for app {app_name}: {'on' if result.ok else result.output[-200:]}")
    ctx.audit.record("dev-link", mod=mod_id, version=str(manifest.get("version") or ""), result="ok", files=[str(link), str(checkout)], detail="symlinked checkout; enabled")
    ctx.say(f"{mod_id}: linked {link} -> {checkout} and enabled (validate: {len(report.errors)} error(s), {len(report.warnings)} warning(s))")
    if not args.no_reload and (ctx.gateway_running() or ctx.in_gateway):
        outcome["reload"] = reload_gateway(ctx, why=f"dev link {mod_id}")
        ctx.say(f"  gateway reload: {outcome['reload']}")
    if not ctx.in_gateway and any(p.get("kind") in ("patch", "spa") for p in manifest.get("parts") or []):
        applied = reapply(ctx, quiet=True)
        outcome["patches"] = applied.to_dict() if applied else None
    ctx.set_result(**outcome)
    if args.follow:
        _follow(ctx, mod_id)
    return 0


def _follow(ctx: CliContext, mod_id: str) -> None:
    """Stream ``mods/<id>/.floofy/mod.log`` and the Loader's faults for this mod until Ctrl-C (Requirement 14.2)."""
    log_path = ctx.home.mod_runtime_dir(mod_id) / "mod.log"
    state_path = ctx.home.loader_state
    ctx.say(f"following {log_path} and Loader faults for {mod_id} (Ctrl-C to stop)")
    offset = log_path.stat().st_size if log_path.is_file() else 0
    seen_faults = 0
    try:
        while True:
            if log_path.is_file():
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    for line in handle:
                        ctx.say(line.rstrip("\n"))
                    offset = handle.tell()
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                faults = [f for f in state.get("faults", []) if f.get("mod") == mod_id]
                for fault in faults[seen_faults:]:
                    ctx.say(f"FAULT {fault.get('ts')}: {fault.get('source')}: {fault.get('message')}")
                seen_faults = len(faults)
            except (OSError, ValueError):
                pass
            time.sleep(1.0)
    except KeyboardInterrupt:
        ctx.say("stopped")
