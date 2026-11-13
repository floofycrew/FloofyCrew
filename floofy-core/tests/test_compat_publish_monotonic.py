"""D-7 — publishing the matrix never removes a row or a version (``nightly-follow.md`` task E; Requirements 6.1, 3.3).

# Feature: public-first-pathway, D-7: publish is monotone

The owner's ask of 2026-11-02: mods and registries follow the nightly build, and
old hosts keep old versions. D-5 makes every Forge lane publish its graded rows to
the hosted ``compat.json`` per tick; D-7 is the guarantee that this publish is
*monotone* — it adds rows for hosts the registry never heard of and cells for
versions measured since, and takes nothing away — so a version a host could
install before a publish can still be installed after it, and the only way a
version leaves a row is a human's hand (``compat-merge --override`` /
:func:`registry_tools.compat_merge.override_cell`, or ``yanked`` in a release
record).

Three paths write a registry's ``compat.json``, and all three go through one
function, :func:`registry_tools.compat_merge.merge_row`:

* the Forge's per-tick matrix publish (``forge.matrixpublish._publish``):
  ``load_compat`` of the registry clone, ``merge_row`` of **every** row of the
  Forge's matrix, ``validate_compat``, write, sign, land. That module is overlay
  content (``forge/``), which a test in the public tree cannot import, so
  :func:`publish` below is that loop verbatim over the shared function — the
  merge is the only thing the module does to the document;
* the release's registry step (``forge.release``): :func:`registry_tools.seed.seed_release`
  for the archive records, then ``compat_merge`` of every public matrix row, then
  the index rebuild — the same loop plus ``seed_release``, which writes under
  ``mods/`` only and is exercised here on a scratch registry;
* a curator's ``python -m registry_tools compat-merge`` — one ``merge_row``.

**The invariant** (over a published document *S*, the Forge's matrix *M* and the
registry index *I*; *P* = ``merge_row`` of every row of *M* into *S*):

1. every row of *S* is in *P* (``row_key`` for ``row_key``) and *P* holds exactly
   the rows of *S* plus the rows of *M* for keys *S* lacked — nothing is deduped or
   lost while sorting; a row *M* does not carry is in *P* unchanged;
2. every effective cell of a row of *S* (measured cells with the human
   ``overrides[]`` applied, exactly as :class:`floofy_core.compat.CompatCache`
   reads them) is an effective cell of *P*'s row with the same verdict, and every
   ``overrides[]`` entry survives verbatim — a human's word outlives any number
   of publishes;
3. every version of *I* that :meth:`floofy_core.registry.IndexCache.best_version`
   admits on a row of *S* is still admitted on that row of *P*, with the same or a
   better verdict, and the version it ranks best on the row ranks the same or
   better afterwards (``(VERDICT_RANK, Version)`` is non-decreasing: a new cell may
   put a newer tested version on top; an old one never falls off). The one
   exception is stated, not hidden: a publish may carry the Forge's **first** word
   on a version the row had never measured, and when that word is ``broken`` the
   version is excluded from then on — Requirement 9.2's quarantine, a measurement,
   not a yank; everything the matrix said before, it keeps saying;
4. *P* is valid against ``compat.schema.json``;

and, for the release path: ``seed_release`` leaves ``compat.json`` byte for byte,
the rebuilt index lists every version it listed before plus the new records, and
(3) holds across the rebuild.

**Fixtures.** No snapshot of the hosted public ``compat.json`` exists in this
repository (the registry repository is not vendored and no test fetches anything),
so :data:`HOSTED_SNAPSHOT` is **constructed**: the one row the hosted file carried
on 2026-11-02 (``external``/``stable``/``0.6.0``, measured 2026-09-22,
``settings-demo@1.0.0`` tested — ``nightly-follow.md``) with the curator's override
the Forge suite records on it, plus rows for every other shape the schema allows:
an insider ``rc`` row, a nightly ``.dev`` row, internal rows with ``X.Y.Z.N`` hosts,
``inherited`` cells and ``run`` links, bare-string cells, an override that promotes
an unmeasured version and one that yanks a measured one. :data:`MEASURED_MATRIX` is
the Forge's external matrix in the shape ``forge/tests/test_matrixpublish.py``
seeds: the hosts ``pyproject.toml`` lists under ``[tool.floofycrew.supports.external]``
plus ``0.11.1`` and the nightly ``0.12.0.dev20261031060601`` (the facts of
2026-11-02), every row ``loader: ok``, including a re-measured ``0.6.0`` row with a
cell the hosted row never had. :data:`PUBLIC_INDEX` lists the public mods with the
range mainline declares (``>=0.7.0 <0.11.0``), so on the ``0.6.0`` host **only the
row** makes ``settings-demo@1.0.0`` installable — the case D-7 names.

**Generator domain** (the properties): an index of 1–3 mods × 1–4 versions with
ranges from a pool that admits some hosts and not others, random ``editions``, a
rare ``strict`` and a rare ``yanked``; a snapshot of 0–5 rows with unique keys over
both editions, four channels and the four host-version shapes, cells in every
allowed shape (bare verdict, ``{verdict, run}``, ``{verdict, detail}``,
``{verdict, inherited, run}``), 0–2 overrides per row on measured or unmeasured
cells; a Forge matrix of 0–4 rows for keys the snapshot lacks (arbitrary
measurements) plus a re-measurement of a random subset of the snapshot's keys. A
re-measurement carries every cell the hosted row measured with the same verdict —
the hosted row *came from* this matrix, which the Forge's ``merge_row_file`` built
with the same function — possibly reshaped (a bare verdict re-measured as
``{verdict, run}``), plus the Forge's first word on versions measured since, and
never ``overrides`` (the Forge writes none). For the release path the same index
is a registry on disk (manifest and release record per version, ``yanked`` where
the index says so), the snapshot is its ``compat.json``, and the release ships one
version per recorded mod — usually new, sometimes one already recorded — and now
and then a mod the registry never listed. Outside that domain nothing is claimed:
:func:`test_a_re_measurement_is_the_forges_word_not_a_yank` pins what the shared
function does when a re-measurement changes or omits a cell, so a change in either
direction is a deliberate one.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from floofy_core.compat import CompatCache
from floofy_core.registry import VERDICT_RANK, IndexCache
from floofy_core.semver import HostVersion, Version

from registry_tools.build import build_index
from registry_tools.compat_merge import merge_row, override_cell, row_key, validate_compat
from registry_tools.repo import RegistryRepo
from registry_tools.seed import seed_release

pytestmark = pytest.mark.registry

RowKey = tuple[str, str, str]

FLOOFYCREW = "https://github.com/floofycrew/FloofyCrew"
REGISTRY_SOURCE = "github:floofycrew/floofycrew-registry"
FORGE_RUNS = "https://forge.example/runs"
#: What every public mod on mainline declares today (``mods/*/floofy.json``, 2026-11-02); ``model-retry-policy`` admits 0.11 too.
MAINLINE_RANGE = ">=0.7.0 <0.11.0"

# --- the concrete pair -----------------------------------------------------------------------------------------------

#: The one row the hosted public ``compat.json`` carried on 2026-11-02 (``nightly-follow.md``), with a curator's override on it
#: — the same row ``forge/tests/test_matrixpublish.py`` seeds its fixture registry with.
HOSTED_ROW: dict[str, Any] = {
    "edition": "external",
    "channel": "stable",
    "hostVersion": "0.6.0",
    "framework": {"loader": "ok"},
    "mods": {"settings-demo@1.0.0": {"verdict": "tested"}},
    "checkedAt": "2026-09-22T09:00:00Z",
    "overrides": [{"cell": "settings-demo@1.0.0", "verdict": "expected", "reason": "curator: re-measured by hand", "by": "curator"}],
}

#: A published document in every shape the schema allows (constructed; see the module docstring).
HOSTED_SNAPSHOT: dict[str, Any] = {
    "schema": 1,
    "source": REGISTRY_SOURCE,
    "generatedAt": "2026-10-22T04:27:00Z",
    "rows": [
        HOSTED_ROW,
        {
            "edition": "external",
            "channel": "insider",
            "hostVersion": "0.7.0rc5",
            "framework": {"loader": "ok", "spaFingerprints": {"matched": 3, "total": 3}, "pythonAnchors": {"matched": 2, "total": 2}, "shimVersion": "1.0.3"},
            "mods": {"settings-demo@1.0.0": "tested", "custom-themes@1.0.2": {"verdict": "expected", "detail": "fingerprints matched; not exercised"}},
            "run": f"{FORGE_RUNS}/public-insider/0.7.0rc5",
            "checkedAt": "2026-09-21T05:36:10Z",
        },
        {
            "edition": "external",
            "channel": "nightly",
            "hostVersion": "0.11.0.dev20261019030000",
            "framework": {"loader": "ok"},
            "mods": {"settings-demo@1.0.0": {"verdict": "broken", "run": f"{FORGE_RUNS}/public-nightly/0.11.0.dev20261019030000"}, "model-retry-policy@0.2.1": {"verdict": "broken", "run": f"{FORGE_RUNS}/public-nightly/0.11.0.dev20261019030000"}},
            "checkedAt": "2026-10-19T07:30:00Z",
            "overrides": [{"cell": "settings-demo@1.0.1", "verdict": "expected", "reason": "curator: 1.0.1 carries the fix for the mode ids a 0.11 host renamed", "by": "curator", "at": "2026-10-20T07:00:00Z"}],
        },
        {
            "edition": "internal",
            "channel": "beta",
            "hostVersion": "0.10.0.2",
            "framework": {"loader": "ok", "shimVersion": "1.0.3", "floofycrew": "1.3.10"},
            "mods": {
                "settings-demo@1.0.0": {"verdict": "tested", "inherited": "public-stable 0.10.0", "run": f"{FORGE_RUNS}/public-stable/0.10.0"},
                "display-comfort@1.0.1": {"verdict": "tested", "run": f"{FORGE_RUNS}/internal-beta/0.10.0.2"},
            },
            "checkedAt": "2026-10-22T04:27:00Z",
        },
        {
            "edition": "internal",
            "channel": "stable",
            "hostVersion": "0.9.0.4",
            "framework": {"loader": "degraded", "spaFingerprints": {"matched": 2, "total": 3, "missed": ["sidebar-tabs"]}},
            "mods": {"settings-demo@1.0.0": "tested", "display-comfort@1.0.1": {"verdict": "tested"}, "custom-themes@1.0.2": {"verdict": "tested"}},
            "checkedAt": "2026-10-01T09:00:00Z",
            "overrides": [{"cell": "custom-themes@1.0.2", "verdict": "broken", "reason": "curator: the baked theme CSS misses the 0.9 sidebar; withdrawn on this host", "by": "curator", "at": "2026-10-02T09:00:00Z"}],
        },
    ],
}


@dataclasses.dataclass(frozen=True)
class ModSpec:
    """One listed version, as both renderers need it: :func:`index_document` (in memory) and :func:`write_registry` (records on disk)."""

    mod_id: str
    version: str
    kirocrew: str = MAINLINE_RANGE
    editions: tuple[str, ...] = ("internal", "external")
    strict: bool = False
    yanked: str | None = None

    @property
    def cell(self) -> str:
        return f"{self.mod_id}@{self.version}"

    def manifest(self) -> dict[str, Any]:
        """The ``floofy.json`` a registry record (or a release's ``mods/<id>/``) carries for this version."""
        host: dict[str, Any] = {"version": self.kirocrew, "editions": list(self.editions)}
        if self.strict:
            host["strict"] = True
        return {"schema": 1, "id": self.mod_id, "name": self.mod_id, "version": self.version, "description": f"{self.mod_id} {self.version}", "authors": ["floofycrew"], "tags": [], "kirocrew": host, "parts": []}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _archive_url(spec: ModSpec) -> str:
    """Where the public registry's ``archiveUrlTemplate`` puts a release asset: ``…/releases/download/<id>-<version>/<id>-<version>.zip``."""
    return f"{FLOOFYCREW}/releases/download/{spec.mod_id}-{spec.version}/{spec.mod_id}-{spec.version}.zip"


def index_document(specs: Iterable[ModSpec], *, source: str = "test:registry", **extra: Any) -> dict[str, Any]:
    """An ``index.json`` listing ``specs`` — the shape ``registry_tools build`` writes, with empty inline ``compat`` cells (the client grades from the rows)."""
    mods: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        entry: dict[str, Any] = {
            "version": spec.version,
            "kirocrew": spec.kirocrew,
            "editions": list(spec.editions),
            "compat": {},
            "files": [{"url": _archive_url(spec), "sha256": _sha(spec.cell), "size": 1}],
            "dependencies": {},
            "channel": "stable",
            "publishedAt": "2026-09-19T08:00:00Z",
        }
        if spec.strict:
            entry["strict"] = True
        if spec.yanked:
            entry["yanked"] = spec.yanked
        mods.setdefault(spec.mod_id, []).append(entry)
    listed = [{"id": mod_id, "name": mod_id, "description": "", "authors": ["floofycrew"], "tags": [], "repo": FLOOFYCREW, "versions": versions} for mod_id, versions in mods.items()]
    return {"schema": 1, "source": source, "generatedAt": "2026-10-22T04:27:00Z", **extra, "mods": listed}


#: The public mods as mainline lists them: every range is the forecast written when 0.11 did not exist; one version withdrawn by hand.
PUBLIC_SPECS: tuple[ModSpec, ...] = (
    ModSpec("settings-demo", "1.0.0"),
    ModSpec("settings-demo", "1.0.1"),
    ModSpec("custom-themes", "1.0.2"),
    ModSpec("custom-themes", "1.0.3"),
    ModSpec("display-comfort", "1.0.0", yanked="1.0.1 supersedes it: the zoom handler leaked a listener"),
    ModSpec("display-comfort", "1.0.1"),
    ModSpec("model-retry-policy", "0.2.1"),
    ModSpec("model-retry-policy", "0.2.2", ">=0.7.0 <0.12.0"),
    ModSpec("kirocrew-ide", "0.23.3"),
)
PUBLIC_INDEX: dict[str, Any] = index_document(PUBLIC_SPECS, source=REGISTRY_SOURCE, floofycrew="1.3.10")

#: The cells the Forge measures on a current host: every public mod at its mainline version.
CURRENT_SET = {"settings-demo@1.0.1": "tested", "custom-themes@1.0.3": "tested", "display-comfort@1.0.1": "tested", "model-retry-policy@0.2.2": "tested", "kirocrew-ide@0.23.3": "tested"}


def _measured(channel: str, host: str, cells: Mapping[str, str], checked_at: str) -> dict[str, Any]:
    """One row of the Forge's external matrix: object cells with the run link, ``loader: ok``, no ``overrides``."""
    run = f"{FORGE_RUNS}/public-{channel}/{host}"
    framework = {"loader": "ok", "spaFingerprints": {"matched": 3, "total": 3}, "pythonAnchors": {"matched": 2, "total": 2}, "shimVersion": "1.0.3", "floofycrew": "1.3.11"}
    return {"edition": "external", "channel": channel, "hostVersion": host, "framework": framework, "mods": {cell: {"verdict": verdict, "run": run} for cell, verdict in cells.items()}, "run": run, "checkedAt": checked_at}


#: ``~/forge/matrix/external/compat.json`` as measured by 2026-11-02: the ``supports.external`` hosts, 0.11.1 and the nightly.
MEASURED_MATRIX: list[dict[str, Any]] = [
    # the hosted 0.6.0 row again — its one cell, plus a mod the registry never saw on this host (the override must survive)
    _measured("stable", "0.6.0", {"settings-demo@1.0.0": "tested", "kirocrew-ide@0.23.3": "tested"}, "2026-11-01T07:10:00Z"),
    *(_measured("stable", host, CURRENT_SET, f"2026-11-01T07:{minute:02d}:00Z") for minute, host in enumerate(("0.7.0", "0.7.1", "0.7.2", "0.9.0", "0.11.1"), start=11)),
    # the hosted insider row again — same cells, same verdicts, one more mod
    _measured("insider", "0.7.0rc5", {"settings-demo@1.0.0": "tested", "custom-themes@1.0.2": "expected", "kirocrew-ide@0.23.3": "tested"}, "2026-11-01T07:20:00Z"),
    _measured("insider", "0.8.0rc1", CURRENT_SET, "2026-11-01T07:21:00Z"),
    _measured("insider", "0.10.0rc4", CURRENT_SET, "2026-11-01T07:22:00Z"),
    # the hosted nightly row again — its broken cells kept, the repaired versions measured since
    _measured("nightly", "0.11.0.dev20261019030000", {"settings-demo@1.0.0": "broken", "model-retry-policy@0.2.1": "broken", "settings-demo@1.0.1": "tested", "model-retry-policy@0.2.2": "tested"}, "2026-11-01T07:30:00Z"),
    _measured("nightly", "0.12.0.dev20261031060601", CURRENT_SET, "2026-11-01T07:31:00Z"),
]


# --- the publish paths, as the public tree can reach them --------------------------------------------------------------


def publish(document: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The merge of ``forge.matrixpublish._publish`` (and of the release's registry step), over the shared function.

    ``_publish`` does ``load_compat`` of the clone, then exactly this: ``merge_row`` of every Forge row in
    matrix order, ``validate_compat`` of the result — then writes, signs and lands the document unchanged.
    ``merge_row`` returns a new document each time; the inputs are never mutated.
    """
    merged: dict[str, Any] = dict(document)
    for row in rows:
        merged = merge_row(merged, dict(row))
    problems = validate_compat(merged)
    assert not problems, problems
    return merged


def row_keys(document: Mapping[str, Any]) -> set[RowKey]:
    return {row_key(row) for row in document["rows"]}


def rows_by_key(document: Mapping[str, Any]) -> dict[RowKey, dict[str, Any]]:
    return {row_key(row): row for row in document["rows"]}


def client_view(document: Mapping[str, Any]) -> CompatCache:
    """The document as a client reads it (``floofy registry refresh`` writes it to ``cache/compat.json``; the Loader and the CLI grade from it)."""
    cache = CompatCache.from_dict(dict(document))
    assert not cache.notes, cache.notes
    return cache


def effective_cells(compat: CompatCache) -> dict[RowKey, dict[str, str]]:
    """``mod@version → verdict`` per row: the measured cells with the human ``overrides[]`` applied."""
    return {(row.edition, row.channel, row.host_version): dict(row.mods) for row in compat.rows}


def index_of(document: Mapping[str, Any]) -> IndexCache:
    index = IndexCache.from_documents([("registry", dict(document))])
    assert not index.notes, index.notes
    return index


def _ask(index: IndexCache, compat: CompatCache, row: Mapping[str, Any], ref: str):
    host = HostVersion.parse(str(row["hostVersion"]))
    return index.best_version(ref, base_version=host.base, edition=str(row["edition"]), host_version=str(row["hostVersion"]), channel=str(row["channel"]), compat=compat)


def best_on(index: IndexCache, compat: CompatCache, row: Mapping[str, Any]) -> dict[str, tuple[int, Version, str] | None]:
    """Per mod of the index: ``(VERDICT_RANK, parsed version, version text)`` of the version ``best_version`` picks on this row, or ``None``."""
    graded: dict[str, tuple[int, Version, str] | None] = {}
    for entry in index.mods:
        best = _ask(index, compat, row, entry.key)
        graded[entry.key] = None if best.version is None else (VERDICT_RANK.get(best.verdict, 1), best.version.parsed, best.version.version)
    return graded


def admitted(index: IndexCache, compat: CompatCache, row: Mapping[str, Any], ref: str, version: str) -> tuple[int, str | None] | None:
    """Whether ``ref@version`` is installable on this row — graded rather than excluded by ``best_version`` — and with which verdict.

    Asked of an index that lists only that version, so the answer is about the version itself, not about a newer
    sibling outranking it: ``(VERDICT_RANK, verdict)`` when it is admitted, ``None`` when it is excluded.
    """
    entry = index.find(ref)
    assert entry is not None, ref
    only = IndexCache(mods=[dataclasses.replace(entry, versions=[v for v in entry.versions if v.version == version])], source=index.source)
    best = _ask(only, compat, row, entry.key)
    return None if best.version is None else (VERDICT_RANK.get(best.verdict, 1), best.verdict)


def assert_publish_is_monotone(index: IndexCache, before: Mapping[str, Any], after: Mapping[str, Any], matrix: Iterable[Mapping[str, Any]]) -> None:
    """The D-7 invariant between a published document and the same document after a matrix publish (module docstring, 1–4)."""
    matrix_rows = [dict(row) for row in matrix]
    matrix_keys = {row_key(row) for row in matrix_rows}
    keys_before, keys_after = row_keys(before), row_keys(after)
    # 1. rows: every old row is there, every new key is there, nothing else, no duplicate keys; untouched rows are untouched
    assert keys_before <= keys_after, f"rows removed: {sorted(keys_before - keys_after)}"
    assert keys_after == keys_before | matrix_keys, f"rows: expected {sorted(keys_before | matrix_keys)}, got {sorted(keys_after)}"
    assert len(after["rows"]) == len(keys_after), "a key appears twice after the publish"
    rows_before, rows_after = rows_by_key(before), rows_by_key(after)
    for key in keys_before - matrix_keys:
        assert rows_after[key] == rows_before[key], f"row {key} was not in the matrix yet changed"
    # 2. cells and overrides
    view_before, view_after = client_view(before), client_view(after)
    cells_before, cells_after = effective_cells(view_before), effective_cells(view_after)
    for key, row in rows_before.items():
        missing = {cell: verdict for cell, verdict in cells_before[key].items() if cells_after[key].get(cell) != verdict}
        assert not missing, f"row {key}: effective cells lost or changed by the publish: {missing}"
        kept = rows_after[key].get("overrides") or []
        for human in row.get("overrides") or []:
            assert human in kept, f"row {key}: the human override {human} did not survive the publish"
    # 3. every admitted version stays admitted (same or better verdict) and the best version ranks the same or better,
    #    except a version the row had never measured that the Forge's first word on it grades broken (Requirement 9.2)
    for key, row in rows_before.items():
        first_word_broken: set[tuple[str, str]] = set()
        for entry in index.mods:
            for version in entry.versions:
                cell = f"{entry.id}@{version.version}"
                was, now = admitted(index, view_before, row, entry.key, version.version), admitted(index, view_after, row, entry.key, version.version)
                if was is None:
                    continue
                if cell not in cells_before[key] and cells_after[key].get(cell) == "broken":
                    first_word_broken.add((entry.key, version.version))
                    assert now is None, f"row {key}: {cell} measured broken for the first time, yet still admitted"
                    continue
                assert now is not None, f"row {key}: {cell} was installable ({was[1]}) and is excluded after the publish"
                assert now[0] >= was[0], f"row {key}: {cell} graded {was[1]} before, {now[1]} after"
        best_before, best_after = best_on(index, view_before, row), best_on(index, view_after, row)
        for ref, best in best_before.items():
            if best is None or (ref, best[2]) in first_word_broken:
                continue
            assert best_after[ref] is not None, f"row {key}: {ref} had a best version ({best[2]}) and has none after the publish"
            assert best_after[ref][:2] >= best[:2], f"row {key}: {ref} best was {best[2]} ({best[0]}), now {best_after[ref][2]} ({best_after[ref][0]})"
    # 4. the document is still a compat.json
    assert validate_compat(dict(after)) == []


# --- generators (module docstring, "Generator domain") -----------------------------------------------------------------

EDITIONS = ("internal", "external")
CHANNELS = ("stable", "beta", "insider", "nightly")
VERDICTS = ("tested", "expected", "broken")
LOADERS = ("ok", "degraded", "broken")
MOD_IDS = ("alpha-mod", "beta-mod", "gamma-mod")
#: Forecast ranges: each admits some of the generated hosts (``0.5`` … ``0.12``) and not others.
RANGES = ("*", MAINLINE_RANGE, ">=0.6.0 <0.8.0", ">=0.9.0", "^0.8.0", "<0.7.0", ">=0.7.0 <0.12.0")
STAMPS = ("2026-09-22T09:00:00Z", "2026-10-01T09:00:00Z", "2026-10-19T07:30:00Z", "2026-11-01T07:10:00Z", "2026-11-02T07:10:00.5Z")
#: What a ``framework`` block may carry besides ``loader``: fingerprint and anchor counts (with a miss), the shim version, the release stamp.
FRAMEWORK_EXTRAS: tuple[dict[str, Any], ...] = (
    {},
    {"spaFingerprints": {"matched": 3, "total": 3}},
    {"spaFingerprints": {"matched": 2, "total": 3, "missed": ["sidebar-tabs"]}, "pythonAnchors": {"matched": 1, "total": 1}, "shimVersion": "1.0.3"},
    {"shimVersion": None, "floofycrew": "1.3.11"},
)

mod_versions = st.builds("{}.{}.{}".format, st.integers(0, 2), st.integers(0, 2), st.integers(0, 2))
frameworks = st.builds(lambda loader, extra: {"loader": loader, **extra}, st.sampled_from(LOADERS), st.sampled_from(FRAMEWORK_EXTRAS))


@st.composite
def host_versions(draw: st.DrawFn) -> str:
    """The four shapes a host prints: ``X.Y.Z``, ``X.Y.Z.N`` (internal build), ``X.Y.ZrcN`` (insider), ``X.Y.Z.devN`` (nightly)."""
    core = f"0.{draw(st.integers(5, 12))}.{draw(st.integers(0, 2))}"
    shape, number = draw(st.sampled_from(("final", "build", "rc", "dev"))), draw(st.integers(1, 9))
    return {"final": core, "build": f"{core}.{number}", "rc": f"{core}rc{number}", "dev": f"{core}.dev{number}"}[shape]


keys_st = st.tuples(st.sampled_from(EDITIONS), st.sampled_from(CHANNELS), host_versions())


@st.composite
def mod_specs(draw: st.DrawFn) -> tuple[ModSpec, ...]:
    specs: list[ModSpec] = []
    for mod_id in draw(st.lists(st.sampled_from(MOD_IDS), min_size=1, max_size=3, unique=True)):
        for version in draw(st.lists(mod_versions, min_size=1, max_size=4, unique=True)):
            editions = draw(st.sampled_from((("internal", "external"), ("internal",), ("external",))))
            strict, yanked = draw(st.integers(0, 9)) == 0, ("withdrawn by hand" if draw(st.integers(0, 9)) == 0 else None)
            specs.append(ModSpec(mod_id, version, draw(st.sampled_from(RANGES)), editions, strict, yanked))
    return tuple(specs)


def cell_names(specs: Iterable[ModSpec]) -> st.SearchStrategy[str]:
    """Mostly listed versions; now and then a version the index does not list (the matrix may grade a mod before its record exists)."""
    listed = sorted({spec.cell for spec in specs})
    return st.one_of(st.sampled_from(listed), st.sampled_from(listed), st.builds("{}@{}".format, st.sampled_from(MOD_IDS), mod_versions))


@st.composite
def cell_values(draw: st.DrawFn, verdict: str, internal: bool) -> str | dict[str, Any]:
    """Every cell shape the schema allows; ``inherited`` only on internal rows (the delta check copies a twin's verdict)."""
    shape = draw(st.sampled_from(("bare", "object", "run", "detail") + (("inherited",) if internal else ())))
    if shape == "bare":
        return verdict
    cell: dict[str, Any] = {"verdict": verdict}
    if shape == "run":
        cell["run"] = f"{FORGE_RUNS}/{draw(st.integers(1, 99))}"
    elif shape == "detail":
        cell["detail"] = "re-measured"
    elif shape == "inherited":
        cell.update(inherited="public-stable 0.10.0", run=f"{FORGE_RUNS}/twin")
    return cell


@st.composite
def human_overrides(draw: st.DrawFn, specs: Iterable[ModSpec]) -> dict[str, Any]:
    entry = {"cell": draw(cell_names(specs)), "verdict": draw(st.sampled_from(VERDICTS)), "reason": "curator: by hand", "by": "curator"}
    if draw(st.booleans()):
        entry["at"] = draw(st.sampled_from(STAMPS))
    return entry


@st.composite
def rows_for(draw: st.DrawFn, key: RowKey, specs: tuple[ModSpec, ...], *, human: bool) -> dict[str, Any]:
    """A row for ``key``; a ``human`` row (the published document's) may carry overrides, a Forge row never does."""
    edition, channel, host = key
    mods = {name: draw(cell_values(draw(st.sampled_from(VERDICTS)), edition == "internal")) for name in draw(st.lists(cell_names(specs), max_size=5, unique=True))}
    row: dict[str, Any] = {"edition": edition, "channel": channel, "hostVersion": host, "framework": draw(frameworks), "mods": mods}
    if draw(st.booleans()):
        row["run"] = f"{FORGE_RUNS}/{edition}-{channel}/{host}"
    if draw(st.booleans()):
        row["checkedAt"] = draw(st.sampled_from(STAMPS))
    if human:
        overrides = draw(st.lists(human_overrides(specs), max_size=2, unique_by=lambda o: o["cell"]))
        if overrides:
            row["overrides"] = overrides
    return row


@st.composite
def re_measurements(draw: st.DrawFn, row: Mapping[str, Any], specs: tuple[ModSpec, ...]) -> dict[str, Any]:
    """The Forge's current row for a key the document has: every measured cell again with the same verdict (any shape), its first word on versions measured since, no overrides."""
    edition, channel, host = row_key(dict(row))
    measured = CompatCache.from_dict({"schema": 1, "rows": [{k: v for k, v in row.items() if k != "overrides"}]}).rows[0].mods
    mods = {name: draw(cell_values(verdict, edition == "internal")) for name, verdict in measured.items()}
    for name in draw(st.lists(cell_names(specs), max_size=3, unique=True)):
        if name not in mods:
            mods[name] = draw(cell_values(draw(st.sampled_from(VERDICTS)), edition == "internal"))
    fresh: dict[str, Any] = {"edition": edition, "channel": channel, "hostVersion": host, "framework": draw(frameworks), "mods": mods, "checkedAt": draw(st.sampled_from(STAMPS))}
    if draw(st.booleans()):
        fresh["run"] = f"{FORGE_RUNS}/{edition}-{channel}/{host}/again"
    return fresh


@dataclasses.dataclass(frozen=True)
class World:
    """A matrix publish: the registry's index, the published ``compat.json`` and the Forge's matrix rows."""

    index: dict[str, Any]
    snapshot: dict[str, Any]
    matrix: tuple[dict[str, Any], ...]


@st.composite
def worlds(draw: st.DrawFn) -> World:
    specs = draw(mod_specs())
    keys = draw(st.lists(keys_st, max_size=5, unique=True))
    rows = [draw(rows_for(key, specs, human=True)) for key in keys]
    taken = set(keys)
    matrix = [draw(rows_for(key, specs, human=False)) for key in draw(st.lists(keys_st.filter(lambda k: k not in taken), max_size=4, unique=True))]
    matrix += [draw(re_measurements(row, specs)) for row in rows if draw(st.booleans())]
    return World(index_document(specs), {"schema": 1, "source": "test:registry", "generatedAt": STAMPS[0], "rows": rows}, tuple(matrix))


@dataclasses.dataclass(frozen=True)
class Release:
    """A release's registry step: the versions already recorded, the published ``compat.json``, the versions the release's ``mods/`` carries."""

    recorded: tuple[ModSpec, ...]
    snapshot: dict[str, Any]
    shipped: tuple[ModSpec, ...]


@st.composite
def releases(draw: st.DrawFn) -> Release:
    recorded = draw(mod_specs())
    keys = draw(st.lists(keys_st, max_size=4, unique=True))
    rows = [draw(rows_for(key, recorded, human=True)) for key in keys]
    shipped: list[ModSpec] = []
    for mod_id in sorted({spec.mod_id for spec in recorded} | ({draw(st.sampled_from(MOD_IDS))} if draw(st.booleans()) else set())):
        # the release's version of each mod: usually new, sometimes one already recorded (the record is kept, nothing is rewritten)
        shipped.append(ModSpec(mod_id, draw(mod_versions), draw(st.sampled_from(RANGES)), draw(st.sampled_from((("internal", "external"), ("external",))))))
    return Release(recorded, {"schema": 1, "source": "test:registry", "generatedAt": STAMPS[0], "rows": rows}, tuple(shipped))


CONCRETE_WORLD = World(PUBLIC_INDEX, HOSTED_SNAPSHOT, tuple(MEASURED_MATRIX))
#: What ``v1.3.12`` ships: a settings-demo fix, model-retry-policy widened to 0.12, custom-themes unchanged (already recorded).
CONCRETE_RELEASE = Release(PUBLIC_SPECS, HOSTED_SNAPSHOT, (ModSpec("settings-demo", "1.0.2"), ModSpec("custom-themes", "1.0.3"), ModSpec("model-retry-policy", "0.2.3", ">=0.7.0 <0.13.0")))


# --- the release path on a scratch registry ------------------------------------------------------------------------------


def write_registry(root: Path, recorded: Iterable[ModSpec], snapshot: Mapping[str, Any]) -> RegistryRepo:
    """A registry repository (the ``registry_tools.repo`` layout): ``recorded`` as archive records, ``snapshot`` as its ``compat.json``."""
    root.mkdir(parents=True)
    config = {"schema": 1, "source": "test:registry", "recordKind": "archive", "archiveUrlTemplate": f"{FLOOFYCREW}/releases/download/{{id}}-{{version}}/{{file}}", "floofycrew": FLOOFYCREW}
    (root / "registry.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
    for spec in recorded:
        directory = root / "mods" / spec.mod_id / spec.version
        directory.mkdir(parents=True)
        (directory.parent / "mod.json").write_text(json.dumps({"repo": FLOOFYCREW, "contact": "floofycrew"}) + "\n", encoding="utf-8")
        (directory / "floofy.json").write_text(json.dumps(spec.manifest(), indent=2) + "\n", encoding="utf-8")
        asset = {"url": _archive_url(spec), "sha256": _sha(spec.cell), "size": 1, "name": f"{spec.mod_id}-{spec.version}.zip"}
        release: dict[str, Any] = {"tag": f"{spec.mod_id}-{spec.version}", "channel": "stable", "publishedAt": "2026-09-19T08:00:00Z", "files": [asset]}
        if spec.yanked:
            release["yanked"] = spec.yanked
        (directory / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    (root / "compat.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return RegistryRepo.load(root)


def write_export(root: Path, shipped: Iterable[ModSpec]) -> Path:
    """The release's public tree as ``seed_release`` reads it: ``mods/<id>/floofy.json`` plus a file to archive."""
    (root / "mods").mkdir(parents=True)
    (root / "mods" / "README.md").write_text("# mods\n", encoding="utf-8")
    for spec in shipped:
        mod_dir = root / "mods" / spec.mod_id
        mod_dir.mkdir()
        (mod_dir / "floofy.json").write_text(json.dumps(spec.manifest(), indent=2) + "\n", encoding="utf-8")
        (mod_dir / "README.md").write_text(f"# {spec.mod_id} {spec.version}\n", encoding="utf-8")
    return root


def built(repo: RegistryRepo) -> dict[str, Any]:
    result = build_index(repo)
    assert result.ok, result.problems
    return result.index


def listed(index: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {(mod["id"], version["version"]) for mod in index["mods"] for version in mod["versions"]}


def assert_release_is_monotone(release: Release, scratch: Path) -> None:
    """The release's registry step (design step 7) on a scratch registry: records are added, nothing is removed, ``compat.json`` is untouched."""
    repo = write_registry(scratch / "registry", release.recorded, release.snapshot)
    export = write_export(scratch / "FloofyCrew", release.shipped)
    compat_bytes = repo.compat_path.read_bytes()
    before = built(repo)
    results = seed_release(export, registry=repo)
    assert repo.compat_path.read_bytes() == compat_bytes, "seed_release wrote to compat.json"
    after = built(repo)
    recorded = {(spec.mod_id, spec.version) for spec in release.recorded}
    shipped = {(spec.mod_id, spec.version) for spec in release.shipped}
    assert {(r["id"], r["version"]) for r in results if r.get("skipped")} == shipped & recorded, "an existing record is kept, not rewritten"
    assert all(r["skipped"] == "record exists" for r in results if r.get("skipped"))
    assert listed(before) == recorded
    assert listed(after) == recorded | shipped, "the rebuilt index lists every version it listed before plus the release's records"
    index_before, index_after, view = index_of(before), index_of(after), client_view(release.snapshot)
    for row in release.snapshot["rows"]:
        for entry in index_before.mods:
            for version in entry.versions:
                was, now = admitted(index_before, view, row, entry.key, version.version), admitted(index_after, view, row, entry.key, version.version)
                assert was == now, f"row {row_key(row)}: {entry.key}@{version.version} graded {was} before the release, {now} after"
        best_before, best_after = best_on(index_before, view, row), best_on(index_after, view, row)
        for ref, best in best_before.items():
            if best is None:
                continue
            assert best_after[ref] is not None and best_after[ref][:2] >= best[:2], f"row {row_key(row)}: {ref} best was {best[2]}, now {best_after[ref]}"


# --- the properties ---------------------------------------------------------------------------------------------------


# Feature: public-first-pathway, D-7: publish is monotone
@settings(max_examples=100, deadline=None)
@example(CONCRETE_WORLD)
@given(worlds())
def test_property_matrix_publish_never_removes_a_row_or_a_version(world: World) -> None:
    """For any published document and any Forge matrix in the domain, ``merge_row`` of every matrix row — the matrix publish,
    the release's compat step and a curator's ``compat-merge`` alike — keeps every row, every effective cell and every human
    override, keeps every admitted version admitted with the same or a better verdict (the stated first-word-broken exception
    aside), never lowers a row's best version, and yields a valid document; publishing the same matrix again changes nothing."""
    assert validate_compat(world.snapshot) == [], "the generator must produce a valid published document"
    index = index_of(world.index)
    after = publish(world.snapshot, world.matrix)
    assert_publish_is_monotone(index, world.snapshot, after, world.matrix)
    assert publish(after, world.matrix)["rows"] == after["rows"], "the next tick with an unchanged matrix publishes the same rows"


# Feature: public-first-pathway, D-7: publish is monotone
@settings(max_examples=100, deadline=None)
@example(CONCRETE_RELEASE)
@given(releases())
def test_property_release_records_add_versions_and_leave_the_matrix_alone(release: Release) -> None:
    """For any recorded registry, published matrix and release tree, ``seed_release`` adds exactly the release's unrecorded versions,
    rewrites no record, leaves ``compat.json`` byte for byte, and the rebuilt index grades every version it already listed exactly as
    before on every row while the row's best version ranks the same or better."""
    with tempfile.TemporaryDirectory(prefix="floofy-d7-") as scratch:
        assert_release_is_monotone(release, Path(scratch))


# --- the concrete pair, spelled out --------------------------------------------------------------------------------------


def test_hosted_snapshot_survives_the_measured_matrix() -> None:
    """The hosted document after the Forge's external matrix lands (D-5): the 0.6.0 host keeps settings-demo@1.0.0 by its row alone,
    the curator's word on it outlives the Forge's re-measurement, the new hosts gain rows, the internal rows are untouched, and a
    version leaves a row only when a human says so — which the next publish respects."""
    index = index_of(PUBLIC_INDEX)
    before = copy.deepcopy(HOSTED_SNAPSHOT)
    after = publish(before, MEASURED_MATRIX)
    assert_publish_is_monotone(index, before, after, MEASURED_MATRIX)
    assert len(after["rows"]) == len(HOSTED_SNAPSHOT["rows"]) + 8, "the matrix adds the eight hosts the registry never heard of"
    view_before, view_after = client_view(before), client_view(after)
    stable_060 = rows_by_key(before)[("external", "stable", "0.6.0")]
    # the forecast (>=0.7.0) excludes 0.6.0; the row admits 1.0.0 and the curator's `expected` outranks the Forge's `tested`
    assert admitted(index, view_before, stable_060, "settings-demo", "1.0.0") == admitted(index, view_after, stable_060, "settings-demo", "1.0.0") == (2, "expected")
    assert admitted(index, view_after, stable_060, "settings-demo", "1.0.1") is None, "a version without a cell on 0.6.0 stays excluded by its range"
    assert {ref: best and best[2] for ref, best in best_on(index, view_before, stable_060).items()} == {"settings-demo": "1.0.0", "custom-themes": None, "display-comfort": None, "model-retry-policy": None, "kirocrew-ide": None}
    assert {ref: best and best[2] for ref, best in best_on(index, view_after, stable_060).items()} == {"settings-demo": "1.0.0", "custom-themes": None, "display-comfort": None, "model-retry-policy": None, "kirocrew-ide": "0.23.3"}
    assert rows_by_key(after)[("external", "stable", "0.6.0")]["overrides"] == HOSTED_ROW["overrides"]
    # the host the stale forecast refused (nightly-follow.md): every mod tested, within the tick, with no release
    stable_0111 = rows_by_key(after)[("external", "stable", "0.11.1")]
    every_mod_tested = {"settings-demo": (3, "1.0.1"), "custom-themes": (3, "1.0.3"), "display-comfort": (3, "1.0.1"), "model-retry-policy": (3, "0.2.2"), "kirocrew-ide": (3, "0.23.3")}
    assert {ref: (best[0], best[2]) for ref, best in best_on(index, view_after, stable_0111).items()} == every_mod_tested
    # the external matrix leaves the internal rows alone; the curator's yank of custom-themes@1.0.2 on 0.9.0.4 stands, as does the yanked index record
    internal_0904 = rows_by_key(before)[("internal", "stable", "0.9.0.4")]
    assert rows_by_key(after)[("internal", "stable", "0.9.0.4")] == internal_0904 and rows_by_key(after)[("internal", "beta", "0.10.0.2")] == rows_by_key(before)[("internal", "beta", "0.10.0.2")]
    assert admitted(index, view_after, internal_0904, "custom-themes", "1.0.2") is None and admitted(index, view_after, internal_0904, "display-comfort", "1.0.0") is None
    # yanking is a human act: an override withdraws a version from a host, and the Forge's next publish keeps the human's word
    yanked = override_cell(after, edition="external", channel="stable", host_version="0.6.0", cell="settings-demo@1.0.0", verdict="broken", reason="curator: withdrawn on 0.6.0", by="curator")
    assert best_on(index, client_view(yanked), stable_060)["settings-demo"] is None
    republished = publish(yanked, MEASURED_MATRIX)
    assert best_on(index, client_view(republished), stable_060)["settings-demo"] is None
    assert [(o["cell"], o["verdict"]) for o in rows_by_key(republished)[("external", "stable", "0.6.0")]["overrides"]] == [("settings-demo@1.0.0", "broken")]
    assert row_keys(republished) == row_keys(after)


def test_a_re_measurement_is_the_forges_word_not_a_yank() -> None:
    """The premise the domain rests on, pinned: ``merge_row`` *replaces* a same-key row. A re-measurement that grades a cell
    ``broken`` keeps the cell and quarantines the version (Requirement 9.2 — a verdict, not a removal); a re-measurement that
    omits a cell drops it from the document — so the Forge's matrix, built by the same function, is what must carry every cell it
    ever measured for a host, and the generator's re-measurements do."""
    index = index_of(PUBLIC_INDEX)
    key = ("external", "insider", "0.7.0rc5")
    row = rows_by_key(HOSTED_SNAPSHOT)[key]
    hosted = client_view(HOSTED_SNAPSHOT)
    assert best_on(index, hosted, row)["settings-demo"][2] == "1.0.0", "tested 1.0.0 outranks the unmeasured 1.0.1"
    assert best_on(index, hosted, row)["custom-themes"][2] == "1.0.2", "expected 1.0.2 outranks the unmeasured 1.0.3"
    run = f"{FORGE_RUNS}/public-insider/0.7.0rc5/again"
    worse = _measured("insider", "0.7.0rc5", {"settings-demo@1.0.0": "broken", "custom-themes@1.0.2": "expected"}, "2026-11-01T07:20:00Z")
    after = publish(HOSTED_SNAPSHOT, [worse])
    assert row_keys(after) == row_keys(HOSTED_SNAPSHOT) and len(after["rows"]) == len(HOSTED_SNAPSHOT["rows"])
    assert set(rows_by_key(after)[key]["mods"]) == set(row["mods"]), "the cell stays; its verdict is the Forge's current word"
    assert admitted(index, client_view(after), row, "settings-demo", "1.0.0") is None and best_on(index, client_view(after), row)["settings-demo"][2] == "1.0.1"
    fewer = {**worse, "mods": {"settings-demo@1.0.0": {"verdict": "tested", "run": run}}}
    after = publish(HOSTED_SNAPSHOT, [fewer])
    assert "custom-themes@1.0.2" not in rows_by_key(after)[key]["mods"], "replace, not union: an omitted cell is gone"
    assert admitted(index, client_view(after), row, "custom-themes", "1.0.2") == (1, None), "still installable by its forecast, no longer measured"
    assert best_on(index, client_view(after), row)["custom-themes"][2] == "1.0.3", "two unmeasured versions: the newest wins"
    assert row_keys(after) == row_keys(HOSTED_SNAPSHOT), "even then no row is removed"
