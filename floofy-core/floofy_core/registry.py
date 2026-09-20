"""The registry client's local side: the merged ``cache/index.json`` (Requirement 8.1, 8.4, 8.5, 9.2).

``floofy registry refresh`` (task 6.8) fetches every configured source's
``index.json`` + ``compat.json`` over HTTPS into ``cache/sources/<key>/`` and
merges the **usable** ones (signature verified, or the source explicitly
``allowUnsigned``) into ``cache/index.json`` / ``cache/compat.json``. This module
reads that merged index for ``search``, ``install <id[@version]>``, ``update``
and ``which`` (hash lookup):

* :class:`IndexCache` — ``mods[]`` records (design "Registry index"); a mod id
  that collides across sources is namespaced ``<source>/<id>`` (Requirement 8.4),
  ``<source>`` being the user's configured label for that source (``--name``,
  else the cache key), so an index cannot choose its own namespace;
* :func:`IndexCache.best_version` — "newest mod version known to work here"
  (Requirement 9.2): candidates whose ``kirocrew`` range contains the host base
  version, whose ``editions`` admit this edition and whose verdict for this host
  is not ``broken``; the verdict of a ``mod@version`` is the compat matrix row
  for ``edition × channel × hostVersion`` (:class:`floofy_core.compat.CompatCache`)
  or, failing that, the index's own inline ``compat`` cell. ``tested`` beats
  ``expected`` beats an unknown cell, and within one verdict the newest version
  wins; a ``yanked`` version is never picked automatically;
* :func:`IndexCache.pick` — the version to install: an exact requested one, else
  :func:`IndexCache.best_version`;
* :func:`IndexCache.by_hash` — ``mod@version`` for any file's SHA-256 (Requirement 8.5),
  over both record shapes: the ``files[]`` of an asset record name archives by
  URL, those of a link record name the checkout's files by path (Requirement 8.9;
  :attr:`IndexVersion.link` carries ``repo``/``tag``/``commit``/``manifestSha256``).

The network side (sources, trust, signatures) is :mod:`floofy_core.registry_sources`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .compat import CompatCache
from .datahome import DataHome
from .semver import InvalidRange, InvalidVersion, Range, Version

__all__ = ["BestVersion", "IndexCache", "IndexVersion", "ModEntry", "VERDICT_RANK"]

#: ``tested`` beats ``expected`` beats an unknown cell; ``broken`` is excluded (Requirement 9.2).
VERDICT_RANK = {"tested": 3, "expected": 2, None: 1, "": 1, "broken": 0}


@dataclass(frozen=True)
class IndexVersion:
    version: str
    kirocrew: str
    editions: tuple[str, ...]
    compat: dict[str, str]
    files: tuple[dict[str, Any], ...]
    dependencies: dict[str, Any]
    channel: str | None
    published_at: str | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def parsed(self) -> Version | None:
        try:
            return Version.parse(self.version)
        except InvalidVersion:
            return None

    def supports(self, base_version: Version | str, edition: str | None) -> bool:
        try:
            in_range = Range.parse(self.kirocrew or "*").contains(base_version)
        except (InvalidRange, ValueError):
            in_range = False
        edition_ok = not self.editions or edition in (None, "unknown") or edition in self.editions
        return in_range and edition_ok

    def verdict_for(self, host_version: str | None) -> str | None:
        return self.compat.get(host_version) if host_version else None

    @property
    def yanked(self) -> str | None:
        reason = self.raw.get("yanked")
        return reason if isinstance(reason, str) and reason else None

    @property
    def changelog(self) -> str | None:
        """The version's release-notes URL when the record names one (Requirement 16.6)."""
        url = self.raw.get("changelog")
        return url if isinstance(url, str) and url.startswith("https://") else None

    @property
    def link(self) -> dict[str, Any] | None:
        """The link record (``repo``, ``tag``, ``commit``, ``manifestSha256``[, ``path``]) when the version is link-based (Requirement 8.9), else ``None``."""
        link = self.raw.get("link")
        return dict(link) if isinstance(link, dict) and isinstance(link.get("repo"), str) else None

    @property
    def asset_files(self) -> list[dict[str, Any]]:
        """The downloadable ``{url, sha256, size}`` entries (an asset record); empty for a link record."""
        return [dict(f) for f in self.files if isinstance(f.get("url"), str)]

    @property
    def path_files(self) -> list[dict[str, Any]]:
        """The ``{path, sha256, size}`` entries a link record lists for the checkout; empty for an asset record."""
        return [dict(f) for f in self.files if isinstance(f.get("path"), str) and "url" not in f]

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "kirocrew": self.kirocrew, "editions": list(self.editions), "compat": dict(self.compat), "files": [dict(f) for f in self.files], "dependencies": dict(self.dependencies), "channel": self.channel, "publishedAt": self.published_at, "yanked": self.yanked, "link": self.link, "changelog": self.changelog}


