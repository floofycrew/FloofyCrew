"""``registry_tools.seed`` — the archive records of one FloofyCrew release for a registry repository (Requirement 8.2; public-first Requirement 3.2 (e), 3.3, 4.1; design DR-9).

The record logic that lived in the internal ``seed_public_records.py`` script,
as a library so the Forge's release stage (design step 7: "the direct signed
registry commit") and that script — now a thin CLI over :func:`seed_release` —
write **the same records**. Two kinds of first-party mod are covered:

1. **mods under ``mods/`` of the public tree** (``export``: the release's clone,
   or a tree extracted from its release commit with :func:`committed_tree`), read
   from the public tree and never from an internal overlay, so the record
   describes what GitHub users see;
2. **mods in their own repository** (``mods``: the loaded entries of the Forge's
   ``mods.json`` — anything with ``id`` and ``repo`` as attributes or keys; this
   module imports nothing of the Forge), read from the **committed** tree of a
   clone of that repository (``git archive``: tracked files only, so ``.git`` and
   untracked files never reach the zip). Their curated ``mod.json`` points at the
   mod's own repository (the entry's ``repo``); an existing ``mod.json`` is left
   as the curators wrote it.

Both kinds use :func:`registry_tools.bootstrap._seed_mod` — the function the
public registry was seeded with — so the record shape and the archive bytes
match the existing rows and the asset the Forge uploads (``forge.release``
archives the same committed tree with the same ``deterministic_zip``). A version
that already has a ``release.json`` is skipped (``skipped: "record exists"``);
with a ``released`` predicate a version whose GitHub release ``<id>-<version>``
does not exist is skipped too (``skipped: "no release <tag>"``), so a record
never names an asset that is not there. Nothing here commits, signs or pushes:
the caller rebuilds the index (``registry_tools build --sign``) and lands.
"""
from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .bootstrap import _seed_mod
from .repo import RegistryRepo, RepoError

__all__ = ["CONTACT", "ModEntry", "ReleasedPredicate", "SeedError", "committed_tree", "export_mod_dirs", "mod_entry", "release_tag", "seed_one", "seed_release", "seed_standalone"]

#: Who answers for a first-party mod in the curated ``mod.json`` (the registry's community bar, Requirement 8.10).
CONTACT = "floofycrew"

#: ``released(mod_id, version) -> bool``: whether the archive ``<id>-<version>`` exists where the record will point.
ReleasedPredicate = Callable[[str, str], bool]


class SeedError(RuntimeError):
    """A record could not be written: not a FloofyCrew tree, a clone missing or naming another mod, a registry that cannot be read."""


@dataclass(frozen=True)
class ModEntry:
    """A standalone first-party mod as this module needs it: its id and its public repository (the curated record's link)."""

    id: str
    repo: str


def mod_entry(mod: Any) -> ModEntry:
    """Accept a ``mods.json`` entry in either spelling: an object with ``id`` and ``repo`` attributes (the Forge's ``Mod``) or a mapping with those keys."""
    if isinstance(mod, ModEntry):
        return mod
    if isinstance(mod, Mapping):
        mod_id, repo = mod.get("id"), mod.get("repo")
    else:
        mod_id, repo = getattr(mod, "id", None), getattr(mod, "repo", None)
    if not isinstance(mod_id, str) or not mod_id or not isinstance(repo, str) or not repo:
        raise SeedError(f"not a standalone mod entry (needs a non-empty id and repo): {mod!r}")
    return ModEntry(mod_id, repo)


def export_mod_dirs(export: Path) -> list[Path]:
    """The mod directories of the public tree, sorted by id."""
    mods = Path(export) / "mods"
    if not mods.is_dir():
        raise SeedError(f"{export}: no mods/ directory (not a FloofyCrew tree)")
    return sorted(p for p in mods.iterdir() if p.is_dir() and (p / "floofy.json").is_file())


def committed_tree(clone: Path, target: Path, *, ref: str = "HEAD", paths: Iterable[str] = ()) -> Path:
    """Extract ``git archive <ref> [paths…]`` of ``clone`` into ``target`` and return it — the committed files only, never the working tree."""
    argv = ["git", "-C", str(clone), "archive", "--format=tar", ref, *paths]
    archive = subprocess.run(argv, capture_output=True, check=False, timeout=300)
    if archive.returncode != 0:
        raise SeedError(f"{clone}: git archive {ref} failed: {archive.stderr.decode(errors='replace').strip()[-300:]}")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(target, filter="data")
    return target


def release_tag(repo: RegistryRepo, mod_id: str, version: str) -> str:
    """The tag an archive record of this registry names: ``<id>-<version>`` when ``archiveUrlTemplate`` spells it, else the bare version (as ``_seed_mod`` does)."""
    return f"{mod_id}-{version}" if "{id}-{version}" in str(repo.config.get("archiveUrlTemplate", "")) else version


