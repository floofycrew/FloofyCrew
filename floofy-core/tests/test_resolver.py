"""Resolver tests (Requirement 1.3, 1.4, 1.5, 3.5; task 2.4).

Random DAGs of ``dependsOn`` / ``loadAfter`` / ``loadBefore`` edges produce an order
that respects every edge; every mod is either in the order or carries a reason;
cycles disable exactly their participants; duplicates keep the highest version;
``strict`` decides between ``Unsupported`` and a warning; conflicts are mutual;
the result is deterministic.
"""
from __future__ import annotations

import json
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.resolver import Decision, HostFacts, ModRecord, Reason, Resolution, resolve
from floofy_core.semver import Version

HOST = HostFacts("0.7.0", "internal", "beta", "1.2.0")
IDS = [f"m{i}" for i in range(8)]


def mod(mod_id: str, version: str = "1.0.0", *, deps: dict | None = None, enabled: bool = True, files_ok: bool = True, quarantined: bool = False, **extra: Any) -> ModRecord:
    manifest = {
        "id": mod_id,
        "version": version,
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": "^1.0", **(deps or {})},
        **extra,
    }
    return ModRecord(mod_id, version, manifest, enabled=enabled, files_ok=files_ok, quarantined=quarantined)


def reasons(resolution: Resolution) -> dict[str, str | None]:
    return {mod_id: (d.reason.value if d.reason else None) for mod_id, d in resolution.decisions.items()}


# --- unit: the documented rules --------------------------------------------------------------


def test_reason_enum_is_exactly_requirement_3_5() -> None:
    assert [r.value for r in Reason] == ["Error", "Duplicate", "Conflict", "Dependency", "Released", "Feature", "Unsupported", "MissingFiles", "Quarantined", "UserDisabled"]
    assert "Governance" not in Reason.__members__  # governance is a warning, never a reason (DR-5)


def test_dependency_chain_orders_and_propagates() -> None:
    res = resolve([mod("c", deps={"b": "^1"}), mod("b", deps={"a": "^1"}), mod("a")], HOST)
    assert res.order == ["a", "b", "c"] and res.inactive == {}
    res = resolve([mod("c", deps={"b": "^1"}), mod("b", deps={"zzz": "^1"}), mod("a")], HOST)
    assert res.order == ["a"]
    assert reasons(res) == {"a": None, "b": "Dependency", "c": "Dependency"}
    assert "zzz" in res.decisions["b"].detail and "not active (Dependency)" in res.decisions["c"].detail
    res = resolve([mod("b", deps={"a": "^2"}), mod("a", "1.5.0")], HOST)
    assert reasons(res)["b"] == "Dependency" and "a@1.5.0" in res.decisions["b"].detail


def test_framework_version_is_the_hard_dependency() -> None:
    res = resolve([mod("a", deps={"floofycrew": "^2"})], HOST)
    assert reasons(res) == {"a": "Dependency"} and "floofycrew 1.2.0" in res.decisions["a"].detail
    assert resolve([mod("a", deps={"floofycrew": "^1.1"})], HOST).order == ["a"]


def test_record_flags_have_priority_order() -> None:
    res = resolve(
        [
            mod("a", enabled=False, files_ok=False),
            mod("b", files_ok=False, quarantined=True),
            mod("c", quarantined=True),
            mod("d", enabled=False, kirocrew={"version": "^9", "strict": True}),
        ],
        HOST,
    )
    assert reasons(res) == {"a": "UserDisabled", "b": "MissingFiles", "c": "Quarantined", "d": "UserDisabled"}
    assert res.order == []


def test_strict_versus_warning(caplog) -> None:
    res = resolve(
        [
            mod("a", kirocrew={"version": "^0.5", "strict": True}),
            mod("b", kirocrew={"version": "^0.5"}),
            mod("c", kirocrew={"version": "*", "editions": ["external"]}),
            mod("d", kirocrew={"version": "*", "channels": ["stable"], "strict": True}),
            mod("e", kirocrew={"version": "*", "editions": ["internal"], "channels": ["beta"], "strict": True}),
        ],
        HOST,
    )
    assert reasons(res) == {"a": "Unsupported", "b": None, "c": None, "d": "Unsupported", "e": None}
    assert [w.code for w in res.decisions["b"].warnings] == ["HostVersionOutOfRange"]
    assert [w.code for w in res.decisions["c"].warnings] == ["HostEditionMismatch"]
    assert res.decisions["e"].warnings == [] and res.order == ["b", "c", "e"]