@dataclass
class ModEntry:
    id: str
    name: str
    description: str
    authors: list[str]
    tags: list[str]
    repo: str | None
    source: str
    versions: list[IndexVersion]
    #: ``<source>/<id>`` when the id collides across sources (Requirement 8.4), else the id.
    key: str = ""

    def __post_init__(self) -> None:
        self.key = self.key or self.id

    def version(self, text: str) -> IndexVersion | None:
        return next((v for v in self.versions if v.version == text), None)

    def matches(self, query: str) -> bool:
        needle = query.lower()
        haystack = " ".join([self.id, self.name, self.description, *self.tags, self.key]).lower()
        return all(term in haystack for term in needle.split())

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "name": self.name, "description": self.description, "authors": list(self.authors), "tags": list(self.tags), "repo": self.repo, "source": self.source, "versions": [v.to_dict() for v in self.versions]}


@dataclass
class BestVersion:
    """:meth:`IndexCache.best_version`'s answer: the entry, the chosen version (or ``None``), its verdict here and why."""

    entry: ModEntry | None
    version: IndexVersion | None
    verdict: str | None
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.entry.key if self.entry else None, "version": self.version.version if self.version else None, "verdict": self.verdict, "why": self.why}


@dataclass
class IndexCache:
    mods: list[ModEntry] = field(default_factory=list)
    source: str = ""
    generated_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.mods) or not self.notes

    @classmethod
    def load(cls, home: DataHome) -> "IndexCache":
        return cls.load_path(home.index_cache)

    @classmethod
    def load_path(cls, path: Path) -> "IndexCache":
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(source=str(path), notes=["no registry cache yet (floofy registry add <url>, then floofy registry refresh)"])
        except (OSError, ValueError) as exc:
            return cls(source=str(path), notes=[f"registry cache unreadable: {exc}"])
        if isinstance(document, dict) and document.get("merged") is True and isinstance(document.get("documents"), list):
            # the merged cache `floofy registry refresh` writes: one entry per usable source under its configured
            # label, which is the namespace on collisions (Requirement 8.4) — never a name the index claims for itself
            pairs = [(str(entry.get("source") or entry.get("key") or "source"), entry.get("document")) for entry in document["documents"] if isinstance(entry, dict)]
            cache = cls.from_documents(pairs, prefer_label=True)
            cache.source = str(path)
            cache.generated_at = document.get("generatedAt") if isinstance(document.get("generatedAt"), str) else cache.generated_at
            if not pairs:
                cache.notes.append("the registry cache has no usable source (floofy registry refresh; unsigned indexes are refused by default)")
            return cache
        return cls.from_documents([(str(path), document)])

    @classmethod
    def from_documents(cls, documents: Iterable[tuple[str, Any]], *, prefer_label: bool = False) -> "IndexCache":
        """Merge one or more ``index.json`` documents; ids colliding across sources get ``<source>/<id>`` keys.

        The source name of each document is the index's own ``source`` field unless
        ``prefer_label`` is set, in which case the caller's label wins (the merged
        cache passes the user's configured source label so an index cannot pick its
        own namespace).
        """
        cache = cls()
        seen: dict[str, ModEntry] = {}
        listed: set[tuple[str, str]] = set()
        for label, document in documents:
            if not isinstance(document, dict) or not isinstance(document.get("mods"), list):
                cache.notes.append(f"{label}: no mods[] in the index")
                continue
            source = str(label if prefer_label else (document.get("source") or label))
            cache.source = cache.source or source
            cache.generated_at = cache.generated_at or (document.get("generatedAt") if isinstance(document.get("generatedAt"), str) else None)
            for raw in document["mods"]:
                entry = _entry(raw, source)
                if entry is None or (source, entry.id) in listed:
                    continue  # the same source listing an id twice: keep the first
                listed.add((source, entry.id))
                if entry.id in seen:
                    first = seen[entry.id]
                    first.key = f"{first.source}/{first.id}"
                    entry.key = f"{source}/{entry.id}"
                    cache.notes.append(f"mod id {entry.id!r} exists in {first.source} and {source}; namespaced as {first.key} and {entry.key}")
                else:
                    seen[entry.id] = entry
                cache.mods.append(entry)
        return cache

    # -- queries ---------------------------------------------------------------------------------

    def find(self, ref: str) -> ModEntry | None:
        """By key (``source/id``) first, then by bare id when unambiguous."""
        for entry in self.mods:
            if entry.key == ref:
                return entry
        matches = [entry for entry in self.mods if entry.id == ref]
        return matches[0] if len(matches) == 1 else None

    def search(self, query: str) -> list[ModEntry]:
        return [entry for entry in self.mods if not query or entry.matches(query)]

    def best_version(self, ref: str, *, base_version: Version | str, edition: str | None, host_version: str | None, channel: str | None = None, compat: CompatCache | None = None) -> "BestVersion":
        """The newest version of ``ref`` known to work on this host (Requirement 9.2), with the verdict that ranked it.

        Every version is graded by the matrix row for ``edition × channel ×
        hostVersion`` (``compat``), else by the index's inline ``compat`` cell:
        ``tested`` > ``expected`` > unknown; ``broken`` and ``yanked`` versions are
        excluded, as are versions whose ``kirocrew`` range or ``editions`` do not
        admit this host. The reason for an empty answer names what was excluded.
        """
        entry = self.find(ref)
        if entry is None:
            return BestVersion(None, None, None, f"{ref!r} is not in the registry cache")
        row = compat.row_for(edition, channel, host_version) if compat is not None and edition and host_version else None
        graded: list[tuple[int, Version, IndexVersion, str | None]] = []
        excluded: list[str] = []
        for version in entry.versions:
            parsed = version.parsed
            if parsed is None:
                excluded.append(f"{version.version} (not SemVer)")
                continue
            if version.yanked:
                excluded.append(f"{version.version} (yanked: {version.yanked})")
                continue
            if not version.supports(base_version, edition):
                excluded.append(f"{version.version} (declares kirocrew {version.kirocrew}" + (f", editions {', '.join(version.editions)}" if version.editions else "") + ")")
                continue
            verdict = row.verdict(entry.id, version.version) if row is not None else None
            if verdict is None:
                verdict = version.verdict_for(host_version)
            if verdict == "broken":
                excluded.append(f"{version.version} (broken on {host_version})")
                continue
            graded.append((VERDICT_RANK.get(verdict, 1), parsed, version, verdict))
        if not graded:
            why = f"no version of {entry.key} works on host {host_version or base_version} ({edition or 'unknown edition'})"
            if excluded:
                why += ": " + "; ".join(excluded)
            return BestVersion(entry, None, None, why)
        graded.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _rank, _parsed, best, verdict = graded[0]
        return BestVersion(entry, best, verdict, "")

    def pick(self, ref: str, *, base_version: Version | str, edition: str | None, host_version: str | None, requested: str | None = None, channel: str | None = None, compat: CompatCache | None = None) -> tuple[ModEntry | None, IndexVersion | None, str]:
        """The version to install: an exact ``requested`` one, else :meth:`best_version` (Requirement 9.2)."""
        entry = self.find(ref)
        if entry is None:
            return None, None, f"{ref!r} is not in the registry cache"
        if requested:
            picked = entry.version(requested)
            return entry, picked, "" if picked else f"{ref} has no version {requested} in the cache"
        best = self.best_version(ref, base_version=base_version, edition=edition, host_version=host_version, channel=channel, compat=compat)
        return best.entry, best.version, best.why

    def by_hash(self, sha256: str) -> list[tuple[ModEntry, IndexVersion, dict[str, Any]]]:
        """Every ``(mod, version, file)`` whose file ``sha256`` matches (Requirement 8.5)."""
        wanted = sha256.lower().strip()
        hits: list[tuple[ModEntry, IndexVersion, dict[str, Any]]] = []
        for entry in self.mods:
            for version in entry.versions:
                for file in version.files:
                    if str(file.get("sha256", "")).lower() == wanted:
                        hits.append((entry, version, dict(file)))
        return hits

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "generatedAt": self.generated_at, "mods": len(self.mods), "notes": list(self.notes)}


