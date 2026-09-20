"""``floofy registry {add,remove,list,refresh}``, ``floofy audit``, ``floofy which`` (Requirement 8.3, 8.4, 8.5, 11.3, 11.5).

* ``registry add <url> [--trust index|owner] [--allow-unsigned] [--key-id ID] [--name NAME] [--no-refresh]``
  records a source in ``registries.json`` (plaintext ``http://`` refused unless
  loopback — Requirement 11.6) and fetches it right away. ``--allow-unsigned`` is
  the user's explicit loosening of the default signature requirement: it prints a
  prominent warning and writes an audit row ``registry-trust-loosened`` with the
  flag ``UnsignedIndexAccepted`` (Requirement 8.3, 11.3).
* ``registry refresh [SOURCE]`` fetches ``index.json`` / ``compat.json`` (+ ``.sig``)
  over HTTPS into ``cache/sources/<key>/``, asks the verification hook, refuses an
  unverified index unless the source allows unsigned, and rebuilds the merged
  cache from the usable sources; the ``registry-refresh`` audit row records the
  per-source decision (``sources[] {label, url, status, usable, allowUnsigned}``).
* ``registry remove <url|key|name>`` drops the source and rebuilds the merged cache.
* ``registry list`` shows every source with its trust settings and cache state.
* ``audit [--tail N] [--op OP …] [--json]`` reads ``audit.jsonl`` (Requirement 11.5).
* ``which <sha256>`` resolves a file hash to ``mod@version`` through the cached
  index and the installed mods' ``files[]`` (Requirement 8.5).
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from ..audit import read_audit
from ..installer import InstallError
from ..registry import IndexCache
from ..registry_sources import STATUS_VERIFIED, TRUST_LEVELS, Source, SourceStore, rebuild_merged, refresh
from .context import CliContext, CliError

__all__ = ["audit_cmd", "register", "registry", "which"]

LOOSENING_WARNING = (
    "TRUST LOOSENED for {url}: its index will be used WITHOUT a verified signature. "
    "A tampered index can point you at tampered mod files (file hashes are still checked, but against that index). "
    "This is your choice and it is recorded in the audit log (registry-trust-loosened); "
    "`floofy registry add {url}` without --allow-unsigned restores the default."
)


def register(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("registry", help="registry sources and their trust; refresh the local cache")
    actions = r.add_subparsers(dest="registry_action", required=True)
    add = actions.add_parser("add", help="add (or update) a source")
    add.add_argument("url", help="https URL of the registry directory (index.json, compat.json and .sig files live there)")
    add.add_argument("--trust", choices=TRUST_LEVELS, default="index", help="what you trust the source for (default index)")
    add.add_argument("--allow-unsigned", action="store_true", help="accept this source's index without a verified signature (warned, audited)")
    add.add_argument("--key-id", default=None, help="the signing key id you expect")
    add.add_argument("--name", default=None, help="a short name for the source (also the namespace prefix when a mod id collides across sources)")
    add.add_argument("--no-refresh", action="store_true", help="do not fetch right away")
    rm = actions.add_parser("remove", help="remove a source (by url, key or name)")
    rm.add_argument("ref")
    actions.add_parser("list", help="list the sources and their cache state")
    rf = actions.add_parser("refresh", help="fetch index.json/compat.json of every (or one) source into the cache")
    rf.add_argument("source", nargs="?", default=None)
    r.set_defaults(handler=registry)

    a = sub.add_parser("audit", help="read the audit log (every mutating operation)")
    a.add_argument("--tail", type=int, default=None, help="only the last N rows")
    a.add_argument("--op", action="append", default=[], help="only these operations (repeatable)")
    a.set_defaults(handler=audit_cmd)

    w = sub.add_parser("which", help="resolve a file's sha256 to mod@version (registry cache and installed mods)")
    w.add_argument("sha256")
    w.set_defaults(handler=which)


def registry(ctx: CliContext, args: argparse.Namespace) -> int:
    try:
        store = SourceStore.load(ctx.home)
    except InstallError as exc:
        raise CliError(str(exc)) from exc
    action = args.registry_action
    if action == "list":
        return _list(ctx, store)
    if action == "add":
        return _add(ctx, store, args)
    if action == "remove":
        removed = store.remove(args.ref)
        if removed is None:
            raise CliError(f"no registry source matches {args.ref!r}")
        store.save()
        mods, rows = rebuild_merged(ctx.home, store)
        ctx.audit.record("registry-remove", result="ok", detail=f"{removed.url} ({removed.label})", files=[str(ctx.home.registries), str(ctx.home.index_cache)])
        ctx.set_result(removed=removed.to_dict(), mergedMods=mods, mergedCompatRows=rows)
        ctx.say(f"removed {removed.url}; merged cache now holds {mods} mod(s) and {rows} compat row(s)")
        return 0
    if action == "refresh":
        return _refresh(ctx, store, only=args.source)
    raise CliError(f"unknown registry action {action!r}", exit_code=2)


def _list(ctx: CliContext, store: SourceStore) -> int:
    rows = []
    for source in store.sources:
        meta_path = ctx.home.cache_sources / source.key / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None
        rows.append({**source.to_dict(), "cache": meta})
    ctx.set_result(sources=rows, cache=IndexCache.load(ctx.home).to_dict())
    if not rows:
        ctx.say("no registry sources (floofy registry add <url>)")
    for row in rows:
        cache = row["cache"] or {}
        state = "never fetched" if not cache else ("usable" if cache.get("usable") else f"refused: {cache.get('refusal') or cache.get('error')}")
        ctx.say(f"{row['label']}: {row['url']} trust={row['trust']} allowUnsigned={row['allowUnsigned']} keyId={row['keyId'] or '-'} — {state}" + (f" ({cache.get('mods')} mod(s), signature {cache.get('signature', {}).get('status')}, fetched {cache.get('fetchedAt')})" if cache.get("mods") is not None else ""))
    return 0


def _add(ctx: CliContext, store: SourceStore, args: argparse.Namespace) -> int:
    try:
        source, loosened = store.add(Source(args.url, args.trust, args.allow_unsigned, args.key_id, args.name))
    except InstallError as exc:
        raise CliError(str(exc)) from exc
    store.save()
    ctx.audit.record("registry-add", result="ok", detail=f"{source.url} ({source.label}) trust={source.trust} allowUnsigned={source.allow_unsigned} keyId={source.key_id or '-'}", files=[str(ctx.home.registries)])
    ctx.say(f"registry source recorded: {source.url} (label {source.label}, trust={source.trust})")
    if source.allow_unsigned:
        ctx.warn(LOOSENING_WARNING.format(url=source.url))
        if loosened:
            ctx.audit.record("registry-trust-loosened", result="ok", detail=f"{source.url} ({source.label}): allowUnsigned=true (trust={source.trust})", files=[str(ctx.home.registries)], governanceFlags=["UnsignedIndexAccepted"])
    ctx.set_result(source=source.to_dict(), loosened=loosened)
    if args.no_refresh:
        # the merged cache follows the current trust settings even without a refetch
        mods, rows = rebuild_merged(ctx.home, store)
        ctx.set_result(mergedMods=mods, mergedCompatRows=rows)
        return 0
    return _refresh(ctx, store, only=source.key)


def _refresh(ctx: CliContext, store: SourceStore, *, only: str | None) -> int:
    if not store.sources:
        raise CliError("no registry sources to refresh (floofy registry add <url>)")
    if only and store.find(only) is None:
        raise CliError(f"no registry source matches {only!r}")
    try:
        outcome = refresh(ctx.home, store, only=only)
    except InstallError as exc:
        raise CliError(str(exc)) from exc
    ctx.set_result(**outcome.to_dict())
    for record in outcome.sources:
        signature = record.get("signature") or {}
        if record.get("error"):
            ctx.warn(f"{record['url']}: {record['error']}")
        elif record.get("usable"):
            accepted = "" if signature.get("status") == STATUS_VERIFIED else " (accepted on your allowUnsigned)"
            ctx.say(f"{record['label']}: {record['mods']} mod(s), signature {signature.get('status')}{accepted}, compat {'yes' if record.get('compat') else 'no'}")
            if record.get("warning"):
                ctx.warn(record["warning"])
        else:
            ctx.warn(f"{record['url']}: {record.get('refusal')}")
    ctx.say(f"merged cache: {outcome.merged_mods} mod(s) from {len(outcome.usable)} usable source(s), {outcome.merged_rows} compat row(s); refused {len(outcome.refused)}")
    ctx.audit.record(
        "registry-refresh",
        result="ok" if not outcome.refused else ("partial" if outcome.usable else "refused"),
        detail=f"usable {len(outcome.usable)}, refused {len(outcome.refused)}; merged {outcome.merged_mods} mod(s)",
        files=[str(ctx.home.index_cache), str(ctx.home.compat_cache)],
        sources=outcome.decisions(),
    )
    return 0 if not outcome.refused or outcome.usable else 1


def audit_cmd(ctx: CliContext, args: argparse.Namespace) -> int:
    if args.tail is not None and args.tail < 0:
        raise CliError("--tail takes a non-negative count", exit_code=2)
    rows = read_audit(ctx.home.audit, tail=args.tail, ops=args.op or None)
    ctx.set_result(rows=rows, path=str(ctx.home.audit), count=len(rows))
    if not rows:
        ctx.say(f"no audit rows in {ctx.home.audit}")
    for row in rows:
        who = f"{row.get('actor', '?')}/{row.get('by', '?')}"
        subject = (f" {row['mod']}" + (f"@{row['version']}" if row.get("version") else "")) if row.get("mod") else ""
        flags = f" governance={row['governanceFlags']}" if row.get("governanceFlags") else ""
        ctx.say(f"{row.get('ts')} {row.get('op')}{subject} [{who}] {row.get('result') or ''}{flags}" + (f" — {row['detail']}" if row.get("detail") else ""))
    return 0


def which(ctx: CliContext, args: argparse.Namespace) -> int:
    digest = args.sha256.lower().strip()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise CliError("expected a 64-hex-digit sha256", exit_code=2)
    cache = IndexCache.load(ctx.home)
    hits: list[dict[str, Any]] = [{"where": "registry", "mod": entry.key, "version": version.version, "file": file.get("url") or file.get("path"), "source": entry.source} for entry, version, file in cache.by_hash(digest)]
    for mod in ctx.mods(include_broken=False):
        for entry in mod.manifest.get("files") or []:
            if isinstance(entry, dict) and str(entry.get("sha256", "")).lower() == digest:
                hits.append({"where": "installed", "mod": mod.id, "version": mod.version, "file": entry.get("path"), "source": str(mod.dir)})
    ctx.set_result(sha256=digest, hits=hits)
    if not hits:
        ctx.say(f"{digest[:12]}…: not in the registry cache nor in any installed mod")
        return 1
    for hit in hits:
        ctx.say(f"{hit['mod']}@{hit['version']} ({hit['where']}): {hit['file']}")
    return 0
