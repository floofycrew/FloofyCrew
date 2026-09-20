"""Build ``index.json`` from the registry records (Requirement 8.1, 9.2).

The index is derived — never edited by hand: every field comes from the mod
records (:mod:`registry_tools.repo`) and the compatibility matrix. Per version:

* the Requirement 8.1 fields from the manifest (``version``, ``kirocrew.version``
  → ``kirocrew``, ``kirocrew.editions`` → ``editions``, ``dependsOn`` →
  ``dependencies``) and the release record (``files[]``, ``channel``,
  ``publishedAt``, ``tag``, ``yanked``);
* ``compat{hostVersion: verdict}`` folded in from ``compat.json``: every matrix
  row that has a cell for this ``id@version`` contributes ``hostVersion →
  verdict`` (overrides applied; when two rows disagree for one host version the
  most cautious verdict wins: ``broken`` > ``expected`` > ``tested``), so a
  client can grade versions from the index alone before downloading anything;
* ``kinds`` (the part kinds) and, for a pure App Kit app, ``app{name, subdirectory}``.

The result is checked against ``index.schema.json`` before it is written;
``--check`` compares it with the committed ``index.json`` instead (the CI gate
that the index was regenerated after a record change). Signing is a separate
step (``sign``) or ``--sign KEY`` here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.canonical import canonical_bytes
from floofy_core.compat import CompatCache
from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance

from .repo import RegistryRepo, RepoError, VersionRecord

__all__ = ["BuildResult", "build_index", "fold_compat", "write_index"]

CAUTION = {"broken": 3, "expected": 2, "tested": 1}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class BuildResult:
    index: dict[str, Any]
    problems: list[str] = field(default_factory=list)  # schema violations and record defects (the build fails on any)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def fold_compat(compat: CompatCache | None, mod_id: str, version: str) -> dict[str, str]:
    """``hostVersion → verdict`` for one ``mod@version`` across every matrix row (most cautious verdict wins on a clash)."""
    cells: dict[str, str] = {}
    if compat is None:
        return cells
    for row in compat.rows:
        verdict = row.verdict(mod_id, version)
        if verdict is None:
            continue
        current = cells.get(row.host_version)
        if current is None or CAUTION.get(verdict, 0) > CAUTION.get(current, 0):
            cells[row.host_version] = verdict
    return dict(sorted(cells.items()))


def _dependencies(manifest: dict[str, Any]) -> dict[str, Any]:
    depends = manifest.get("dependsOn")
    return {str(k): v for k, v in depends.items()} if isinstance(depends, dict) else {}


def _version_entry(record: VersionRecord, compat: CompatCache | None) -> dict[str, Any]:
    manifest, release = record.manifest, record.release
    host = manifest.get("kirocrew") if isinstance(manifest.get("kirocrew"), dict) else {}
    entry: dict[str, Any] = {
        "version": record.version,
        "kirocrew": str(host.get("version") or "*"),
        "editions": [str(e) for e in (host.get("editions") or ["internal", "external"])],
        "compat": fold_compat(compat, record.mod_id, record.version),
        "files": [{k: f[k] for k in ("url", "sha256", "size", "name") if k in f} for f in record.files],
        "dependencies": _dependencies(manifest),
        "channel": str(release.get("channel") or "stable"),
        "publishedAt": str(release.get("publishedAt") or ""),
    }
    if isinstance(host.get("channels"), list) and host["channels"]:
        entry["channels"] = [str(c) for c in host["channels"]]
    if isinstance(host.get("strict"), bool) and host["strict"]:
        entry["strict"] = True
    if record.tag != record.version:
        entry["tag"] = record.tag
    if record.kinds:
        entry["kinds"] = record.kinds
    app = record.app_facts()
    if app is not None:
        entry["app"] = app
    if isinstance(release.get("yanked"), str) and release["yanked"]:
        entry["yanked"] = release["yanked"]
    return entry


def build_index(repo: RegistryRepo, *, compat: CompatCache | None = None, generated_at: str | None = None, floofycrew_version: str | None = None) -> BuildResult:
    """Assemble the index document from every record; schema and record defects land in ``problems``."""
    if compat is None and repo.compat_path.is_file():
        compat = CompatCache.load(repo.compat_path)
    result = BuildResult({"schema": 1, "source": repo.source, "generatedAt": generated_at or _now(), "mods": []})
    if floofycrew_version:
        result.index["floofycrew"] = floofycrew_version
    try:
        mods = list(repo.mods())
    except RepoError as exc:
        result.problems.append(str(exc))
        return result
    for mod in mods:
        if not mod.versions:
            result.notes.append(f"{mod.mod_id}: no version directories; listed with an empty versions[]")
        repo_url = mod.repo()
        if repo_url is None:
            result.problems.append(f"{mod.mod_id}: no repository (mods/{mod.mod_id}/mod.json \"repo\" or a manifest links.repo)")
            repo_url = ""
        entry: dict[str, Any] = {
            "id": mod.mod_id,
            "name": str(mod.field("name", mod.mod_id)),
            "description": str(mod.field("description", "")),
            "authors": [str(a) for a in (mod.field("authors", []) or [])],
            "tags": sorted({str(t) for t in (mod.field("tags", []) or [])}),
            "repo": repo_url,
            "versions": [_version_entry(v, compat) for v in mod.versions],
        }
        for optional in ("license", "links", "icon"):
            value = mod.field(optional)
            if value:
                entry[optional] = value
        result.index["mods"].append(entry)
    for error in validate_instance(result.index, load_schema("index")):
        result.problems.append(f"index.json{error.path or '/'}: {error.message}")
    return result


def write_index(repo: RegistryRepo, result: BuildResult, *, out: Path | None = None) -> Path:
    target = Path(out) if out else repo.index_path
    target.write_text(json.dumps(result.index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def index_matches(existing: Path, result: BuildResult) -> tuple[bool, str]:
    """Whether the committed index equals the rebuilt one, ignoring ``generatedAt`` (compared in canonical form)."""
    try:
        committed = json.loads(Path(existing).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"{existing} does not exist; run `registry_tools build`"
    except (OSError, ValueError) as exc:
        return False, f"{existing}: {exc}"
    if not isinstance(committed, dict):
        return False, f"{existing}: not a JSON object"
    left = {k: v for k, v in committed.items() if k != "generatedAt"}
    right = {k: v for k, v in result.index.items() if k != "generatedAt"}
    if canonical_bytes(left) != canonical_bytes(right):
        return False, f"{existing} is stale: it differs from the index rebuilt from mods/ (run `registry_tools build` and commit)"
    return True, f"{existing} is up to date with mods/"
