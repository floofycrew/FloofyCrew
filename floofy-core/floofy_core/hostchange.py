"""Host-version-change handling behind ``floofy apply --if-changed`` (Requirement 6.1, 6.2).

The re-apply triggers (hourly timer, PATH wrapper) and the Loader's
``host.version_changed`` event all funnel here. ``host-state.json`` remembers the
payload set the last run saw (``{payloads: {id: version}, current: version,
recordedAt}``); :func:`detect_change` compares it with what discovery finds now.
Nothing changed → nothing to do (a quiet hour costs one discovery). Something
changed → :func:`apply_if_changed` re-applies the enabled patch set to every
payload (Requirement 5.4, 6.4) and records ``host-state.json``; task 6.5 adds
the reporter, the Python anchors, the compat consult and the yeet.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .datahome import DataHome
from .payloads import Payload

__all__ = ["HostState", "apply_if_changed", "detect_change", "record_host_state"]


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
        return cls({p.id: p.host_version.text for p in payloads}, current.host_version.text if current else None, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

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


def apply_if_changed(ctx: Any, *, confirmed_governance_targets: list[str] | None = None, verify: bool = False) -> dict[str, Any]:
    """The trigger's entry point: re-apply only when the payload set or host version changed."""
    from .cli.actions import reapply  # noqa: PLC0415 - the CLI context drives the Patcher

    payloads = ctx.payloads()
    current = ctx.current_payload()
    previous = HostState.load(ctx.home)
    change = detect_change(previous, payloads, current)
    outcome: dict[str, Any] = {"ok": True, "change": change, "reapplied": False}
    if not change["changed"]:
        ctx.say("apply --if-changed: nothing changed since the last run; nothing to do")
        return outcome
    ctx.say(f"apply --if-changed: change detected ({'first run' if change['firstRun'] else ''}{' added ' + ', '.join(change['added']) if change['added'] else ''}{' vanished ' + ', '.join(change['vanished']) if change['vanished'] else ''}{' version ' + str(change['previousCurrent']) + ' -> ' + str(change['current']) if change['versionChanged'] else ''})")
    report = reapply(ctx, verify=verify, confirmed_governance_targets=confirmed_governance_targets)
    if report is not None:
        outcome["reapplied"] = True
        outcome["apply"] = report.to_dict()
        outcome["ok"] = report.ok
        for item in report.payloads:
            ctx.say(f"  {item.payload}: {'ok' if item.ok else 'errors'}; written {len(item.written)}, restored {len(item.restored)}, applied {len(item.applied)}, skipped {len(item.skipped)}")
    record_host_state(ctx.home, payloads, current)
    ctx.audit.record("apply-if-changed", payload=current.id if current else None, result="ok" if outcome["ok"] else "errors", detail=json.dumps(change, sort_keys=True))
    return outcome