def _entry(raw: Any, source: str) -> ModEntry | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    versions: list[IndexVersion] = []
    for item in raw.get("versions") or []:
        if not isinstance(item, dict) or not isinstance(item.get("version"), str):
            continue
        compat = item.get("compat") if isinstance(item.get("compat"), dict) else {}
        versions.append(
            IndexVersion(
                version=item["version"],
                kirocrew=str(item.get("kirocrew") or "*"),
                editions=tuple(str(e) for e in (item.get("editions") or [])),
                compat={str(k): str(v) for k, v in compat.items()},
                files=tuple(dict(f) for f in (item.get("files") or []) if isinstance(f, dict)),
                dependencies=dict(item.get("dependencies") or {}) if isinstance(item.get("dependencies"), dict) else {},
                channel=item.get("channel") if isinstance(item.get("channel"), str) else None,
                published_at=item.get("publishedAt") if isinstance(item.get("publishedAt"), str) else None,
                raw=item,
            )
        )
    return ModEntry(
        id=raw["id"],
        name=str(raw.get("name") or raw["id"]),
        description=str(raw.get("description") or ""),
        authors=[str(a) for a in (raw.get("authors") or [])],
        tags=[str(t) for t in (raw.get("tags") or [])],
        repo=raw.get("repo") if isinstance(raw.get("repo"), str) else None,
        source=source,
        versions=versions,
    )