def test_conflicts_are_mutual_and_breaks_one_directional() -> None:
    res = resolve([mod("a", conflicts={"b": "*"}), mod("b")], HOST)
    assert reasons(res) == {"a": "Conflict", "b": None} and res.order == ["b"]
    assert [w.code for w in res.decisions["b"].warnings] == ["ConflictDeclared"]
    res = resolve([mod("a", conflicts={"b": "*"}), mod("b", conflicts={"a": "*"})], HOST)
    assert reasons(res) == {"a": None, "b": "Conflict"}  # both declare: the greater id gives way
    res = resolve([mod("a", conflicts={"b": "^2"}), mod("b", "1.0.0")], HOST)
    assert res.order == ["a", "b"]  # out of the declared range: no conflict
    res = resolve([mod("a", breaks={"b": "<2"}), mod("b", "1.0.0")], HOST)
    assert reasons(res) == {"a": "Conflict", "b": None} and res.decisions["b"].warnings == []
    res = resolve([mod("a", conflicts={"b": "*"}), mod("b", enabled=False)], HOST)
    assert reasons(res) == {"a": None, "b": "UserDisabled"}  # an inactive mod conflicts with nobody


def test_soft_dependencies_only_warn() -> None:
    res = resolve([mod("a", recommends={"x": "*"}, suggests={"b": {"range": "^2", "reason": "nicer"}}), mod("b", "1.0.0")], HOST)
    assert res.order == ["a", "b"]
    assert [(w.code) for w in res.decisions["a"].warnings] == ["RecommendsMissing", "SuggestsMissing"]


def test_load_before_and_after() -> None:
    res = resolve([mod("a", loadAfter=["c"]), mod("b", loadBefore=["a"]), mod("c")], HOST)
    assert res.order == ["b", "c", "a"]
    res = resolve([mod("a", loadAfter=["ghost"]), mod("b")], HOST)
    assert res.order == ["a", "b"]  # unknown ids are ignored


def test_cycles_disable_participants_only() -> None:
    res = resolve([mod("a", loadAfter=["b"]), mod("b", loadAfter=["a"]), mod("c", deps={"a": "*"}), mod("d", loadAfter=["a"])], HOST)
    assert reasons(res) == {"a": "Conflict", "b": "Conflict", "c": "Dependency", "d": None}
    assert res.decisions["a"].detail == "load-order cycle: a -> b -> a" == res.decisions["b"].detail
    assert res.order == ["d"]
    res = resolve([mod("a", deps={"b": "*"}), mod("b", deps={"c": "*"}), mod("c", deps={"a": "*"}), mod("d")], HOST)
    assert reasons(res) == {"a": "Conflict", "b": "Conflict", "c": "Conflict", "d": None}
    assert res.decisions["a"].detail.startswith("load-order cycle: a -> ")
    res = resolve([mod("a", loadAfter=["a"])], HOST)
    assert reasons(res) == {"a": "Conflict"} and "a -> a" in res.decisions["a"].detail


def test_duplicates_keep_the_highest_version() -> None:
    res = resolve([mod("a", "1.0.0"), mod("a", "1.2.0"), mod("a", "1.1.0")], HOST)
    assert res.order == ["a"] and res.decisions["a"].version == Version.parse("1.2.0")
    assert [(d.reason, str(d.version), d.detail) for d in res.duplicates] == [
        (Reason.Duplicate, "1.1.0", "superseded by a@1.2.0"),
        (Reason.Duplicate, "1.0.0", "superseded by a@1.2.0"),
    ]


def test_invalid_data_is_error_not_crash() -> None:
    res = resolve([ModRecord("a", "nope", mod("a").manifest), mod("b", kirocrew={"version": ">>1"}), mod("c", deps={"d": "^^1"}), mod("d")], HOST)
    assert reasons(res) == {"a": "Error", "b": "Error", "c": "Error", "d": None}


def test_to_dict_is_json_serialisable() -> None:
    res = resolve([mod("a", conflicts={"b": "*"}), mod("b"), mod("b", "0.9.0")], HOST)
    payload = json.loads(json.dumps(res.to_dict()))
    assert payload["order"] == ["b"] and payload["mods"]["a"]["reason"] == "Conflict"
    assert payload["mods"]["b"]["warnings"] == [{"code": "ConflictDeclared", "message": "a declares a conflict with this mod and was disabled"}]
    assert payload["duplicates"][0]["reason"] == "Duplicate"
    assert isinstance(res.decisions["a"], Decision) and res.active == ["b"]


