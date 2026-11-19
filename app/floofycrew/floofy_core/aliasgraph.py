"""Immutable-asset cache bust through an alias graph, and ``.br``/``.gz`` sidelining (Requirement 5.6).

Hashed assets under ``static/dist/assets`` are served ``Cache-Control: public,
max-age=31536000, immutable`` (``dashboard/server.py`` ``_IMMUTABLE_CACHE_CONTROL``),
so a client that has ever loaded the app never refetches a name it has cached:
patching a chunk *in place* is invisible on a normal reload, and a lazy chunk is
imported by its hashed name from other immutable chunks, so even a fresh
``index.html`` cannot reach it directly. The theme patcher's answer, ported and
generalised here to any chunk target:

1. build the **importer graph** of the assets directory — chunk *A* imports *B*
   when *B*'s file name occurs in *A*'s text (hashed names are unique, so a
   textual reference is a reference; ``import``/``export … from``, ``import()``
   and Vite's dependency arrays are all caught by one token scan);
2. take the **importer closure** of the patched chunk(s): every chunk that can
   reach them, up to the entry the shell references;
3. **republish** every member under a new name ``<stem>-<runtag>-floofy.<ext>``
   with its member references rebased onto the alias names — unpatched members
   are byte-identical apart from those references, so the plain graph stays
   consistent and ``restore`` only has to sweep ``*-floofy.*`` and the backups;
4. **repoint** the never-cached ``index.html`` (``<script type=module src>``,
   ``<link rel=modulepreload>``, import-map values) at the aliases;
5. **sideline** the pre-compressed ``.br``/``.gz`` sidecars of every file patched
   in place (a server preferring them would serve the stale compressed bytes) by
   renaming them to the backup name, so ``restore`` puts them back.

One ``runtag`` (digest of the patched bytes) names the whole set, so alias names
are stable across re-runs and cycles between chunks are harmless; stale aliases
of a stem from a previous runtag are removed.

Since spike 1.4 the primary path for module patches is the import-map remap
(``patches.merge_import_map``), which needs none of this; the alias graph is the
fallback for targets the import map cannot reach — the entry ``main-*.js`` itself,
stylesheets, or a client without import-map support.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .deploy import Backups, DeployManifest, added_name, is_floofy_added, sha256_file

__all__ = [
    "ALIAS_RE",
    "AliasResult",
    "ChunkGraph",
    "SIDECAR_SUFFIXES",
    "alias_for",
    "build_graph",
    "is_alias",
    "reference_pattern",
    "repoint_index",
    "republish",
    "runtag_for",
    "sideline_sidecars",
    "stale_aliases",
]

#: Pre-compressed sidecar suffixes the host's build emits beside hashed assets.
SIDECAR_SUFFIXES: tuple[str, ...] = (".br", ".gz")

#: Asset extensions that take part in the graph.
_ASSET_EXTS = ("js", "mjs", "css")
_TOKEN_RE = re.compile(r"(?<![\w.-])[\w.-]+\.(?:" + "|".join(_ASSET_EXTS) + r")(?![\w.-])")
#: ``<stem>-<8 hex>-floofy.<ext>`` — an alias this module published.
ALIAS_RE = re.compile(r"^(?P<stem>.+)-(?P<runtag>[0-9a-f]{8})-floofy\.(?P<ext>" + "|".join(_ASSET_EXTS) + r")$")


def is_alias(name: str | Path) -> bool:
    return ALIAS_RE.match(Path(name).name) is not None


def alias_for(name: str, runtag: str) -> str:
    """``main-qB7mk2ul.js`` → ``main-qB7mk2ul-<runtag>-floofy.js``."""
    return added_name(name, runtag)


def reference_pattern(name: str) -> re.Pattern[str]:
    """Matches the original hashed name or any previous alias of it, at a token boundary."""
    stem, dot, ext = name.rpartition(".")
    return re.compile(r"(?<![\w.-])" + re.escape(stem) + r"(?:-[0-9a-f]{8}-floofy)?\." + re.escape(ext) + r"(?![\w.-])")


def runtag_for(paths: Iterable[Path]) -> str:
    """Eight hex digits over the bytes of the (patched) seed files, in sorted order."""
    digest = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


@dataclass
class ChunkGraph:
    """Assets and who references whom (``importers[b]`` = chunks whose text names ``b``)."""

    assets_dir: Path
    chunks: dict[str, Path] = field(default_factory=dict)
    references: dict[str, set[str]] = field(default_factory=dict)  # a -> names a mentions
    importers: dict[str, set[str]] = field(default_factory=dict)  # b -> chunks mentioning b

    def closure(self, seeds: Iterable[str]) -> list[str]:
        """Seeds first, then every transitive importer in breadth-first order (deterministic)."""
        order: list[str] = []
        seen: set[str] = set()
        frontier = [s for s in seeds if s in self.chunks]
        while frontier:
            next_frontier: list[str] = []
            for name in frontier:
                if name in seen:
                    continue
                seen.add(name)
                order.append(name)
                next_frontier.extend(sorted(self.importers.get(name, ())))
            frontier = next_frontier
        return order


def build_graph(assets_dir: Path, *, exclude: Iterable[str] = ()) -> ChunkGraph:
    """Scan every asset once and build the reference and importer maps (aliases excluded).

    ``exclude`` holds file-name globs to leave out (for example the artefacts of
    another tool found in a dist that is not vanilla).
    """
    assets_dir = Path(assets_dir)
    graph = ChunkGraph(assets_dir)
    globs = list(exclude)
    for path in sorted(assets_dir.iterdir()) if assets_dir.is_dir() else []:
        if not path.is_file() or is_floofy_added(path) or path.suffix.lstrip(".") not in _ASSET_EXTS:
            continue
        if any(fnmatch.fnmatchcase(path.name, glob) for glob in globs):
            continue
        graph.chunks[path.name] = path
    for name, path in graph.chunks.items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        mentioned = {token for token in _TOKEN_RE.findall(text) if token in graph.chunks and token != name}
        graph.references[name] = mentioned
        for other in mentioned:
            graph.importers.setdefault(other, set()).add(name)
    return graph


def stale_aliases(assets_dir: Path, name: str, keep: str | None) -> list[Path]:
    """Aliases of ``name``'s stem other than ``keep`` (from earlier runtags)."""
    stem, _dot, ext = name.rpartition(".")
    found: list[Path] = []
    for candidate in sorted(Path(assets_dir).glob(f"{stem}-*-floofy.{ext}")):
        match = ALIAS_RE.match(candidate.name)
        if match and match.group("stem") == stem and candidate.name != keep:
            found.append(candidate)
    return found


