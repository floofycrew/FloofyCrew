"""Unattended update freshness for the re-apply trigger (Requirement 7.7).

The App reminds the user about updates — FloofyCrew's own release banner and the
per-mod update rows — from **cached** data: ``cache/self-update.json`` (the daily
release check) and the registry source caches. The Loader deliberately performs
no network I/O, and until 1.3.1 the caches were only written when the user ran
``floofy doctor``/``status``/``self-update`` or refreshed a source by hand — a
user who never opened a terminal was never reminded.

:func:`run` closes that gap from the one place that already runs unattended,
outside the gateway and with the user's own credentials: the re-apply trigger's
``floofy apply --if-changed`` (the hourly user timer on the internal edition,
the PATH wrapper on the public one; design DR-3). It performs, best-effort:

* the **release check** — :func:`floofy_core.cli.cmd_selfupdate.update_check`,
  which is itself cached for 24 h and honours ``updates.check``, so the hourly
  trigger costs at most one GET a day;
* a **registry-source refresh** at most once every :data:`REFRESH_TTL` seconds,
  judged by the newest ``meta.json`` ``fetchedAt`` across the configured
  sources (a source the user added but never fetched does not force one — the
  user has not opted into that registry's traffic until the first explicit
  refresh wrote a cache; missing/broken metadata among fetched sources counts as
  stale);
* the **edition's default source** (Requirement 8.12), before the two above:
  recorded when it is missing from ``registries.json`` (add-only — a row the
  user has keeps its trust settings) and fetched on every tick while its cache
  is not usable (never fetched, refused, errored), because FloofyCrew's own
  registry is the one source the user consented to at ``floofy init``. Once it
  is usable it falls under the daily rule like every other source.

Both steps are silent and never raise: the trigger's job is re-applying patches,
and an expired credential, an offline desk or a broken feed must not fail it —
the caches simply stay at their previous state (the release check grades itself
``unreachable`` and keeps the previous ``latest``). ``updates.check`` false
disables the two update steps (the default source is still recorded, not
fetched); the audit trail comes from the steps themselves
(``registry-refresh`` rows carry ``actor=trigger`` via ``FLOOFY_ACTOR``).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

__all__ = ["REFRESH_TTL", "run"]

#: How old the newest fetched source cache may be before the trigger refreshes (seconds).
REFRESH_TTL = 24 * 60 * 60


def _parse_iso(text: Any) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _newest_fetch(ctx: Any, sources: list[Any]) -> tuple[float | None, bool]:
    """(newest ``fetchedAt`` across fetched sources, whether any source has been fetched at all)."""
    newest: float | None = None
    any_fetched = False
    for source in sources:
        meta_path = ctx.home.cache_sources / source.key / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        any_fetched = True
        stamp = _parse_iso(meta.get("fetchedAt"))
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest, any_fetched


def _default_source_step(ctx: Any, *, fetch: bool) -> str:
    """Requirement 8.12 on the trigger: record the edition's default source when missing, fetch it while its cache is unusable."""
    from .cli.cmd_registry import ensure_default_sources, fetch_default_sources  # noqa: PLC0415
    from .registry_sources import SourceStore  # noqa: PLC0415

    store = SourceStore.load(ctx.home)
    record = ensure_default_sources(ctx, store, why="re-apply trigger")
    if record["status"] in ("none", "error"):
        return record["status"] if record["status"] == "none" else f"error: {record.get('error')}"
    if not record["pending"]:
        return f"{record['status']}, usable"
    if not fetch:
        return f"{record['status']}, not fetched ({'offline' if ctx.offline else 'updates.check false'})"
    outcome = fetch_default_sources(ctx, store, record["pending"], why="re-apply trigger")
    return f"{record['status']}, fetched: usable {len(outcome['usable'])}, refused {len(outcome['refused'])}"


def run(ctx: Any, *, now: float | None = None) -> dict[str, Any]:
    """The trigger's freshness pass; returns a small report for ``--json`` documents. Never raises."""
    report: dict[str, Any] = {"selfUpdate": "skipped", "registry": "skipped", "defaultSource": "skipped"}
    try:
        enabled = bool(ctx.settings().get("updates.check"))
    except Exception:  # noqa: BLE001 - unreadable settings never fail the trigger
        enabled = True

    # 0. the edition's default source: recorded when missing (no network), fetched while unusable (Requirement 8.12)
    try:
        report["defaultSource"] = _default_source_step(ctx, fetch=enabled and not ctx.offline)
    except Exception as exc:  # noqa: BLE001
        report["defaultSource"] = f"error: {exc}"

    if ctx.offline:
        report["selfUpdate"] = report["registry"] = "offline"
        return report
    if not enabled:
        report["selfUpdate"] = report["registry"] = "disabled (updates.check false)"
        return report

    # 1. the daily release check (its own 24 h cache makes the hourly trigger cheap)
    try:
        from .cli.cmd_selfupdate import update_check  # noqa: PLC0415

        outcome = update_check(ctx)
        report["selfUpdate"] = outcome.status
    except Exception as exc:  # noqa: BLE001
        report["selfUpdate"] = f"error: {exc}"

    # 2. the registry refresh, at most once per REFRESH_TTL
    try:
        from .registry_sources import SourceStore, refresh  # noqa: PLC0415

        store = SourceStore.load(ctx.home)
        if not store.sources:
            report["registry"] = "no sources"
            return report
        newest, any_fetched = _newest_fetch(ctx, store.sources)
        if not any_fetched:
            report["registry"] = "never fetched (waiting for the user's first explicit refresh)"
            return report
        moment = time.time() if now is None else now
        if newest is not None and moment - newest < REFRESH_TTL:
            report["registry"] = f"fresh (fetched {datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()})"
            return report
        outcome = refresh(ctx.home, store, opener_for=ctx.url_opener)
        report["registry"] = f"refreshed: {outcome.merged_mods} mod(s) from {len(outcome.usable)} usable source(s), refused {len(outcome.refused)}"
        ctx.audit.record(
            "registry-refresh",
            result="ok" if not outcome.refused else ("partial" if outcome.usable else "refused"),
            detail=f"unattended (re-apply trigger): usable {len(outcome.usable)}, refused {len(outcome.refused)}; merged {outcome.merged_mods} mod(s)",
            files=[str(ctx.home.index_cache), str(ctx.home.compat_cache)],
            sources=outcome.decisions(),
        )
    except Exception as exc:  # noqa: BLE001
        report["registry"] = f"error: {exc}"
    return report
