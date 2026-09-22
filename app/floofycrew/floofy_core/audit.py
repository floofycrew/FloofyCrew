"""The audit log: every mutating operation, one JSON object per line (Requirement 11.5).

``<data home>/audit.jsonl`` is FloofyCrew's own trail, kept in addition to
whatever the host's SEL records for app and theme installs. **One writer** for
every surface — the CLI commands (``install``/``uninstall``/``enable``/``disable``/
``update``/``apply``/``restore``/``yeet``/``hold``/``profile``/``init``/``deinit``/
``registry``/``vanilla``), the seam kind handlers, the Patcher (``apply``,
``restore``, ``apply-confirm``), the Loader (``net-denied`` rows), the interactive
``floofy`` (``actor: tui``) and the manager App's typed routes (``actor: app``) —
so every row has the same shape
(design "Consent, trust and audit")::

    {"ts": "...", "op": "install", "mod": "rimuru-branding", "version": "1.0.0",
     "payload": "venv:crew-venv:0.7.0", "files": [...], "governanceFlags": [...],
     "consentRef": {"warningVersion": 1, "sha256": "<sha256 of consent.json>"} | null,
     "actor": "cli" | "tui" | "app" | "loader" | "trigger" | "patcher", "by": "<os user>",
     "result": "ok" | "declined" | "error", "detail": "...", ...op-specific fields}

``consentRef`` ties the row to the exact acknowledgement it ran under
(:func:`floofy_core.consent.consent_reference`; ``null`` when no consent record
existed, e.g. the ``consent`` row itself is written right after the record and
so already carries it); ``actor`` says which surface performed the operation,
``by`` who owns the process. Extra keyword fields are recorded verbatim so a
caller can add what its operation needs (``code``/``url`` for network denials,
``warnings`` for the survival question, ``sources`` for a registry refresh).
Reading is :func:`read_audit` (``floofy audit [--tail N] [--op OP] [--json]``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .consent import consent_reference
from .deploy import utc_now

__all__ = ["ACTORS", "AuditLog", "read_audit"]

#: The surfaces that perform mutations; anything else is recorded as given. ``tui`` is the interactive ``floofy``
#: (Requirement 15.4), ``app`` the manager App's typed routes (Requirement 16.2); ``ui`` the manager page's read-only ``/cli``.
ACTORS = ("cli", "tui", "app", "ui", "loader", "trigger", "patcher", "forge")


def _os_user() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or ""


class AuditLog:
    """Append-only ``audit.jsonl``; ``record(op, **fields)`` never raises into the caller's operation."""

    def __init__(self, data_home: Path, *, actor: str = "cli"):
        self.data_home = Path(data_home)
        self.path = self.data_home / "audit.jsonl"
        self.actor = actor
        self._consent_path = self.data_home / "consent.json"

    def consent_ref(self) -> dict[str, Any] | None:
        return consent_reference(self._consent_path)

    def entry(self, op: str, **fields: Any) -> dict[str, Any]:
        """The row :meth:`record` writes (also the shape tests assert on)."""
        row: dict[str, Any] = {
            "ts": utc_now(),
            "op": op,
            "actor": fields.pop("actor", self.actor),
            "by": _os_user(),
            "consentRef": fields.pop("consentRef", self.consent_ref()),
        }
        for key in ("mod", "version", "payload", "result", "detail"):
            row[key] = fields.pop(key, None)
        row["files"] = list(fields.pop("files", []) or [])
        row["governanceFlags"] = list(fields.pop("governanceFlags", []) or [])
        row.update(fields)
        return row

    def record(self, op: str, **fields: Any) -> dict[str, Any]:
        row = self.entry(op, **fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        except OSError:
            pass  # the audit log must never turn a successful operation into a failure
        return row


def read_audit(path: Path, *, tail: int | None = None, ops: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The rows of an ``audit.jsonl`` (oldest first); malformed lines are skipped."""
    wanted = set(ops) if ops is not None else None
    rows: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (wanted is None or row.get("op") in wanted):
            rows.append(row)
    if tail is not None:
        rows = rows[-tail:] if tail > 0 else []
    return rows
