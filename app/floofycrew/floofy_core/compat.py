"""The local compatibility-matrix cache (Requirement 9.2) — shared by the Loader, the CLI and the Forge.

``cache/compat.json`` is the registry's ``compat.json`` (design "Registry index
and compat.json"): ``rows[]`` keyed by ``edition × channel × hostVersion`` with a
``framework`` verdict and per ``mod@version`` verdicts ``tested | expected |
broken`` (human ``overrides[]`` carry a reason). The Loader consults it at boot
(only ``broken`` changes its behaviour: the mod is reported ``Quarantined`` and
a quarantine request is written for the manager, which moves the files — the
Loader never moves mod directories itself); ``floofy doctor`` shows the row for
the running host; ``floofy update`` prefers the newest version marked ``tested``
or ``expected`` here; the host-version-change handler yeets ``broken`` mods.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CompatCache", "CompatRow"]

VERDICTS = ("tested", "expected", "broken")


@dataclass(frozen=True)
class CompatRow:
    edition: str
    channel: str
    host_version: str
    framework: dict[str, Any] = field(default_factory=dict)
    mods: dict[str, str] = field(default_factory=dict)  # "id@version" -> verdict
    runs: dict[str, str] = field(default_factory=dict)  # "id@version" -> run link

    def verdict(self, mod_id: str, version: str) -> str | None:
        return self.mods.get(f"{mod_id}@{version}")

    def to_dict(self) -> dict[str, Any]:
        return {"edition": self.edition, "channel": self.channel, "hostVersion": self.host_version, "framework": dict(self.framework), "mods": dict(self.mods)}


@dataclass
class CompatCache:
    """Rows parsed from ``compat.json``; a missing or malformed cache is an empty one with a note."""

    rows: list[CompatRow] = field(default_factory=list)
    source: str = ""
    generated_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "CompatCache":
        cache = cls(source=str(path))
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            cache.notes.append("no compat cache yet (floofy registry refresh writes it)")
            return cache
        except (OSError, ValueError) as exc:
            cache.notes.append(f"compat cache unreadable: {exc}")
            return cache
        return cls.from_dict(document, source=str(path))

    @classmethod
    def from_dict(cls, document: Any, *, source: str = "<inline>") -> "CompatCache":
        cache = cls(source=source)
        if not isinstance(document, dict) or not isinstance(document.get("rows"), list):
            cache.notes.append("compat cache has no rows[]")
            return cache
        cache.generated_at = document.get("generatedAt") if isinstance(document.get("generatedAt"), str) else None
        for index, raw in enumerate(document["rows"]):
            if not isinstance(raw, dict):
                continue
            edition, channel, host_version = raw.get("edition"), raw.get("channel"), raw.get("hostVersion")
            if not all(isinstance(v, str) for v in (edition, channel, host_version)):
                cache.notes.append(f"rows[{index}] lacks edition/channel/hostVersion")
                continue
            mods: dict[str, str] = {}
            runs: dict[str, str] = {}
            raw_mods = raw.get("mods") if isinstance(raw.get("mods"), dict) else {}
            for key, cell in raw_mods.items():
                verdict = cell.get("verdict") if isinstance(cell, dict) else cell
                if isinstance(verdict, str) and verdict in VERDICTS:
                    mods[str(key)] = verdict
                    if isinstance(cell, dict) and isinstance(cell.get("run"), str):
                        runs[str(key)] = cell["run"]
            for override in raw.get("overrides") or []:
                if isinstance(override, dict) and isinstance(override.get("cell"), str) and override.get("verdict") in VERDICTS:
                    mods[override["cell"]] = str(override["verdict"])
            framework = raw.get("framework") if isinstance(raw.get("framework"), dict) else {}
            cache.rows.append(CompatRow(edition, channel, host_version, dict(framework), mods, runs))
        return cache

    def row_for(self, edition: str, channel: str | None, host_version: str) -> CompatRow | None:
        """The row for this host.

        The exact ``edition × channel × hostVersion`` row wins. A build whose
        channel is unknown, or whose channel has no row yet, takes the same
        edition's row for that version on another channel: the channels of a
        stamped build are the same bytes (design DR-4), so its verdict carries.
        """
        same_version = [row for row in self.rows if row.edition == edition and row.host_version == host_version]
        for row in same_version:
            if channel is None or row.channel == channel:
                return row
        return same_version[0] if same_version else None

    def to_dict(self, row: CompatRow | None = None) -> dict[str, Any]:
        return {
            "source": self.source,
            "generatedAt": self.generated_at,
            "rows": len(self.rows),
            "row": row.to_dict() if row else None,
            "notes": list(self.notes),
        }
