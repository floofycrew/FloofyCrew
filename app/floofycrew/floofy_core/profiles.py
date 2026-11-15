"""Profiles: named mod sets with pinned versions, exportable as one file (Requirement 7.5).

A profile is a lockfile ``profiles/<name>.lock.json``::

    {"schema": 1, "name": "work", "savedAt": "…", "hostVersion": "0.7.0.5", "edition": "internal",
     "floofycrew": "1.2.0",
     "mods": {"rimuru-branding": {"version": "1.0.0", "enabled": true,
                                  "source": {"source": "registry", "ref": "reg/rimuru-branding@1.0.0", "registryKey": "reg/rimuru-branding"},
                                  "sha256": "<sha256 of the installed floofy.json>"}}}

``save`` snapshots the installed set (version, flag, install source, manifest
hash); ``use`` makes the machine match the profile: mods in the profile at the
pinned version are enabled/disabled as recorded, missing ones are installed from
the registry cache (``id@version``) through the normal install flow, and
installed mods the profile does not name are **disabled**, never uninstalled.
``export``/``import`` move the same document as a single file.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .datahome import DataHome
from .modstore import InstalledMod, read_enabled, read_source

__all__ = ["PROFILE_SCHEMA", "Profile", "ProfileError", "list_profiles", "plan_use", "snapshot"]

PROFILE_SCHEMA = 1
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProfileError(ValueError):
    pass


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ProfileError(f"{name!r} is not a valid profile name (letters, digits, . _ -; up to 64)")
    return name


@dataclass
class Profile:
    name: str
    mods: dict[str, dict[str, Any]] = field(default_factory=dict)
    saved_at: str | None = None
    host_version: str | None = None
    edition: str | None = None
    floofycrew: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PROFILE_SCHEMA, "name": self.name, "savedAt": self.saved_at, "hostVersion": self.host_version, "edition": self.edition, "floofycrew": self.floofycrew, "mods": {k: dict(v) for k, v in sorted(self.mods.items())}}

    @classmethod
    def from_dict(cls, document: Any, *, name: str | None = None) -> "Profile":
        if not isinstance(document, dict) or document.get("schema") != PROFILE_SCHEMA or not isinstance(document.get("mods"), dict):
            raise ProfileError("not a schema-1 FloofyCrew profile (needs schema: 1 and mods{})")
        mods: dict[str, dict[str, Any]] = {}
        for mod_id, entry in document["mods"].items():
            if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
                raise ProfileError(f"profile entry {mod_id!r} needs a version string")
            mods[str(mod_id)] = {"version": entry["version"], "enabled": bool(entry.get("enabled", True)), "source": dict(entry.get("source") or {}), "sha256": entry.get("sha256")}
        return cls(_check_name(str(name or document.get("name") or "imported")), mods, document.get("savedAt"), document.get("hostVersion"), document.get("edition"), document.get("floofycrew"))

    @classmethod
    def load(cls, path: Path, *, name: str | None = None) -> "Profile":
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProfileError(f"no profile at {path}") from exc
        except (OSError, ValueError) as exc:
            raise ProfileError(f"{path}: {exc}") from exc
        return cls.from_dict(document, name=name)

    def save(self, path: Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return Path(path)


def snapshot(name: str, mods: list[InstalledMod], enabled: dict[str, bool], *, host_version: str | None, edition: str | None, floofycrew: str) -> Profile:
    """The installed set as a profile."""
    entries: dict[str, dict[str, Any]] = {}
    for mod in mods:
        if mod.problem:
            continue
        entries[mod.id] = {
            "version": mod.version,
            "enabled": enabled.get(mod.id, not mod.has_code),
            "source": {k: v for k, v in read_source(mod.dir).items() if k in ("source", "ref", "registryKey", "sha256") and v is not None},
            "sha256": hashlib.sha256((mod.dir / "floofy.json").read_bytes()).hexdigest(),
        }
    return Profile(_check_name(name), entries, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), host_version, edition, floofycrew)


def list_profiles(home: DataHome) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not home.profiles.is_dir():
        return found
    for path in sorted(home.profiles.glob("*.lock.json")):
        name = path.name[: -len(".lock.json")]
        try:
            profile = Profile.load(path, name=name)
            found.append({"name": name, "path": str(path), "mods": len(profile.mods), "savedAt": profile.saved_at, "hostVersion": profile.host_version})
        except ProfileError as exc:
            found.append({"name": name, "path": str(path), "error": str(exc)})
    return found


def plan_use(profile: Profile, mods: list[InstalledMod], enabled: dict[str, bool]) -> list[dict[str, Any]]:
    """What ``use`` must do, per mod: ``set-flag`` / ``install`` / ``reinstall`` (version differs) / ``disable-extra``."""
    installed = {m.id: m for m in mods if not m.problem}
    steps: list[dict[str, Any]] = []
    for mod_id, entry in sorted(profile.mods.items()):
        current = installed.get(mod_id)
        if current is None:
            steps.append({"id": mod_id, "action": "install", "version": entry["version"], "enabled": entry["enabled"], "ref": _ref(mod_id, entry)})
        elif current.version != entry["version"]:
            steps.append({"id": mod_id, "action": "reinstall", "version": entry["version"], "installed": current.version, "enabled": entry["enabled"], "ref": _ref(mod_id, entry)})
        else:
            flag = enabled.get(mod_id, not current.has_code)
            steps.append({"id": mod_id, "action": "set-flag", "version": entry["version"], "enabled": entry["enabled"], "changed": flag != entry["enabled"]})
    for mod_id, current in sorted(installed.items()):
        if mod_id not in profile.mods and enabled.get(mod_id, not current.has_code):
            steps.append({"id": mod_id, "action": "disable-extra", "version": current.version})
    return steps


def _ref(mod_id: str, entry: dict[str, Any]) -> str:
    """Where ``use`` fetches a missing mod from: its registry key, else the recorded path/archive/URL, else ``id@version``."""
    source = entry.get("source") or {}
    key = source.get("registryKey")
    if key:
        return f"{key}@{entry['version']}"
    if source.get("source") in ("path", "archive", "url") and source.get("ref"):
        return str(source["ref"])
    return f"{mod_id}@{entry['version']}"
