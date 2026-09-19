"""Alias-graph cache bust and sidecar sidelining tests (Requirement 5.6; task 3.4).

A synthetic dist with a four-level import chain (index.html → main → a → b → c),
a lazily imported chunk, a stylesheet and ``.br``/``.gz`` sidecars: closure
membership and order, rebased references, repointed shell, sidelining recorded
in the manifest, stale-alias removal, idempotent re-runs and the restore sweep.
Real fixture payloads are exercised in the fixture tests (task 3.7).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.aliasgraph import (
    ALIAS_RE,
    alias_for,
    build_graph,
    is_alias,
    reference_pattern,
    repoint_index,
    republish,
    runtag_for,
    sideline_sidecars,
    stale_aliases,
)
from floofy_core.deploy import BACKUP_SUFFIX, Backups, DeployManifest, is_floofy_added

MAIN, A, B, C, LAZY, LEAF, CSS = (
    "main-MMMM0000.js",
    "chunk-a-AAAA1111.js",
    "chunk-b-BBBB2222.js",
    "chunk-c-CCCC3333.js",
    "lazy-LLLL4444.js",
    "leaf-FFFF5555.js",
    "index-SSSS6666.css",
)

INDEX = f"""<!doctype html>
<html><head>
<script type="importmap">{{"imports":{{"react":"/vendor/react.mjs","#a":"/assets/{A}"}}}}</script>
<link rel="stylesheet" crossorigin href="/assets/{CSS}">
<link rel="modulepreload" crossorigin href="/assets/{A}">
<link rel="modulepreload" crossorigin href="/assets/{B}">
<script type="module" crossorigin src="/assets/{MAIN}"></script>
</head><body></body></html>
"""


def make_dist(root: Path) -> tuple[Path, Path]:
    assets = root / "assets"
    assets.mkdir(parents=True)
    files = {
        MAIN: f'import "./{A}";import("./{LAZY}").then(m=>m.x);console.log("main");\n',
        A: f'import{{b}}from"./{B}";export const a=b+1;\n',
        B: f'import{{c}}from"./{C}";export const b=c+1;\n',
        C: 'export const c=1;\n//# sourceMappingURL=chunk-c-CCCC3333.js.map\n',
        LAZY: f'import{{c}}from"./{C}";export const x=c;\n',
        LEAF: 'export const leaf=0;\n',
        CSS: ":root{--x:1}\n",
    }
    for name, text in files.items():
        (assets / name).write_text(text, encoding="utf-8")
        (assets / (name + ".br")).write_bytes(b"br:" + name.encode())
        (assets / (name + ".gz")).write_bytes(b"gz:" + name.encode())
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    return root / "index.html", assets


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- graph ---------------------------------------------------------------------------


def test_graph_references_and_importers(tmp_path: Path) -> None:
    _, assets = make_dist(tmp_path)
    graph = build_graph(assets)
    assert set(graph.chunks) == {MAIN, A, B, C, LAZY, LEAF, CSS}
    assert graph.references[MAIN] == {A, LAZY}
    assert graph.references[C] == set()  # the source-map comment is not a reference
    assert graph.importers[C] == {B, LAZY}
    assert graph.importers.get(LEAF, set()) == set() and graph.importers.get(CSS, set()) == set()


def test_closure_four_levels_and_lazy(tmp_path: Path) -> None:
    _, assets = make_dist(tmp_path)
    graph = build_graph(assets)
    assert graph.closure([C]) == [C, B, LAZY, A, MAIN]
    assert graph.closure([A]) == [A, MAIN]
    assert graph.closure([MAIN]) == [MAIN]
    assert graph.closure([LEAF]) == [LEAF]
    assert graph.closure(["nope.js"]) == []
    assert graph.closure([C, A]) == [C, A, B, LAZY, MAIN]


def test_graph_skips_aliases_and_missing_dir(tmp_path: Path) -> None:
    _, assets = make_dist(tmp_path)
    (assets / alias_for(A, "deadbeef")).write_text("x", encoding="utf-8")
    assert alias_for(A, "deadbeef") not in build_graph(assets).chunks
    assert build_graph(tmp_path / "nowhere").chunks == {}


# --- naming --------------------------------------------------------------------------


def test_alias_naming_and_reference_pattern() -> None:
    alias = alias_for(A, "1a2b3c4d")
    assert alias == "chunk-a-AAAA1111-1a2b3c4d-floofy.js" and is_alias(alias) and is_floofy_added(alias)
    assert not is_alias(A) and not is_alias("chunk-a-AAAA1111-floofy.js")
    pattern = reference_pattern(A)
    assert pattern.search(f'from"./{A}"') and pattern.search(f"/assets/{alias}")
    assert not pattern.search("xchunk-a-AAAA1111.js") and not pattern.search(f"{A}.map")
    assert pattern.sub("Z", f'import "./{A}";import "./{alias}";') == 'import "./Z";import "./Z";'
    assert ALIAS_RE.match(alias).group("runtag") == "1a2b3c4d"


def test_runtag_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    _, assets = make_dist(tmp_path)
    first = runtag_for([assets / C])
    assert first == runtag_for([assets / C]) and len(first) == 8
    (assets / C).write_text("export const c=2;\n", encoding="utf-8")
    assert runtag_for([assets / C]) != first


# --- republish -----------------------------------------------------------------------


def test_republish_closure_rebases_repoints_and_sidelines(tmp_path: Path) -> None:
    index, assets = make_dist(tmp_path)
    backups = Backups()
    manifest = DeployManifest("p", "0.7.0", "external")
    # the "patch": edit c in place (with a backup, as the Patcher would)
    backups.ensure(assets / C)
    (assets / C).write_text("export const c=42;/*floofy*/\n//# sourceMappingURL=chunk-c-CCCC3333.js.map\n", encoding="utf-8")

    result = republish(assets, [assets / C], backups=backups, manifest=manifest, mod="m", index_html=index.read_text())
    assert result.members == [C, B, LAZY, A, MAIN]
    assert len(result.written) == 5 and result.unchanged == [] and result.removed == []
    # rebased references inside aliases, untouched originals
    alias_b = (assets / result.aliases[B]).read_text()
    assert result.aliases[C] in alias_b and C not in alias_b.replace(result.aliases[C], "")
    assert (assets / B).read_text() == f'import{{c}}from"./{C}";export const b=c+1;\n'
    alias_c = (assets / result.aliases[C]).read_text()
    assert "sourceMappingURL=chunk-c-CCCC3333.js.map" in alias_c  # self reference stays
    alias_main = (assets / result.aliases[MAIN]).read_text()
    assert result.aliases[A] in alias_main and result.aliases[LAZY] in alias_main
    # index.html: module tag, both preloads and the import-map value repointed; css untouched
    assert result.index_html is not None and result.index_refs == 4
    assert f"/assets/{result.aliases[MAIN]}" in result.index_html and f"/assets/{MAIN}" not in result.index_html
    assert f'"#a":"/assets/{result.aliases[A]}"' in result.index_html
    assert f"/assets/{CSS}" in result.index_html
    # sidecars of the in-place patched seed moved to backup names, recorded
    assert result.sidelined == [(assets / (C + ".br"), assets / (C + ".br" + BACKUP_SUFFIX)), (assets / (C + ".gz"), assets / (C + ".gz" + BACKUP_SUFFIX))]
    assert not (assets / (C + ".br")).exists() and (assets / (C + ".br" + BACKUP_SUFFIX)).read_bytes() == b"br:" + C.encode()
    assert (assets / (B + ".br")).exists()  # untouched members keep their sidecars
    assert [s.path for s in manifest.sidelined] == [str(assets / (C + ".br")), str(assets / (C + ".gz"))]
    assert sorted(a.path for a in manifest.added) == sorted(str(assets / v) for v in result.aliases.values())
    assert result.summary().startswith(f"alias graph {result.runtag}")


def test_republish_is_idempotent_and_removes_stale_aliases(tmp_path: Path) -> None:
    index, assets = make_dist(tmp_path)
    (assets / C).write_text("export const c=42;\n", encoding="utf-8")
    first = republish(assets, [assets / C], index_html=index.read_text())
    again = republish(assets, [assets / C], index_html=first.index_html)
    assert again.runtag == first.runtag and again.written == [] and len(again.unchanged) == 5
    assert again.index_html == first.index_html and again.index_refs == 4  # aliases re-matched, same names
    # a new patch → new runtag → the old aliases are stale and removed
    (assets / C).write_text("export const c=43;\n", encoding="utf-8")
    manifest = DeployManifest("p", "0.7.0", "external")
    for alias in first.aliases.values():
        manifest.record_added(assets / alias, "x", "m")
    third = republish(assets, [assets / C], manifest=manifest, index_html=first.index_html)
    assert third.runtag != first.runtag and len(third.removed) == 5
    assert not any((assets / a).exists() for a in first.aliases.values())
    assert sorted(a.path for a in manifest.added) == sorted(str(assets / v) for v in third.aliases.values())
    assert third.index_refs == 4 and third.aliases[MAIN] in third.index_html
    assert stale_aliases(assets, C, third.aliases[C]) == []


def test_republish_css_member_and_multi_seed(tmp_path: Path) -> None:
    index, assets = make_dist(tmp_path)
    (assets / CSS).write_text(":root{--x:2}\n", encoding="utf-8")
    result = republish(assets, [assets / CSS, assets / A], index_html=index.read_text())
    assert set(result.members) == {CSS, A, MAIN}
    assert result.aliases[CSS].endswith("-floofy.css") and f"/assets/{result.aliases[CSS]}" in result.index_html
    assert result.index_refs == 4  # css link, a's preload and import-map value, the main tag
    assert result.index_html.count(result.aliases[A]) == 2


def test_sideline_sidecars_twice_and_restore(tmp_path: Path) -> None:
    index, assets = make_dist(tmp_path)
    backups = Backups()
    (assets / (LEAF + ".br")).unlink()
    (assets / (LEAF + ".gz")).unlink()
    before = snapshot(tmp_path)
    moved = sideline_sidecars(assets / A, backups)
    assert [m[0].name for m in moved] == [A + ".br", A + ".gz"]
    (assets / (A + ".br")).write_bytes(b"stale")  # the host re-laid a sidecar; sideline again
    moved = sideline_sidecars(assets / A, backups)
    assert len(moved) == 2  # the re-laid .br parked again (stale bytes dropped), the .gz still recorded as parked
    assert (assets / (A + ".br" + BACKUP_SUFFIX)).read_bytes() == b"br:" + A.encode() and not (assets / (A + ".br")).exists()
    assert sideline_sidecars(assets / LEAF, backups) == []  # no sidecars: nothing to park, nothing recorded
    # restore: every backup name moves back over its original name
    for backup in sorted(tmp_path.rglob("*" + BACKUP_SUFFIX)):
        backups.restore_file(backups.original_of(backup))
    assert snapshot(tmp_path) == before


def test_full_restore_sweep_after_republish(tmp_path: Path) -> None:
    index, assets = make_dist(tmp_path)
    before = snapshot(tmp_path)
    backups = Backups()
    backups.ensure(assets / C)
    (assets / C).write_text("export const c=42;\n", encoding="utf-8")
    result = republish(assets, [assets / C], backups=backups, index_html=index.read_text())
    backups.ensure(index)
    index.write_text(result.index_html, encoding="utf-8")
    assert snapshot(tmp_path) != before
    for added in sorted(p for p in tmp_path.rglob("*") if p.is_file() and is_floofy_added(p)):
        added.unlink()
    for backup in sorted(tmp_path.rglob("*" + BACKUP_SUFFIX)):
        backups.restore_file(backups.original_of(backup))
    assert snapshot(tmp_path) == before


# --- properties ----------------------------------------------------------------------

_hex = st.text(alphabet="0123456789abcdef", min_size=8, max_size=8)
_stem = st.text(alphabet="abcdefXYZ019_-", min_size=1, max_size=20).filter(lambda s: not s.endswith("-") and not s.startswith("-"))


@settings(max_examples=100, deadline=None)
@given(stem=_stem, ext=st.sampled_from(["js", "mjs", "css"]), tag=_hex, other=_hex)
def test_alias_round_trip_and_pattern(stem: str, ext: str, tag: str, other: str) -> None:
    name = f"{stem}.{ext}"
    alias = alias_for(name, tag)
    match = ALIAS_RE.match(alias)
    assert match and match.group("stem") == stem and match.group("runtag") == tag and match.group("ext") == ext
    pattern = reference_pattern(name)
    assert pattern.fullmatch(name) and pattern.fullmatch(alias) and pattern.fullmatch(alias_for(name, other))
    html, count = repoint_index(f'<script src="/assets/{name}"></script>', {name: alias})
    assert count == 1 and alias in html
