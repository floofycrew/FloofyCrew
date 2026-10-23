"""What the Loader publishes: :class:`LoaderState` (design "Error Handling", Requirement 3.5, 2.7).

The same object backs ``GET /api/apps/floofycrew/state``, ``loader-state.json``
(read by ``floofy doctor``) and the manager UI. Vocabulary:

* ``loader``: ``ok`` (booted, mods considered), ``inert`` (no consent, nothing
  activated, everything reported read-only), ``failed`` (the Loader itself
  failed; the gateway runs vanilla — Requirement 3.6);
* per-mod ``reason``: exactly the resolver's :class:`floofy_core.resolver.Reason`
  values plus the Loader-level ``ConsentRequired``. **Governance is never a
  reason**: it is reported under ``governance[]`` / ``warnings[]`` (DR-5).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from floofy_core.resolver import Reason

from .consent import REASON_CONSENT_REQUIRED

__all__ = ["LoaderState", "ModState", "PartState", "REASONS", "SEAM_BY_KIND", "utc_now"]

#: Every value ``ModState.reason`` may take (Requirement 3.5).
REASONS: tuple[str, ...] = (*(r.value for r in Reason), REASON_CONSENT_REQUIRED)

#: Which host seam each part kind lands through (Requirement 2.7); ``patch`` is the only kind touching payload files.
SEAM_BY_KIND: dict[str, str] = {
    "theme": "themes directory (host validator or byte-equivalent direct write)",
    "agent": "agents directory",
    "skill": "skills directory",
    "appearance": "appearance library",
    "config": "config.json (kirocrew config set semantics)",
    "app": "App Kit (kirocrew app install)",
    "python-hook": "Loader (in-process python-hook activation)",
    "spa": "SPA host (Loader app ui route)",
    "patch": "Patcher overlay (modifies payload files)",
    "ui": "FloofyCrew App (the mod's own page, mounted on request)",
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class PartState:
    """One ``parts[]`` entry and what happened to it."""

    index: int
    kind: str
    side: str
    path: str
    status: str = "inactive"  # inactive | active | error | skipped
    detail: str = ""
    seam: str = ""
    modifies_payload: bool = False
    #: ``spa`` parts only: ``runtime`` (loaded by the SPA host) or ``boot`` (inlined into index.html by the Patcher).
    activation: str | None = None
    #: ``ui`` parts only (Requirement 16.4): the module the App imports, the page title and the optional icon.
    entry: str | None = None
    title: str | None = None
    icon: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "index": self.index,
            "kind": self.kind,
            "side": self.side,
            "path": self.path,
            "status": self.status,
            "detail": self.detail,
            "seam": self.seam or SEAM_BY_KIND.get(self.kind, ""),
            "modifiesPayload": self.modifies_payload or self.kind == "patch",
        }
        if self.kind == "spa":
            data["activation"] = self.activation or "runtime"
        if self.kind == "ui":
            data["entry"] = self.entry
            data["title"] = self.title
            data["icon"] = self.icon
        return data


@dataclass
class ModState:
    """The published verdict for one installed mod."""

    id: str
    version: str
    name: str = ""
    active: bool = False
    reason: str | None = None
    detail: str = ""
    enabled: bool = True
    quarantined: bool = False
    files_ok: bool = True
    warnings: list[dict[str, str]] = field(default_factory=list)
    governance: list[dict[str, Any]] = field(default_factory=list)
    parts: list[PartState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mod_dir: str = ""
    #: Mod-published facts (``ctx.state[...]``) for tests and the UI.
    exports: dict[str, Any] = field(default_factory=dict)
    #: The source tier (Requirement 8.11): ``unlisted`` / ``listed`` / ``tested``, from the install record and this host's matrix row.
    tier: str = "unlisted"

    def set_reason(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"{reason!r} is not a Loader reason ({', '.join(REASONS)})")
        self.active = False
        self.reason = reason
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "active": self.active,
            "reason": self.reason,
            "detail": self.detail,
            "enabled": self.enabled,
            "quarantined": self.quarantined,
            "filesOk": self.files_ok,
            "warnings": list(self.warnings),
            "governance": list(self.governance),
            "parts": [p.to_dict() for p in self.parts],
            "errors": list(self.errors),
            "modDir": self.mod_dir,
            "exports": dict(self.exports),
            "tier": self.tier,
        }


@dataclass
class LoaderState:
    """Everything the Loader knows after a boot."""

    loader: str = "inert"  # ok | inert | failed
    consent: dict[str, Any] = field(default_factory=dict)
    host: dict[str, Any] = field(default_factory=dict)
    mods: dict[str, ModState] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    governance: list[dict[str, Any]] = field(default_factory=list)
    governance_snapshot: dict[str, Any] = field(default_factory=dict)
    compat: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)
    patches: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    booted_at: str = field(default_factory=utc_now)
    duration_ms: int = 0
    data_home: str = ""
    #: ``floofy --vanilla``: this boot ran with every mod disabled (the marker was consumed).
    vanilla: bool = False

    @property
    def active(self) -> list[str]:
        return [mod_id for mod_id in self.order if self.mods[mod_id].active]

    def to_dict(self) -> dict[str, Any]:
        return {
            "loader": self.loader,
            "consent": dict(self.consent),
            "host": dict(self.host),
            "mods": {mod_id: state.to_dict() for mod_id, state in sorted(self.mods.items())},
            "order": list(self.order),
            "active": self.active,
            "governance": list(self.governance),
            "governanceSnapshot": dict(self.governance_snapshot),
            "compat": dict(self.compat),
            "pending": dict(self.pending),
            "patches": dict(self.patches),
            "errors": list(self.errors),
            "bootedAt": self.booted_at,
            "durationMs": self.duration_ms,
            "dataHome": self.data_home,
            "vanilla": self.vanilla,
            "reasons": list(REASONS),
        }
