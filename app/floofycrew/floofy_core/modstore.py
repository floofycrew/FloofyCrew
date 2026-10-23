"""Reading and flagging the installed mod set under ``<data home>/mods/`` (shared by CLI and Loader).

* :func:`installed_mods` — ``mods/*/floofy.json`` → :class:`InstalledMod` records
  (a directory whose name differs from its manifest ``id`` is reported, not
  loaded — the same rule the Loader applies at boot);
* :func:`read_enabled` / :func:`write_enabled` — ``enabled.json`` (``{"<id>": bool}``;
  Requirement 11.7: a confirmed install lands enabled, ``--disabled`` lands it off;
  a code mod with **no** entry — predating the flag — stays off until enabled);
* :func:`code_kinds_of` — whether a manifest has ``python-hook``/``spa`` parts
  (the kinds the install confirmation covers);
* the install source record ``mods/<id>/.floofy/source.json`` the manager writes
  (``{"source": "path|archive|url|registry|git", "ref": ..., "sha256": ..., "installedAt": ...,
  "version": ..., "registryKey": ..., "tier": "unlisted|listed",
  "commit": ..., "git": {"url", "tag", "ref", "subdirectory", "commit"}, "link": {...}}`` — the
  last three only when a clone was involved, Requirement 8.8, 8.9, 8.11) so
  profiles, ``update`` and ``floofy info`` know where a mod came from.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .datahome import DataHome

__all__ = [
    "CODE_KINDS",
    "InstalledMod",
    "code_kinds_of",
    "installed_mods",
    "parts_of",
    "read_enabled",
    "early_entries",
    "sync_early_list",
    "read_source",
    "write_enabled",
    "write_source",
]

#: Kinds whose code runs — the install confirmation covers them, and an absent enabled.json entry means off (Requirement 11.7).
CODE_KINDS = frozenset({"python-hook", "spa"})

SOURCE_FILE = "source.json"


@dataclass
class InstalledMod:
    id: str
    dir: Path
    manifest: dict[str, Any]
    problem: str | None = None  # a directory that is not a loadable mod (bad JSON, id mismatch)

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "0.0.0")

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.id)

    @property
    def parts(self) -> list[dict[str, Any]]:
        return parts_of(self.manifest)

    @property
    def kinds(self) -> list[str]:
        return [str(p.get("kind", "")) for p in self.parts]

    @property
    def has_code(self) -> bool:
        return bool(code_kinds_of(self.manifest))

    @property
    def is_link(self) -> bool:
        return self.dir.is_symlink()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "name": self.name, "dir": str(self.dir), "kinds": self.kinds, "problem": self.problem, "link": self.is_link}


def parts_of(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (manifest.get("parts") or []) if isinstance(p, dict)]


def code_kinds_of(manifest: dict[str, Any]) -> set[str]:
    return {str(p.get("kind")) for p in parts_of(manifest)} & CODE_KINDS


def installed_mods(home: DataHome, *, include_broken: bool = True) -> list[InstalledMod]:
    """Every ``mods/<id>/`` with a ``floofy.json`` (symlinked dirs from ``floofy dev`` included)."""
    found: list[InstalledMod] = []
    if not home.mods.is_dir():
        return found
    for mod_dir in sorted(p for p in home.mods.iterdir() if p.is_dir() and not p.name.endswith(".floofy-old")):
        manifest_path = mod_dir / "floofy.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("floofy.json is not a JSON object")
        except (OSError, ValueError) as exc:
            if include_broken:
                found.append(InstalledMod(mod_dir.name, mod_dir, {}, problem=f"floofy.json unreadable: {exc}"))
            continue
        if manifest.get("id") != mod_dir.name:
            if include_broken:
                found.append(InstalledMod(mod_dir.name, mod_dir, manifest, problem=f"directory {mod_dir.name!r} holds a manifest with id {manifest.get('id')!r}"))
            continue
        found.append(InstalledMod(mod_dir.name, mod_dir, manifest))
    return found


def read_enabled(path: Path) -> dict[str, bool]:
    """``enabled.json`` → ``{id: bool}``; anything unreadable is an empty map."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    return {str(k): bool(v) for k, v in document.items()}


def write_enabled(path: Path, enabled: dict[str, bool]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(enabled.items())), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def early_entries(home: DataHome) -> list[dict[str, Any]]:
    """The early-activation list (Requirement 3.2): every ENABLED mod's ``python-hook`` parts with ``early: true``.

    One entry per part — ``{"id", "path", "module", "part"}`` — in mod-id order,
    the shape ``floofy_early`` reads. Broken mods and disabled mods contribute
    nothing; a mod with no early part contributes nothing.
    """
    flags = read_enabled(home.enabled)
    entries: list[dict[str, Any]] = []
    for mod in installed_mods(home, include_broken=False):
        if not flags.get(mod.id):
            continue
        for index, part in enumerate(mod.parts):
            if part.get("kind") == "python-hook" and part.get("early") is True:
                entries.append({"id": mod.id, "path": str(mod.dir), "module": str(part.get("module") or "hook"), "part": index})
    return entries


def sync_early_list(home: DataHome) -> list[dict[str, Any]]:
    """Rewrite ``<data home>/early.json`` from the enabled set; remove it when no early part is enabled.

    The early shim is a no-op without the file, so an empty list is expressed by
    its absence rather than by ``{"mods": []}``. Called after every change to the
    installed or enabled set (install, enable, disable, uninstall, dev link,
    host-change parking, the Loader's pending apply and boot) so the list never
    lags the state it describes; the shim reads it at the next host start.
    """
    entries = early_entries(home)
    path = home.early
    if not entries:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return entries
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"mods": entries}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return entries


def read_source(mod_dir: Path) -> dict[str, Any]:
    try:
        document = json.loads((Path(mod_dir) / ".floofy" / SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def write_source(mod_dir: Path, *, source: str, ref: str, sha256: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    record = {"source": source, "ref": ref, "sha256": sha256, "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(extra or {})}
    runtime = Path(mod_dir) / ".floofy"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / SOURCE_FILE).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