# --- properties ------------------------------------------------------------------------------


@st.composite
def dags(draw):
    """Mods m0..m(n-1) with edges only from lower to higher index (acyclic by construction).

    Each edge is expressed as ``dependsOn`` (lower must precede higher), ``loadAfter``
    on the higher mod, or ``loadBefore`` on the lower mod.
    """
    n = draw(st.integers(min_value=1, max_value=8))
    ids = IDS[:n]
    edges: list[tuple[str, str, str]] = []
    for j in range(n):
        for i in range(j):
            if draw(st.booleans()):
                edges.append((ids[i], ids[j], draw(st.sampled_from(["dependsOn", "loadAfter", "loadBefore"]))))
    return ids, edges


def _records(ids: list[str], edges: list[tuple[str, str, str]], **extra) -> list[ModRecord]:
    manifests = {mod_id: {"id": mod_id, "version": "1.0.0", "kirocrew": {"version": "*"}, "dependsOn": {"floofycrew": "*"}} for mod_id in ids}
    for before, after, kind in edges:
        if kind == "dependsOn":
            manifests[after]["dependsOn"][before] = "*"
        elif kind == "loadAfter":
            manifests[after].setdefault("loadAfter", []).append(before)
        else:
            manifests[before].setdefault("loadBefore", []).append(after)
    return [ModRecord(mod_id, "1.0.0", manifest, **extra) for mod_id, manifest in manifests.items()]


@settings(max_examples=200, deadline=None)
@given(dags(), st.randoms(use_true_random=False))
def test_dag_order_respects_every_edge(dag, rng: random.Random) -> None:
    """**Validates: Requirements 1.5** — for an acyclic graph every mod activates exactly once
    and every dependsOn/loadAfter/loadBefore edge is honoured, whatever the input order."""
    ids, edges = dag
    records = _records(ids, edges)
    rng.shuffle(records)
    res = resolve(records, HOST)
    assert sorted(res.order) == sorted(ids) and res.inactive == {}
    position = {mod_id: index for index, mod_id in enumerate(res.order)}
    for before, after, _ in edges:
        assert position[before] < position[after], (before, after)


@settings(max_examples=200, deadline=None)
@given(dags(), st.data())
def test_every_mod_is_ordered_or_has_a_reason(dag, data) -> None:
    """**Validates: Requirements 3.5** — with random disabled/missing-file flags, each mod is
    either active (in the order) or inactive with a typed reason; inactive dependencies take
    their dependents down with Dependency."""
    ids, edges = dag
    flags = {mod_id: (data.draw(st.booleans()), data.draw(st.booleans())) for mod_id in ids}
    records = [ModRecord(r.id, r.version, r.manifest, enabled=flags[r.id][0], files_ok=flags[r.id][1]) for r in _records(ids, edges)]
    res = resolve(records, HOST)
    for mod_id in ids:
        decision = res.decisions[mod_id]
        assert (mod_id in res.order) == decision.active
        assert decision.active == (decision.reason is None)
        enabled, files_ok = flags[mod_id]
        if not enabled:
            assert decision.reason is Reason.UserDisabled
        elif not files_ok:
            assert decision.reason is Reason.MissingFiles
    for before, after, kind in edges:
        if kind == "dependsOn" and not res.decisions[before].active and flags[after] == (True, True):
            assert res.decisions[after].reason is Reason.Dependency
    assert len(res.order) == len(set(res.order))


def _cyclic_nodes(ids: list[str], edges: list[tuple[str, str]]) -> set[str]:
    """Oracle: a node is cyclic iff it can reach itself (transitive closure over ≤ 8 nodes)."""
    reach = {mod_id: {after for before, after in edges if before == mod_id} for mod_id in ids}
    changed = True
    while changed:
        changed = False
        for mod_id in ids:
            extra = set().union(*(reach[nxt] for nxt in reach[mod_id])) - reach[mod_id] if reach[mod_id] else set()
            if extra:
                reach[mod_id] |= extra
                changed = True
    return {mod_id for mod_id in ids if mod_id in reach[mod_id]}


