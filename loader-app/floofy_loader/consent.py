"""The one-time consent record (Requirement 11.1, design "Consent, trust and audit").

``floofy init`` (or the manager UI's first enable) shows :data:`WARNING_TEXT`
once and writes ``consent.json`` — ``{"warningVersion": 1, "acknowledgedAt":
"...", "by": "<os user>"}``. Until that record exists for the *current*
warning text, the Loader is inert: nothing activates and every mod that would
otherwise load reports the Loader-level reason ``ConsentRequired``
(Requirement 3.5). This is the **user's** consent to run unofficial code with
gateway privileges; it has nothing to do with the host's governance, which is
only ever a warning (DR-5).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ConsentStatus", "REASON_CONSENT_REQUIRED", "WARNING_TEXT", "WARNING_VERSION", "read_consent", "write_consent"]

#: Bumped whenever :data:`WARNING_TEXT` changes materially; an older acknowledgement no longer counts.
WARNING_VERSION = 1

#: The Loader-level non-load reason (not a resolver :class:`floofy_core.resolver.Reason`).
REASON_CONSENT_REQUIRED = "ConsentRequired"

WARNING_TEXT = """\
FloofyCrew is UNOFFICIAL and not affiliated with Kiro or KiroCrew.

Mods run inside the KiroCrew gateway with the gateway's full privileges
(your files, your network, your credentials in memory). Mods may bypass the
host's governance ceiling. FloofyCrew shows you what each mod does before
install, logs every change it makes and can restore the host to vanilla
(floofy restore --all) — but the risk of every mod you install is yours.
"""


@dataclass(frozen=True)
class ConsentStatus:
    """What ``consent.json`` says right now."""

    ok: bool
    status: str  # "ok" | "missing" | "outdated" | "invalid"
    warning_version: int | None = None
    acknowledged_at: str | None = None
    by: str | None = None
    detail: str = ""

    @property
    def required(self) -> bool:
        return not self.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "required",
            "detail": self.status,
            "required": self.required,
            "warningVersion": self.warning_version,
            "currentWarningVersion": WARNING_VERSION,
            "acknowledgedAt": self.acknowledged_at,
            "by": self.by,
            "message": self.detail,
        }


def read_consent(path: Path) -> ConsentStatus:
    """Read ``consent.json``; any shape problem is reported, never raised."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ConsentStatus(False, "missing", detail="no consent record: run `floofy init` and acknowledge the warning")
    except (OSError, ValueError) as exc:
        return ConsentStatus(False, "invalid", detail=f"consent.json unreadable: {exc}")
    if not isinstance(document, dict) or not isinstance(document.get("warningVersion"), int):
        return ConsentStatus(False, "invalid", detail="consent.json has no integer warningVersion")
    version = int(document["warningVersion"])
    acknowledged = document.get("acknowledgedAt") if isinstance(document.get("acknowledgedAt"), str) else None
    by = document.get("by") if isinstance(document.get("by"), str) else None
    if version < WARNING_VERSION:
        return ConsentStatus(False, "outdated", version, acknowledged, by, detail=f"the warning text changed (v{version} acknowledged, v{WARNING_VERSION} current): run `floofy init` again")
    return ConsentStatus(True, "ok", version, acknowledged, by)


def write_consent(path: Path, *, by: str | None = None, when: str | None = None) -> dict[str, Any]:
    """Record the acknowledgement of the current warning (the manager calls this after the user agreed)."""
    record = {
        "warningVersion": WARNING_VERSION,
        "acknowledgedAt": when or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by": by or os.environ.get("USER") or os.environ.get("USERNAME") or "",
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
