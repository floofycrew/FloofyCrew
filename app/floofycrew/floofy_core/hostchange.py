"""Host-version-change handling and the per-version quarantine (Requirement 6.2, 6.3, 6.6; design "Yeet").

``floofy apply --if-changed`` — run by the hourly timer, the PATH wrapper and
the Loader's ``host.version_changed`` — compares discovery with
``host-state.json`` (:class:`HostState`, :func:`detect_change`); a quiet hour
costs one discovery. When the payload set or the current host version changed,
:func:`handle_host_change` runs, in the order Requirement 6.2 lists:

(a) the SPA reporter offline (:func:`floofy_core.spa_report.check_bundle_fingerprints`
    on the current payload's ``static/dist``) and the Python anchors
    (:mod:`floofy_core.anchors`) — the matrix shape, recorded for the UI and the Forge;
(b) the compat matrix (``cache/compat.json``) row for ``edition × channel × hostVersion``;
(c) re-apply: revert-then-patch every payload with the enabled set
    (fingerprints that still match land, the others are reported as skips);
(d) **yeet**: mods whose manifest (``kirocrew.version`` with ``strict``), matrix
    cell (``broken``) or fingerprints (a ``FingerprintMiss``/``FingerprintAmbiguous``
    skip of their own patch) do not cover the new version — plus the Loader's
    ``quarantine/requests/<id>.json`` — move to ``quarantine/<hostver>/<id>/``
    (``<hostver>`` is the version they last worked on: the previous current
    version when known, else the new one), their seam parts are uninstalled,
    their ``enabled.json`` flag is preserved in ``quarantine/<hostver>/enabled.json``
    and dropped from the live file, reason ``Quarantined``;
(e) the outcome is written to ``hostchange-<hostver>.json`` for the UI, and
    ``host-state.json`` is updated.

A non-strict mod whose range excludes the new version is **not** yeeted: the
manifest says "load anyway with a warning" (Requirement 1.3) and the warning is
recorded here. Rollback (Requirement 6.3): when the new current version names a
quarantine directory, that set is restored first (files back, seam parts
reinstalled, flags restored). ``floofy yeet`` / ``floofy yeet --restore <ver>``
drive :func:`yeet` and :func:`restore_quarantine` by hand; ``floofy hold``
(Requirement 6.6) is the edition adapter's ``update_hold()`` behind an audit
row — never a default.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .anchors import check_anchors
from .compat import CompatCache
from .datahome import DataHome
from .modstore import InstalledMod, read_enabled, sync_early_list, write_enabled
from .payloads import Payload
from .semver import InvalidRange, Range

__all__ = [
    "HostState",
    "YeetDecision",
    "apply_if_changed",
    "decide_yeets",
    "detect_change",
    "handle_host_change",
    "list_quarantine",
    "record_host_state",
    "restore_quarantine",
    "yeet",
]

FINGERPRINT_SKIPS = frozenset({"FingerprintMiss", "FingerprintAmbiguous"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- host state -----------------------------------------------------------------------------------


@dataclass
class HostState:
    payloads: dict[str, str] = field(default_factory=dict)
    current: str | None = None
    recorded_at: str | None = None

    @classmethod
    def load(cls, home: DataHome) -> "HostState":
        try:
            document = json.loads(home.host_state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(document, dict):
            return cls()
        payloads = document.get("payloads") if isinstance(document.get("payloads"), dict) else {}
        return cls({str(k): str(v) for k, v in payloads.items()}, document.get("current") if isinstance(document.get("current"), str) else None, document.get("recordedAt") if isinstance(document.get("recordedAt"), str) else None)

    @classmethod
    def from_payloads(cls, payloads: list[Payload], current: Payload | None) -> "HostState":
        return cls({p.id: p.host_version.text for p in payloads}, current.host_version.text if current else None, _now())

    def to_dict(self) -> dict[str, Any]:
        return {"payloads": dict(self.payloads), "current": self.current, "recordedAt": self.recorded_at}

    def save(self, home: DataHome) -> None:
        home.ensure()
        tmp = home.host_state.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(home.host_state)


def detect_change(previous: HostState, payloads: list[Payload], current: Payload | None) -> dict[str, Any]:
    """What differs between the recorded state and now: new/vanished payloads, a new current version."""
    now = HostState.from_payloads(payloads, current)
    added = sorted(set(now.payloads) - set(previous.payloads))
    vanished = sorted(set(previous.payloads) - set(now.payloads))
    version_changed = previous.current is not None and now.current is not None and previous.current != now.current
    first_run = previous.recorded_at is None
    changed = bool(added or vanished or version_changed or first_run)
    return {"changed": changed, "firstRun": first_run, "added": added, "vanished": vanished, "previousCurrent": previous.current, "current": now.current, "versionChanged": version_changed}


def record_host_state(home: DataHome, payloads: list[Payload], current: Payload | None) -> HostState:
    state = HostState.from_payloads(payloads, current)
    state.save(home)
    return state


# --- yeet decisions ---------------------------------------------------------------------------------


@dataclass
class YeetDecision:
    mod_id: str
    version: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def yeet(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.mod_id, "version": self.version, "yeet": self.yeet, "reasons": list(self.reasons), "warnings": list(self.warnings)}


def _fingerprint_misses(apply_report: dict[str, Any] | None, host_version: str) -> dict[str, list[str]]:
    """Fingerprint skips on the payloads of the NEW host version (another version's misses are not its verdict)."""
    misses: dict[str, list[str]] = {}
    for payload in (apply_report or {}).get("payloads", []):
        if payload.get("host_version") != host_version:
            continue
        for skip in payload.get("skipped", []):
            if skip.get("code") in FINGERPRINT_SKIPS and skip.get("mod"):
                misses.setdefault(str(skip["mod"]), []).append(f"{skip.get('target')}#ops/{skip.get('op')}: {skip.get('code')}")
    return misses


def decide_yeets(mods: list[InstalledMod], *, host: Payload, compat: CompatCache, apply_report: dict[str, Any] | None, requests: dict[str, dict[str, Any]], edition: str | None = None) -> list[YeetDecision]:
    """Requirement 6.2 (d): which installed mods do not cover the new host version."""
    row = compat.row_for(edition or host.edition, host.channel, host.host_version.text)
    misses = _fingerprint_misses(apply_report, host.host_version.text)
    decisions: list[YeetDecision] = []
    for mod in mods:
        if mod.problem:
            continue
        decision = YeetDecision(mod.id, mod.version)
        kirocrew = mod.manifest.get("kirocrew") if isinstance(mod.manifest.get("kirocrew"), dict) else {}
        text = str(kirocrew.get("version") or "*")
        try:
            in_range = Range.parse(text).contains(host.host_version.base)
        except (InvalidRange, ValueError):
            in_range = False
        if not in_range:
            if kirocrew.get("strict"):
                decision.reasons.append(f"manifest: kirocrew.version {text!r} excludes {host.host_version.base} and strict is true (Unsupported)")
            else:
                decision.warnings.append(f"manifest: kirocrew.version {text!r} excludes {host.host_version.base}; non-strict, so the Loader loads it with a warning (Requirement 1.3)")
        if row is not None and row.verdict(mod.id, mod.version) == "broken":
            decision.reasons.append(f"matrix: {mod.id}@{mod.version} is broken on {host.host_version.text} ({row.edition}/{row.channel})")
        for miss in misses.get(mod.id, []):
            decision.reasons.append(f"fingerprint: {miss}")
        if mod.id in requests:
            decision.reasons.append(f"loader request: {requests[mod.id].get('reason') or 'the Loader asked for quarantine'}")
        decisions.append(decision)
    return decisions


# --- the quarantine -----------------------------------------------------------------------------------


def _read_requests(home: DataHome) -> dict[str, dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    if not home.quarantine_requests.is_dir():
        return requests
    for path in sorted(home.quarantine_requests.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        requests[path.stem] = document if isinstance(document, dict) else {}
    return requests


def yeet(ctx: Any, mod: InstalledMod, host_version: str, *, reasons: list[str], run_handlers: bool = True) -> dict[str, Any]:
    """Park ``mod`` under ``quarantine/<host_version>/<id>/`` with its enabled flag; seam parts are uninstalled first."""
    home: DataHome = ctx.home
    target_dir = home.quarantine_dir(host_version)
    target_dir.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {"id": mod.id, "version": mod.version, "hostVersion": host_version, "reasons": list(reasons), "reason": "Quarantined"}
    if run_handlers and not ctx.in_gateway:
        from .cli.actions import kind_context  # noqa: PLC0415
        from .installer import run_handlers as _run  # noqa: PLC0415

        outcome["parts"] = [o.to_dict() for o in _run(kind_context(ctx), mod.id, mod.dir, mod.manifest, "uninstall") if o.kind not in ("python-hook", "spa", "patch")]
    flags = read_enabled(home.enabled)
    was_enabled = flags.pop(mod.id, not mod.has_code)
    write_enabled(home.enabled, flags)
    sync_early_list(home)
    parked_flags_path = target_dir / "enabled.json"
    parked = read_enabled(parked_flags_path)
    parked[mod.id] = was_enabled
    write_enabled(parked_flags_path, parked)
    destination = target_dir / mod.id
    if destination.exists():
        shutil.rmtree(destination)
    if mod.dir.is_symlink():
        link_target = mod.dir.resolve()
        mod.dir.unlink()
        destination.symlink_to(link_target, target_is_directory=True)
    else:
        shutil.move(str(mod.dir), str(destination))
    for extra in (home.spa_dir(mod.id), home.pending / mod.id):
        if extra.exists():
            shutil.rmtree(extra, ignore_errors=True)
    request = home.quarantine_requests / f"{mod.id}.json"
    if request.exists():
        request.unlink()
    outcome["parkedAt"] = str(destination)
    outcome["wasEnabled"] = was_enabled
    ctx.audit.record("yeet", mod=mod.id, version=mod.version, payload=host_version, result="ok", detail="; ".join(reasons) or "manual", files=[str(destination)])
    return outcome


def list_quarantine(home: DataHome) -> dict[str, list[str]]:
    listing: dict[str, list[str]] = {}
    if not home.quarantine.is_dir():
        return listing
    for version_dir in sorted(p for p in home.quarantine.iterdir() if p.is_dir() and p.name != "requests"):
        listing[version_dir.name] = sorted(p.name for p in version_dir.iterdir() if p.is_dir() and (p / "floofy.json").is_file())
    return listing


def restore_quarantine(ctx: Any, host_version: str, *, only: list[str] | None = None, run_handlers: bool = True) -> dict[str, Any]:
    """Bring the mods parked under ``quarantine/<host_version>/`` back (Requirement 6.3), flags included."""
    home: DataHome = ctx.home
    source_dir = home.quarantine_dir(host_version)
    outcome: dict[str, Any] = {"hostVersion": host_version, "restored": [], "skipped": []}
    if not source_dir.is_dir():
        outcome["detail"] = f"no quarantine for {host_version}"
        return outcome
    parked_flags = read_enabled(source_dir / "enabled.json")
    for parked in sorted(p for p in source_dir.iterdir() if p.is_dir() and (p / "floofy.json").is_file()):
        mod_id = parked.name
        if only and mod_id not in only:
            continue
        destination = home.mod_dir(mod_id)
        if destination.exists() or destination.is_symlink():
            outcome["skipped"].append({"id": mod_id, "detail": f"mods/{mod_id} already exists; leaving the quarantined copy in place"})
            continue
        home.mods.mkdir(parents=True, exist_ok=True)
        if parked.is_symlink():
            destination.symlink_to(parked.resolve(), target_is_directory=True)
            parked.unlink()
        else:
            shutil.move(str(parked), str(destination))
        try:
            manifest = json.loads((destination / "floofy.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        flags = read_enabled(home.enabled)
        flags[mod_id] = parked_flags.get(mod_id, True)
        write_enabled(home.enabled, flags)
        sync_early_list(home)
        parked_flags.pop(mod_id, None)
        entry: dict[str, Any] = {"id": mod_id, "version": str(manifest.get("version") or ""), "enabled": flags[mod_id]}
        if run_handlers and not ctx.in_gateway and isinstance(manifest, dict):
            from .cli.actions import kind_context  # noqa: PLC0415
            from .installer import run_handlers as _run  # noqa: PLC0415

            entry["parts"] = [o.to_dict() for o in _run(kind_context(ctx), mod_id, destination, manifest, "install") if o.kind not in ("python-hook", "spa", "patch")]
        outcome["restored"].append(entry)
        ctx.audit.record("yeet-restore", mod=mod_id, version=entry["version"], payload=host_version, result="ok", files=[str(destination)])
    if parked_flags:
        write_enabled(source_dir / "enabled.json", parked_flags)
    elif (source_dir / "enabled.json").exists():
        (source_dir / "enabled.json").unlink()
    if source_dir.is_dir() and not any(source_dir.iterdir()):
        source_dir.rmdir()
    return outcome


# --- the handler ------------------------------------------------------------------------------------


def handle_host_change(ctx: Any, *, change: dict[str, Any], confirmed_governance_targets: list[str] | None = None, verify: bool = False) -> dict[str, Any]:
    """Requirement 6.2 (a)–(e) for the current payload; returns the outcome also written to ``hostchange-<ver>.json``."""
    from .cli.actions import reapply  # noqa: PLC0415
    from .spa_report import check_bundle_fingerprints  # noqa: PLC0415

    home: DataHome = ctx.home
    current: Payload | None = ctx.current_payload()
    outcome: dict[str, Any] = {"ok": True, "change": change, "ts": _now(), "hostVersion": current.host_version.text if current else None, "previousVersion": change.get("previousCurrent")}
    if current is None:
        outcome["ok"] = False
        outcome["detail"] = "no current payload"
        return outcome
    new_version = current.host_version.text
    previous_version = change.get("previousCurrent")

    # Requirement 6.3: a rollback restores the set parked under this exact version first
    if new_version in list_quarantine(home) and (change.get("versionChanged") or change.get("firstRun")):
        outcome["rollbackRestore"] = restore_quarantine(ctx, new_version)
        ctx.say(f"rollback to {new_version}: restored {len(outcome['rollbackRestore']['restored'])} quarantined mod(s)")

    # (a) reporter + anchors
    try:
        spa = check_bundle_fingerprints(current.dist_dir, host_version=new_version) if current.has_frontend else None
        outcome["reporter"] = spa.to_dict() if spa else {"skipped": "payload has no frontend"}
    except Exception as exc:  # noqa: BLE001 - the reporter is diagnostic; never abort the change handling
        outcome["reporter"] = {"error": f"{type(exc).__name__}: {exc}"}
    anchors = check_anchors(current)
    outcome["anchors"] = anchors.to_dict()
    ctx.say(f"host change to {new_version}: spa fingerprints {outcome['reporter'].get('spaFingerprints', {}).get('matched', '?')}/{outcome['reporter'].get('spaFingerprints', {}).get('total', '?')}, python anchors {len(anchors.matched)}/{anchors.total}")

    # (b) compat
    compat = CompatCache.load(home.compat_cache)
    edition = ctx.edition()
    row = compat.row_for(edition, current.channel, new_version)
    outcome["compat"] = {"row": row.to_dict() if row else None, "notes": list(compat.notes)}
    if row is not None and row.framework.get("loader") == "broken":
        ctx.warn(f"the compat matrix marks the FloofyCrew Loader broken on {new_version}; mods are still handled on your consent (matrix verdicts are information)")

    # (c) re-apply with the enabled set
    report = reapply(ctx, verify=verify, confirmed_governance_targets=confirmed_governance_targets, quiet=True)
    apply_report = report.to_dict() if report is not None else None
    outcome["apply"] = apply_report

    # (d) yeet
    requests = _read_requests(home)
    decisions = decide_yeets(ctx.mods(), host=current, compat=compat, apply_report=apply_report, requests=requests, edition=edition)
    outcome["decisions"] = [d.to_dict() for d in decisions]
    park_under = previous_version if previous_version and previous_version != new_version else new_version
    yeeted: list[dict[str, Any]] = []
    for decision in decisions:
        if not decision.yeet:
            for warning in decision.warnings:
                ctx.warn(f"{decision.mod_id}: {warning}")
            continue
        mod = next(m for m in ctx.mods() if m.id == decision.mod_id)
        yeeted.append(yeet(ctx, mod, park_under, reasons=decision.reasons))
        ctx.say(f"yeeted {decision.mod_id} {decision.version} -> quarantine/{park_under}/ ({'; '.join(decision.reasons)})")
    outcome["yeeted"] = yeeted
    if yeeted and any(k in ("patch", "spa") for y in yeeted for k in _kinds_of(home, y)):
        second = reapply(ctx, verify=False, confirmed_governance_targets=confirmed_governance_targets, quiet=True)
        outcome["applyAfterYeet"] = second.to_dict() if second is not None else None
    outcome["ok"] = bool(apply_report is None or apply_report.get("ok", True))

    # (e) record
    home.hostchange(new_version).write_text(json.dumps(outcome, indent=2, default=str) + "\n", encoding="utf-8")
    ctx.audit.record("host-change", payload=current.id, version=new_version, result="ok" if outcome["ok"] else "errors", detail=f"previous {previous_version}; yeeted {[y['id'] for y in yeeted]}", files=[str(home.hostchange(new_version))])
    return outcome


def _kinds_of(home: DataHome, yeeted: dict[str, Any]) -> list[str]:
    try:
        manifest = json.loads((Path(yeeted["parkedAt"]) / "floofy.json").read_text(encoding="utf-8"))
        return [str(p.get("kind")) for p in manifest.get("parts") or [] if isinstance(p, dict)]
    except (OSError, ValueError, KeyError):
        return []


def apply_if_changed(ctx: Any, *, confirmed_governance_targets: list[str] | None = None, verify: bool = False) -> dict[str, Any]:
    """The trigger's entry point: nothing changed → nothing to do; otherwise the full host-change handling."""
    payloads = ctx.payloads()
    current = ctx.current_payload()
    previous = HostState.load(ctx.home)
    change = detect_change(previous, payloads, current)
    if not change["changed"]:
        ctx.say("apply --if-changed: nothing changed since the last run; nothing to do")
        return {"ok": True, "change": change, "reapplied": False}
    ctx.say(f"apply --if-changed: change detected ({'first run' if change['firstRun'] else ''}{' added ' + ', '.join(change['added']) if change['added'] else ''}{' vanished ' + ', '.join(change['vanished']) if change['vanished'] else ''}{' version ' + str(change['previousCurrent']) + ' -> ' + str(change['current']) if change['versionChanged'] else ''})")
    outcome = handle_host_change(ctx, change=change, confirmed_governance_targets=confirmed_governance_targets, verify=verify)
    outcome["reapplied"] = outcome.get("apply") is not None
    for item in (outcome.get("apply") or {}).get("payloads", []):
        ctx.say(f"  {item['payload']}: {'ok' if item['ok'] else 'errors'}; written {len(item['written'])}, restored {len(item['restored'])}, applied {len(item['applied'])}, skipped {len(item['skipped'])}")
    record_host_state(ctx.home, payloads, current)
    ctx.audit.record("apply-if-changed", payload=current.id if current else None, result="ok" if outcome.get("ok", True) else "errors", detail=json.dumps(change, sort_keys=True))
    return outcome
