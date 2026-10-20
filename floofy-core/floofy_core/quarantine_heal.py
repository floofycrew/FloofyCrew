"""Healing the quarantine: a parked mod comes back when the registry grades a version safe here (Requirement 6.7).

Yeet (:mod:`floofy_core.hostchange`) parks a mod under ``quarantine/<hostver>/``
when a host update breaks it. Until 1.3.7 the only way back was a **rollback** of
the host (the set parked under the version the host rolled back to) or the
user's own ``floofy yeet --restore``. Nothing ever revisited the quarantine when
the *mod* was repaired: the Forge would grade a fixed version ``tested`` on the
new host, the registry would carry the row, the hourly freshness pass would even
fetch it — and the mod stayed parked. Measured on the testing desk 2026-10-19:
``custom-themes`` yeeted on 0.8.0.4 (fingerprint miss), ``custom-themes 1.0.2``
graded ``tested`` on 0.8.0.6 two days later, still parked seventeen days on.

:func:`heal` closes that loop. For every parked mod it asks the registry cache
the same question ``floofy update`` asks for an installed one —
:meth:`floofy_core.registry.IndexCache.best_version` for the **current** host —
and when the answer is a version graded ``tested`` or ``expected`` here (the
parked version itself, now graded clean, or a newer one), it installs that
version from the registry through the ordinary install path and drops the
parked copy. The mod comes back with the enabled flag it was parked with
(``keep_enabled`` semantics), its seam parts reinstalled, its patches re-applied
on the next re-apply. Anything else stays parked, with the reason recorded:

* no registry entry (a git or path install the registry does not know) — the
  user's own ``floofy yeet --restore`` is the way back;
* no version graded for this host yet — the Forge has not caught up; wait;
* the install path refused — a disclosure that needs an answer is **never**
  answered by this unattended path; the entry says so and the user installs by
  hand. The one thing carried forward is the yes the user already gave for this
  mod's code kinds (``patch``/``spa``/``python-hook``, read from the parked
  copy's manifest): the host parked the mod without asking, so the same kinds
  coming back ask nothing again. A kind the repaired version *grew*, a
  governance-altering target, a network/unlisted-file flag or an unlisted
  source still stop the heal.

Where it runs: the re-apply trigger's freshness pass (hourly, right after the
registry refresh, so a row the Forge published this hour acts this hour), the
host-version-change handler right after yeeting (a host that lands directly on
a version the matrix already covers does not park and wait an hour), and
``floofy yeet --heal`` for the explicit form. The ``yeet-heal`` audit row names
every restored, skipped and failed mod.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .datahome import DataHome
from .hostchange import list_quarantine
from .modstore import read_enabled, write_enabled
from .semver import InvalidVersion, Version

__all__ = ["heal", "plan"]

#: Verdicts that bring a parked mod back (the ranking :meth:`IndexCache.best_version` already applies).
HEALING_VERDICTS = frozenset({"tested", "expected"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parked(home: DataHome) -> list[dict[str, Any]]:
    """Every parked mod: ``{id, version, hostVersion, dir, enabled, registryKey}``."""
    from .modstore import read_source  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    for host_version, ids in list_quarantine(home).items():
        flags = read_enabled(home.quarantine_dir(host_version) / "enabled.json")
        for mod_id in ids:
            mod_dir = home.quarantine_dir(host_version) / mod_id
            try:
                manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
            source = read_source(mod_dir)
            kinds = sorted({str(p.get("kind")) for p in (manifest.get("parts") or []) if isinstance(p, dict) and p.get("kind")})
            rows.append({"id": mod_id, "version": str(manifest.get("version") or "0.0.0"), "hostVersion": host_version, "dir": mod_dir, "enabled": flags.get(mod_id, True), "registryKey": source.get("registryKey") or (mod_id if source.get("source") == "registry" else None), "source": source.get("source"), "kinds": kinds})
    return rows


def plan(ctx: Any) -> list[dict[str, Any]]:
    """What :func:`heal` would do, without doing it: one row per parked mod with ``action`` ``restore`` / ``wait`` / ``manual``."""
    from .registry import IndexCache  # noqa: PLC0415

    parked = _parked(ctx.home)
    if not parked:
        return []
    cache = IndexCache.load(ctx.home)
    target = ctx.registry_target()
    rows: list[dict[str, Any]] = []
    for entry in parked:
        row = {k: v for k, v in entry.items() if k != "dir"}
        row["dir"] = str(entry["dir"])
        if ctx.home.mod_dir(entry["id"]).exists() or ctx.home.mod_dir(entry["id"]).is_symlink():
            row.update(action="manual", why=f"mods/{entry['id']} exists again; the parked copy is a leftover (floofy yeet --restore {entry['hostVersion']} {entry['id']} to see, or delete it)")
            rows.append(row)
            continue
        key = entry["registryKey"] or entry["id"]
        best = cache.best_version(str(key), **target) if cache.mods else None
        if best is None or best.entry is None:
            row.update(action="manual", why=f"{key!r} is not in the registry cache; restore by hand once it works here (floofy yeet --restore {entry['hostVersion']} {entry['id']})")
            rows.append(row)
            continue
        if best.version is None or best.verdict not in HEALING_VERDICTS:
            row.update(action="wait", why=best.why or f"no version of {key} is graded for host {target.get('host_version')} yet")
            rows.append(row)
            continue
        try:
            older = Version.parse(best.version.version) < Version.parse(entry["version"])
        except InvalidVersion:
            older = False
        if older:
            row.update(action="wait", why=f"the only version graded here is {best.version.version}, older than the parked {entry['version']}; waiting for a repair")
            rows.append(row)
            continue
        row.update(action="restore", candidate=best.version.version, verdict=best.verdict, key=str(key))
        rows.append(row)
    return rows


def heal(ctx: Any, *, now: bool = False, defer_reload: bool = False) -> dict[str, Any]:
    """Bring back every parked mod the registry grades safe on the current host; never raises.

    ``now`` is ``floofy install --now`` (apply while the gateway runs instead of
    staging); the unattended callers leave it off and let the Loader pick the mod
    up at its next start or reload, exactly like an update. Returns ``{restored,
    waiting, manual, failed}``.
    """
    outcome: dict[str, Any] = {"ts": _now(), "restored": [], "waiting": [], "manual": [], "failed": []}
    try:
        rows = plan(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken cache must not fail the trigger
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome
    if not rows:
        return outcome
    for row in rows:
        if row["action"] == "wait":
            outcome["waiting"].append(row)
        elif row["action"] == "manual":
            outcome["manual"].append(row)
    todo = [r for r in rows if r["action"] == "restore"]
    if todo:
        from .cli.cmd_mods import install_source  # noqa: PLC0415
        from .installer import InstallError, resolve_source  # noqa: PLC0415

        target = ctx.registry_target()
        for row in todo:
            source = None
            try:
                source = resolve_source(f"{row['key']}@{row['candidate']}", home=ctx.home, opener_for=ctx.url_opener, **target)
                # the parked flag decides the enabled state the mod comes back with: write it first, keep_enabled reads it
                flags = read_enabled(ctx.home.enabled)
                flags[row["id"]] = bool(row["enabled"])
                write_enabled(ctx.home.enabled, flags)
                installed = install_source(ctx, source, now=now, pre_confirmed=[], accept_flags=False, keep_enabled=True, defer_reload=defer_reload, prior_kinds=frozenset(row.get("kinds") or ()))
                _drop_parked(ctx.home, row)
                outcome["restored"].append({**{k: row[k] for k in ("id", "version", "hostVersion", "candidate", "verdict", "key")}, "enabled": bool(row["enabled"]), "outcome": installed})
                ctx.say(f"quarantine: {row['id']} {row['version']} (parked on {row['hostVersion']}) -> {row['candidate']} ({row['verdict']} on this host), enabled={row['enabled']}")
            except InstallError as exc:
                # a declined disclosure (typed governance target, unlisted source, a code-kind confirmation) is reported, never answered
                flags = read_enabled(ctx.home.enabled)
                if flags.pop(row["id"], None) is not None:
                    write_enabled(ctx.home.enabled, flags)
                outcome["failed"].append({**{k: row[k] for k in ("id", "version", "hostVersion", "candidate")}, "error": str(exc)})
                ctx.warn(f"quarantine: {row['id']} stays parked: {exc}")
            except Exception as exc:  # noqa: BLE001 - one bad download must not stop the others
                outcome["failed"].append({**{k: row[k] for k in ("id", "version", "hostVersion", "candidate")}, "error": f"{type(exc).__name__}: {exc}"})
                ctx.warn(f"quarantine: {row['id']} stays parked: {type(exc).__name__}: {exc}")
            finally:
                if source is not None:
                    source.cleanup()
    if outcome["restored"] or outcome["failed"]:
        ctx.audit.record(
            "yeet-heal",
            result="ok" if not outcome["failed"] else ("partial" if outcome["restored"] else "error"),
            detail="; ".join([f"{r['id']} {r['version']} -> {r['candidate']} ({r['verdict']})" for r in outcome["restored"]] + [f"{f['id']} failed: {f['error'][:160]}" for f in outcome["failed"]]),
            mods=[{"id": r["id"], "version": r["candidate"], "ok": True} for r in outcome["restored"]] + [{"id": f["id"], "version": f["candidate"], "ok": False} for f in outcome["failed"]],
        )
    return outcome


def _drop_parked(home: DataHome, row: dict[str, Any]) -> None:
    """The parked copy and its flag go once the registry version is installed; an emptied quarantine dir goes too."""
    parked_dir = Path(row["dir"])
    if parked_dir.is_symlink():
        parked_dir.unlink()
    elif parked_dir.exists():
        shutil.rmtree(parked_dir, ignore_errors=True)
    version_dir = home.quarantine_dir(row["hostVersion"])
    flags_path = version_dir / "enabled.json"
    flags = read_enabled(flags_path)
    if flags.pop(row["id"], None) is not None:
        if flags:
            write_enabled(flags_path, flags)
        else:
            flags_path.unlink(missing_ok=True)
    if version_dir.is_dir() and not any(version_dir.iterdir()):
        version_dir.rmdir()
