#!/usr/bin/env python3
"""Derive a small fixture ``static/dist`` from a real host dist, preserving its import-graph shape.

Task 3.7 (Requirement 13.3). The Patcher tests need dists from real host versions
without committing 90 MB each. Starting from a read-only source dist this script
keeps, byte for byte:

* ``index.html`` (the real anchors: inline import map, ``modulepreload`` hints,
  the module entry tag);
* one **seed chunk** — a hashed ``.js`` with at least two importers, preferring
  one that carries ``.br``/``.gz`` sidecars (the build emits them above ~1 KB)
  and then the smallest importer closure (``--seed`` overrides) — with its sidecars;

and for every other asset writes a stand-in of the same name:

* members of the seed's importer closure (up to the entry ``main-*.js``) become
  **skeletons** — a comment plus one ``import "./<name>";`` line per chunk the
  original referenced, so :mod:`floofy_core.aliasgraph` computes exactly the same
  graph; their sidecars become 3-byte stubs (the Patcher only renames them);
* every other asset referenced by ``index.html`` becomes a 0-byte file.

``FIXTURE.json`` records the source version and edition, the seed, the closure,
per-file byte counts and the original ``index.html`` hash — never a path. Nothing
is written under the source. A dist that is not vanilla (another tool's aliases
and backups beside the host files) is handled with ``--index <vanilla shell>``
and ``--exclude <glob>``. Usage::

    python scripts/make_fixture_payload.py --source <dist-dir> --out floofy-core/tests/fixtures/dist-<ver> \
        [--seed <chunk>] [--index <index.html>] [--exclude '<glob>' ...]

Standard library plus ``floofy_core`` (run from the repository root or with
``floofy-core`` on ``PYTHONPATH``).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "floofy-core"))

from floofy_core.aliasgraph import SIDECAR_SUFFIXES, build_graph  # noqa: E402
from floofy_core.payloads import default_edition_probe, read_host_version  # noqa: E402

#: Seeds larger than this are only used when nothing smaller has sidecars (keeps fixtures small).
SEED_MAX_BYTES = 8 * 1024

_ASSET_REF_RE = re.compile(r"""(?:src|href)=["']/(assets/[^"']+)["']""")
_IMPORT_MAP_RE = re.compile(r'<script\s+type="importmap"[^>]*>(.*?)</script>', re.S)
_CHUNK_TOKEN_RE = re.compile(r"(?<![\w.-])[\w.-]+\.(?:js|mjs|css)(?![\w.-])")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pick_seed(graph, explicit: str | None) -> str:
    if explicit:
        if explicit not in graph.chunks:
            raise SystemExit(f"--seed {explicit!r} is not a chunk of the source dist")
        return explicit
    def has_sidecar(name: str) -> bool:
        return any(Path(str(graph.chunks[name]) + suffix).is_file() for suffix in SIDECAR_SUFFIXES)

    eligible = [name for name in graph.chunks if name.endswith(".js") and len(graph.importers.get(name, ())) >= 2]
    if not eligible:
        raise SystemExit("no hashed .js chunk with two or more importers in the source dist")
    # the build emits .br/.gz only above ~1 KB: prefer a seed that has them (so sidelining is
    # exercised on real sidecars), then the smallest closure, then the smallest chunk
    with_sidecars = [name for name in eligible if has_sidecar(name)] or eligible
    small = [name for name in with_sidecars if graph.chunks[name].stat().st_size <= SEED_MAX_BYTES] or with_sidecars
    return min(small, key=lambda name: (len(graph.closure([name])), graph.chunks[name].stat().st_size, name))


def skeleton(name: str, original: bytes, references: set[str]) -> bytes:
    header = f"// floofy fixture skeleton of {name} ({len(original)} bytes in the source dist); references only\n"
    if name.endswith(".css"):
        return f"/* floofy fixture skeleton of {name} ({len(original)} bytes) */\n".encode()
    lines = [f'import "./{ref}";' for ref in sorted(references)]
    return (header + "\n".join(lines) + ("\n" if lines else "")).encode()


def referenced_by_index(index_html: str) -> set[str]:
    names = {m.group(1).split("/", 1)[1] for m in _ASSET_REF_RE.finditer(index_html)}
    match = _IMPORT_MAP_RE.search(index_html)
    if match:
        try:
            imports = json.loads(match.group(1)).get("imports", {})
        except json.JSONDecodeError:
            imports = {}
        for value in imports.values():
            if isinstance(value, str) and value.startswith("/assets/"):
                names.add(value[len("/assets/") :])
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="a real kiro_crew/static/dist directory (read-only)")
    parser.add_argument("--out", type=Path, required=True, help="fixture directory to (re)create")
    parser.add_argument("--seed", default=None, help="chunk name to keep whole (default: smallest with >= 2 importers)")
    parser.add_argument("--edition", default=None, help="edition label (default: probe the package)")
    parser.add_argument("--index", type=Path, default=None, help="use this file as the vanilla index.html (when the dist's own is not vanilla)")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="asset file-name glob to leave out, repeatable (another tool's artefacts)")
    parser.add_argument("--version", default=None, help="host version label (default: read from the package)")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    assets = source / "assets"
    index = (args.index or source / "index.html").resolve()
    if not index.is_file() or not assets.is_dir():
        raise SystemExit(f"{source} is not a dist directory (needs index.html and assets/)")
    package_dir = source.parent.parent
    version = args.version or (str(read_host_version(package_dir)) if (package_dir / "__init__.py").is_file() else "unknown")
    edition = args.edition or (default_edition_probe(package_dir) if (package_dir / "__init__.py").is_file() else "unknown")

    graph = build_graph(assets, exclude=args.exclude)
    seed = pick_seed(graph, args.seed)
    members = graph.closure([seed])
    index_html = index.read_text(encoding="utf-8")
    referenced = referenced_by_index(index_html)

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    shutil.copyfile(index, out / "index.html")
    files: dict[str, dict[str, int | str]] = {"index.html": {"bytes": index.stat().st_size, "kind": "verbatim"}}

    def emit(name: str, data: bytes, kind: str, original_size: int) -> None:
        (out / "assets" / name).write_bytes(data)
        files[f"assets/{name}"] = {"bytes": len(data), "sourceBytes": original_size, "kind": kind}

    for name in members:
        path = graph.chunks[name]
        original = path.read_bytes()
        if name == seed:
            emit(name, original, "verbatim", len(original))
            for suffix in SIDECAR_SUFFIXES:
                sidecar = Path(str(path) + suffix)
                if sidecar.is_file():
                    emit(name + suffix, sidecar.read_bytes(), "verbatim", sidecar.stat().st_size)
        else:
            emit(name, skeleton(name, original, graph.references.get(name, set())), "skeleton", len(original))
            for suffix in SIDECAR_SUFFIXES:
                sidecar = Path(str(path) + suffix)
                if sidecar.is_file():
                    emit(name + suffix, b"stb", "stub", sidecar.stat().st_size)
    for name in sorted(referenced - set(members)):
        original_path = assets / name
        if not original_path.is_file() or any(fnmatch.fnmatchcase(name, glob) for glob in args.exclude):
            continue
        emit(name, b"", "stub", original_path.stat().st_size)

    total = sum(int(v["bytes"]) for v in files.values())
    record = {
        "schema": 1,
        "sourceVersion": version,
        "edition": edition,
        "sourceIndexSha256": sha256(index.read_bytes()),
        "excludedAssets": sum(1 for p in assets.iterdir() if any(fnmatch.fnmatchcase(p.name, g) for g in args.exclude)),
        "seed": seed,
        "seedImporters": sorted(graph.importers.get(seed, ())),
        "closure": members,
        "entry": next((m for m in members if m.startswith("main-") and m.endswith(".js")), None),
        "sourceChunkCount": len(graph.chunks),
        "sourceSidecarCount": sum(1 for p in assets.iterdir() if p.suffix in SIDECAR_SUFFIXES),
        "fixtureBytes": total,
        "files": files,
    }
    (out / "FIXTURE.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{out}: {len(files)} files, {total} bytes; seed {seed} (closure {len(members)}), version {version}, edition {edition}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