@settings(max_examples=150, deadline=None)
@given(dags(), st.integers(min_value=2, max_value=8), st.data())
def test_cycle_participants_get_conflict(dag, size: int, data) -> None:
    """**Validates: Requirements 1.5** — adding a loadAfter ring over a random subset makes exactly
    the nodes on a cycle (per an independent reachability oracle) Conflict; everyone else still
    activates in an order honouring the remaining acyclic edges."""
    ids, edges = dag
    if len(ids) < 2:
        return
    ring = data.draw(st.lists(st.sampled_from(ids), min_size=2, max_size=min(size, len(ids)), unique=True))
    order_edges = [e for e in edges if e[2] != "dependsOn"]
    records = _records(ids, order_edges)
    by_id = {r.id: r for r in records}
    ring_edges = list(zip(ring, ring[1:] + ring[:1]))
    for current, nxt in ring_edges:
        by_id[nxt].manifest.setdefault("loadAfter", []).append(current)
    res = resolve(records, HOST)
    conflicted = {mod_id for mod_id, d in res.decisions.items() if d.reason is Reason.Conflict}
    expected = _cyclic_nodes(ids, [(b, a) for b, a, _ in order_edges] + ring_edges)
    assert set(ring) <= expected and conflicted == expected
    assert sorted(res.order) == sorted(set(ids) - expected)
    position = {mod_id: index for index, mod_id in enumerate(res.order)}
    for before, after, _ in order_edges:
        if before in position and after in position:
            assert position[before] < position[after]
    for mod_id in expected:
        assert res.decisions[mod_id].detail.startswith("load-order cycle: ")


_versions = st.builds(lambda a, b, c: f"{a}.{b}.{c}", st.integers(0, 5), st.integers(0, 5), st.integers(0, 5))


@settings(max_examples=150, deadline=None)
@given(st.lists(_versions, min_size=1, max_size=6))
def test_duplicate_ids_keep_the_highest_version(versions: list[str]) -> None:
    """**Validates: Requirements 3.5** — the winner is the maximum version; every other record is Duplicate."""
    res = resolve([mod("a", v) for v in versions], HOST)
    assert res.decisions["a"].version == max(Version.parse(v) for v in versions)
    assert len(res.duplicates) == len(versions) - 1
    assert all(d.reason is Reason.Duplicate and d.id == "a" for d in res.duplicates)


@settings(max_examples=150, deadline=None)
@given(_versions, st.integers(0, 5), st.booleans())
def test_strict_versus_non_strict(host_version: str, major: int, strict: bool) -> None:
    """**Validates: Requirements 1.3** — outside kirocrew.version, strict gives Unsupported and
    non-strict a HostVersionOutOfRange warning with the mod still active; inside, neither."""
    host = HostFacts(host_version, "internal", "beta", "1.0.0")
    in_range = Version.parse(host_version).major == major
    res = resolve([mod("a", kirocrew={"version": f"{major}.x", "strict": strict})], host)
    decision = res.decisions["a"]
    if in_range:
        assert decision.active and decision.warnings == []
    elif strict:
        assert decision.reason is Reason.Unsupported
    else:
        assert decision.active and [w.code for w in decision.warnings] == ["HostVersionOutOfRange"]


@settings(max_examples=100, deadline=None)
@given(st.booleans(), st.booleans(), st.sampled_from(["a", "z"]))
def test_conflicts_disable_exactly_one_side(a_declares: bool, b_declares: bool, name: str) -> None:
    """**Validates: Requirements 1.5** — with at least one declaration exactly one of the two is
    disabled with Conflict: the declaring mod, or the lexicographically greater one when both declare."""
    other = "b" if name == "a" else "y"
    first = mod(name, conflicts={other: "*"} if a_declares else {})
    second = mod(other, conflicts={name: "*"} if b_declares else {})
    res = resolve([first, second], HOST)
    disabled = {mod_id for mod_id, d in res.decisions.items() if d.reason is Reason.Conflict}
    if not (a_declares or b_declares):
        assert disabled == set()
    elif a_declares and b_declares:
        assert disabled == {max(name, other)}
    else:
        assert disabled == ({name} if a_declares else {other})
    assert len(res.order) == 2 - len(disabled)


@settings(max_examples=100, deadline=None)
@given(dags(), st.randoms(use_true_random=False))
def test_resolution_is_deterministic(dag, rng: random.Random) -> None:
    """**Validates: Requirements 1.5** — the same set of records yields byte-identical results
    regardless of input order, twice in a row."""
    ids, edges = dag
    records = _records(ids, edges)
    baseline = resolve(records, HOST).to_dict()
    rng.shuffle(records)
    assert resolve(records, HOST).to_dict() == baseline
    assert resolve(records, HOST).to_dict() == baseline