def sideline_sidecars(file: Path, backups: Backups, manifest: DeployManifest | None = None, mod: str = "") -> list[tuple[Path, Path]]:
    """Rename ``<file>.br`` / ``<file>.gz`` to their backup names so the plain file is served.

    Reversible: the moved name is the sidecar's backup path, which ``restore``
    moves back like any other backup. Recorded as ``sidelined[]`` in the manifest;
    a sidecar already parked by an earlier run is recorded again so it stays parked.
    """
    moved: list[tuple[Path, Path]] = []
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(file) + suffix)
        target = backups.backup_path(sidecar)
        if sidecar.is_file():
            if target.exists():
                sidecar.unlink()  # a previous run already parked the original bytes
            else:
                sidecar.rename(target)
        elif not target.exists():
            continue  # no sidecar for this file
        # else: already sidelined by an earlier run — still record it so the manifest keeps it parked
        moved.append((sidecar, target))
        if manifest is not None:
            manifest.record_sidelined(sidecar, target, mod)
    return moved


def repoint_index(index_html: str, aliases: dict[str, str]) -> tuple[str, int]:
    """Replace every reference to a member (original name or old alias) with its alias; returns ``(html, count)``."""
    total = 0
    for name, alias in aliases.items():
        index_html, count = reference_pattern(name).subn(alias, index_html)
        total += count
    return index_html, total


@dataclass
class AliasResult:
    runtag: str
    members: list[str]
    aliases: dict[str, str]
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    sidelined: list[tuple[Path, Path]] = field(default_factory=list)
    index_html: str | None = None
    index_refs: int = 0

    def summary(self) -> str:
        return (
            f"alias graph {self.runtag}: {len(self.members)} member(s), {len(self.written)} alias(es) written, "
            f"{len(self.removed)} stale removed, {len(self.sidelined)} sidecar(s) sidelined, index refs {self.index_refs}"
        )


def republish(
    assets_dir: Path,
    seeds: Iterable[Path],
    *,
    backups: Backups | None = None,
    manifest: DeployManifest | None = None,
    mod: str = "",
    index_html: str | None = None,
    graph: ChunkGraph | None = None,
) -> AliasResult:
    """Publish the importer closure of ``seeds`` under alias names and repoint ``index_html``.

    ``seeds`` are the chunks already patched in place. Their ``.br``/``.gz``
    sidecars are sidelined; aliases are written only when their bytes differ from
    what is on disk; stale aliases of the same stems are removed; every alias is
    recorded as an added file. The repointed shell text is returned in
    ``AliasResult.index_html`` for the caller to write — it is never written here.
    """
    assets_dir = Path(assets_dir)
    sidecars = backups or Backups()
    seed_paths = sorted({Path(s) for s in seeds})
    graph = graph or build_graph(assets_dir)
    runtag = runtag_for(seed_paths)
    members = graph.closure(p.name for p in seed_paths)
    aliases = {name: alias_for(name, runtag) for name in members}
    result = AliasResult(runtag=runtag, members=members, aliases=aliases)

    for seed in seed_paths:
        result.sidelined.extend(sideline_sidecars(seed, sidecars, manifest, mod))

    for name in members:
        source = graph.chunks[name]
        body = source.read_text(encoding="utf-8", errors="replace")
        for other, alias in aliases.items():
            if other != name:  # self references (source-map comments) stay put
                body = reference_pattern(other).sub(alias, body)
        destination = assets_dir / aliases[name]
        if destination.exists() and destination.read_text(encoding="utf-8", errors="replace") == body:
            result.unchanged.append(destination)
        else:
            destination.write_text(body, encoding="utf-8")
            result.written.append(destination)
        if manifest is not None:
            manifest.record_added(destination, sha256_file(destination), mod)
        for stale in stale_aliases(assets_dir, name, aliases[name]):
            stale.unlink()
            result.removed.append(stale)
            if manifest is not None:
                manifest.forget(stale)

    if index_html is not None:
        result.index_html, result.index_refs = repoint_index(index_html, aliases)
    return result
