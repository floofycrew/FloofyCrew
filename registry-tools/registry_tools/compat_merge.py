"""``compat-merge`` — add or replace one matrix row from a Forge run file (Requirement 9.1, 9.3).

The Forge's check stage (task 8.4) writes one JSON file per
``(edition, channel, hostVersion)`` run::

    {"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.6",
     "framework": {"loader": "ok", "spaFingerprints": {...}, "pythonAnchors": {...}, "shimVersion": "1.0.3"},
     "mods": {"rimuru-branding@1.2.0": {"verdict": "tested", "run": "https://…"}},
     "run": "https://…", "checkedAt": "…Z"}

``merge_row`` replaces the row with the same key (keeping its human
``overrides[]`` unless the run file carries its own) or appends a new one,
sorts rows by edition, channel and host version, validates the document against
``compat.schema.json`` and returns it; the CLI writes it and optionally signs.
A human override is added with ``--override cell=verdict --reason … --by …``
(``override_cell``), which is how "a human MAY override any cell with a reason"
(Requirement 9.3) lands in the file.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance
from floofy_core.semver import HostVersion

__all__ = ["CompatMergeError", "load_compat", "merge_row", "override_cell", "row_key", "validate_compat"]


class CompatMergeError(ValueError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_compat(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {"schema": 1, "rows": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompatMergeError(f"{path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("rows"), list):
        raise CompatMergeError(f"{path}: not a compat document ({{\"schema\": 1, \"rows\": [...]}})")
    return document


def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    try:
        return str(row["edition"]), str(row["channel"]), str(row["hostVersion"])
    except KeyError as exc:
        raise CompatMergeError(f"row lacks {exc}") from exc


def validate_compat(document: dict[str, Any]) -> list[str]:
    return [f"compat.json{e.path or '/'}: {e.message}" for e in validate_instance(document, load_schema("compat"))]


def _sort_key(row: dict[str, Any]) -> tuple[str, str, tuple]:
    try:
        version = (0, HostVersion.parse(str(row["hostVersion"])).sort_key())
    except Exception:  # noqa: BLE001 - unparsable versions sort last, textually
        version = (1, str(row["hostVersion"]))
    return str(row["edition"]), str(row["channel"]), version


def merge_row(document: dict[str, Any], row: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    """Replace the row with ``row``'s key (preserving existing ``overrides[]`` unless ``row`` has some) or append it."""
    key = row_key(row)
    incoming = {k: v for k, v in row.items() if not k.startswith("_")}
    rows = list(document.get("rows") or [])
    for index, existing in enumerate(rows):
        if isinstance(existing, dict) and row_key(existing) == key:
            if "overrides" not in incoming and existing.get("overrides"):
                incoming["overrides"] = existing["overrides"]
            rows[index] = incoming
            break
    else:
        rows.append(incoming)
    merged = {**document, "schema": 1, "rows": sorted(rows, key=_sort_key), "generatedAt": _now()}
    if source:
        merged["source"] = source
    problems = validate_compat(merged)
    if problems:
        raise CompatMergeError("; ".join(problems))
    return merged


def override_cell(document: dict[str, Any], *, edition: str, channel: str, host_version: str, cell: str, verdict: str, reason: str, by: str) -> dict[str, Any]:
    """Record a human override on one row (the row must exist); replaces an earlier override of the same cell."""
    rows = list(document.get("rows") or [])
    for index, existing in enumerate(rows):
        if isinstance(existing, dict) and row_key(existing) == (edition, channel, host_version):
            overrides = [o for o in (existing.get("overrides") or []) if not (isinstance(o, dict) and o.get("cell") == cell)]
            overrides.append({"cell": cell, "verdict": verdict, "reason": reason, "by": by, "at": _now()})
            rows[index] = {**existing, "overrides": overrides}
            merged = {**document, "schema": 1, "rows": rows, "generatedAt": _now()}
            problems = validate_compat(merged)
            if problems:
                raise CompatMergeError("; ".join(problems))
            return merged
    raise CompatMergeError(f"no row for {edition}/{channel}/{host_version} to override")