def seed_one(repo: RegistryRepo, mod_dir: Path, *, floofycrew_repo: str, own_repo: str | None = None, released: ReleasedPredicate | None = None) -> dict[str, Any]:
    """One record from ``mod_dir`` (a tree holding ``floofy.json``); ``own_repo`` is a standalone mod's repository for a *new* curated ``mod.json``."""
    manifest = json.loads((Path(mod_dir) / "floofy.json").read_text(encoding="utf-8"))
    mod_id, version = str(manifest["id"]), str(manifest["version"])
    tag = release_tag(repo, mod_id, version)
    if (repo.version_dir(mod_id, version) / "release.json").is_file():
        return {"id": mod_id, "version": version, "tag": tag, "skipped": "record exists"}
    if released is not None and not released(mod_id, version):
        return {"id": mod_id, "version": version, "tag": tag, "skipped": f"no release {tag}"}
    mod_json = repo.mods_dir / mod_id / "mod.json"
    curated_before = mod_json.read_text(encoding="utf-8") if mod_json.is_file() else None
    out = _seed_mod(repo, Path(mod_dir), floofycrew_repo=floofycrew_repo, channel="stable", archives_in_repo=False, record_kind="archive", contact=CONTACT)
    if curated_before is not None:
        mod_json.write_text(curated_before, encoding="utf-8")  # the curators' metadata outranks the seed's defaults
    elif own_repo is not None:
        curated = json.loads(mod_json.read_text(encoding="utf-8"))
        curated["repo"] = own_repo
        curated["links"] = {"source": own_repo, "docs": f"{own_repo}#readme"}
        mod_json.write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
    out["description"] = manifest.get("description", "")
    return out


def seed_standalone(repo: RegistryRepo, mod: Any, clone: Path, *, floofycrew_repo: str, released: ReleasedPredicate | None = None) -> dict[str, Any]:
    """One ``mods.json`` entry from the committed tree of its clone; the clone's manifest must carry the entry's id."""
    entry = mod_entry(mod)
    clone = Path(clone)
    if not clone.is_dir():
        raise SeedError(f"{entry.id}: clone {clone} is missing (pass --clone {entry.id}=<path>)")
    with tempfile.TemporaryDirectory(prefix=f"seed-{entry.id}-") as tmp:
        tree = committed_tree(clone, Path(tmp) / entry.id)
        if not (tree / "floofy.json").is_file():
            raise SeedError(f"{clone}: HEAD has no floofy.json at its root")
        manifest_id = json.loads((tree / "floofy.json").read_text(encoding="utf-8")).get("id")
        if manifest_id != entry.id:
            raise SeedError(f"{clone}: floofy.json id is {manifest_id!r}, expected {entry.id!r}")
        return seed_one(repo, tree, floofycrew_repo=floofycrew_repo, own_repo=entry.repo, released=released)


def seed_release(export: Path, mods: Iterable[Any] = (), *, registry: Path | RegistryRepo, clones: Mapping[str, Path] | None = None, released: ReleasedPredicate | None = None) -> list[dict[str, Any]]:
    """Write the release's archive records into ``registry``: the export's mods first, then every standalone ``mod`` (by id) from ``clones[mod.id]``; one row each, in that order.

    ``export`` is the public FloofyCrew tree (``mods/<id>/floofy.json``; the release clone, or
    :func:`committed_tree` of its release commit); ``mods`` the loaded standalone entries (``id``,
    ``repo``) and ``clones`` where each one's clone is — a clone for an id not in ``mods`` and an
    entry without a clone are both :class:`SeedError` (the list is the one source of truth,
    Requirement 4.1). ``released`` filters the versions a record may be written for (see the module
    docstring). Each row is ``_seed_mod``'s (``id, version, record, archive, sha256, size, url,
    tag``) plus ``description``, or ``{id, version, tag, skipped}``.
    """
    try:
        repo = registry if isinstance(registry, RegistryRepo) else RegistryRepo.load(Path(registry))
    except RepoError as exc:
        raise SeedError(str(exc)) from exc
    floofycrew_repo = str(repo.config.get("floofycrew") or "")
    if not floofycrew_repo:
        raise SeedError(f"{repo.root}/registry.json names no floofycrew repository (the curated records link to it)")
    results = [seed_one(repo, mod_dir, floofycrew_repo=floofycrew_repo, released=released) for mod_dir in export_mod_dirs(export)]
    entries = sorted((mod_entry(mod) for mod in mods), key=lambda m: m.id)
    clones = {str(k): Path(v) for k, v in dict(clones or {}).items()}
    unknown = sorted(set(clones) - {entry.id for entry in entries})
    if unknown:
        raise SeedError(f"clone(s) for mods not in the standalone list: {', '.join(unknown)} (the mods.json list is the one source of truth)")
    for entry in entries:
        if entry.id not in clones:
            raise SeedError(f"{entry.id}: no clone given (pass --clone {entry.id}=<path>)")
        results.append(seed_standalone(repo, entry, clones[entry.id], floofycrew_repo=floofycrew_repo, released=released))
    return results
