"""The mod resolver: graded dependencies, conflicts, load order and typed reasons.

Pure functions, standard library only, shared by the CLI, the Loader and the
Forge (design "Mod manifest and resolver"). Given the installed mods and the
host facts it decides, for every mod, whether it activates and — when not — a
typed :class:`Reason` (Requirement 3.5), plus an activation order that honours
``dependsOn``, ``loadBefore`` and ``loadAfter`` (Requirement 1.5).

Rules (Requirement 1.3, 1.4, 1.5):

* duplicate ids keep the highest version, the others are ``Duplicate``;
* ``enabled=False`` → ``UserDisabled``; ``files_ok=False`` → ``MissingFiles``;
  ``quarantined`` → ``Quarantined`` (priority in that order);
* ``kirocrew.version`` / ``editions`` / ``channels`` mismatch → ``Unsupported``
  when ``strict``, otherwise warnings ``HostVersionOutOfRange`` /
  ``HostEditionMismatch`` / ``HostChannelMismatch`` and the mod still loads;
* ``dependsOn`` unsatisfied (missing, inactive, out of range, including
  ``floofycrew`` against the framework version) → ``Dependency``, propagated
  transitively;
* ``conflicts`` are mutual: when both mods are active the declaring one is
  disabled with ``Conflict`` (both declare → the lexicographically greater id
  is disabled) and the survivor gets a ``ConflictDeclared`` warning;
* ``breaks`` is one-directional: the declaring mod is disabled with ``Conflict``;
* ``recommends`` / ``suggests`` missing → warnings only;
* participants of a load-order cycle → ``Conflict`` naming the cycle; everyone
  else keeps booting.

Governance is never a reason: the Loader attaches it as a warning (DR-5).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .semver import InvalidRange, InvalidVersion, Range, Version

__all__ = [
    "Decision",
    "HostFacts",
    "ModRecord",
    "ModWarning",
    "Reason",
    "Resolution",
    "resolve",
]

FRAMEWORK_ID = "floofycrew"


class Reason(str, Enum):
    """Typed non-load reasons (Requirement 3.5). Governance is deliberately absent."""

    Error = "Error"
    Duplicate = "Duplicate"
    Conflict = "Conflict"
    Dependency = "Dependency"
    Released = "Released"
    Feature = "Feature"
    Unsupported = "Unsupported"
    MissingFiles = "MissingFiles"
    Quarantined = "Quarantined"
    UserDisabled = "UserDisabled"


@dataclass(frozen=True)
class ModWarning:
    """A non-fatal note attached to a mod (host range, soft dependencies, governance)."""

    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ModRecord:
    """One installed mod as the Loader or CLI sees it before resolution."""

    id: str
    version: Version | str
    manifest: Mapping[str, Any]
    enabled: bool = True
    files_ok: bool = True
    quarantined: bool = False


@dataclass(frozen=True)
class HostFacts:
    """What the resolver needs to know about the host and the framework."""

    base_version: Version | str
    edition: str
    channel: str
    framework_version: Version | str


@dataclass
class Decision:
    """The verdict for one mod."""

    id: str
    version: Version
    active: bool = True
    reason: Reason | None = None
    detail: str = ""
    warnings: list[ModWarning] = field(default_factory=list)

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(ModWarning(code, message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": str(self.version),
            "active": self.active,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "warnings": [w.to_dict() for w in self.warnings],
        }


@dataclass
class Resolution:
    """``order`` lists the active ids in activation order; ``decisions`` covers every id."""

    order: list[str]
    decisions: dict[str, Decision]
    duplicates: list[Decision] = field(default_factory=list)

    @property
    def active(self) -> list[str]:
        return list(self.order)

    @property
    def inactive(self) -> dict[str, Decision]:
        return {mod_id: d for mod_id, d in self.decisions.items() if not d.active}

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": list(self.order),
            "mods": {mod_id: self.decisions[mod_id].to_dict() for mod_id in sorted(self.decisions)},
            "duplicates": [d.to_dict() for d in self.duplicates],
        }


# --- internals ---------------------------------------------------------------------


@dataclass
class _Node:
    record: ModRecord
    version: Version
    manifest: Mapping[str, Any]
    parse_error: str | None = None


@dataclass(frozen=True)
class _Host:
    base_version: Version
    edition: str
    channel: str
    framework_version: Version


def _parse_version(text: Version | str) -> tuple[Version, str | None]:
    try:
        return Version.coerce(text), None
    except InvalidVersion as exc:
        return Version(0, 0, 0), str(exc)


def _range_of(spec: Any) -> Range:
    """A dependency entry is ``range`` or ``{range, reason}``."""
    text = spec.get("range") if isinstance(spec, Mapping) else spec
    return Range.parse(text) if isinstance(text, str) else Range.ANY


def _entries(manifest: Mapping[str, Any], key: str) -> list[tuple[str, Any]]:
    value = manifest.get(key)
    if not isinstance(value, Mapping):
        return []
    return sorted((str(k), v) for k, v in value.items())


def _ids(manifest: Mapping[str, Any], key: str) -> list[str]:
    value = manifest.get(key)
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def _deactivate(decision: Decision, reason: Reason, detail: str) -> bool:
    """Monotone: a mod is never re-activated. Returns whether anything changed."""
    if not decision.active:
        return False
    decision.active = False
    decision.reason = reason
    decision.detail = detail
    return True


def _dedupe(records: Iterable[ModRecord]) -> tuple[dict[str, _Node], list[Decision]]:
    """Requirement 1.4/3.5: same id twice → the highest version wins, the rest are ``Duplicate``."""
    grouped: dict[str, list[tuple[int, _Node]]] = {}
    for index, record in enumerate(records):
        version, error = _parse_version(record.version)
        grouped.setdefault(record.id, []).append((index, _Node(record, version, record.manifest, error)))
    winners: dict[str, _Node] = {}
    duplicates: list[Decision] = []
    for mod_id in sorted(grouped):
        candidates = grouped[mod_id]
        # highest version first; equal versions keep input order (stable sort on the index)
        candidates.sort(key=lambda item: (item[1].version.precedence_key(), -item[0]), reverse=True)
        winner = candidates[0][1]
        winners[mod_id] = winner
        for _, loser in candidates[1:]:
            duplicates.append(
                Decision(mod_id, loser.version, False, Reason.Duplicate, f"superseded by {mod_id}@{winner.version}")
            )
    return winners, duplicates


def _check_intrinsic(node: _Node, decision: Decision, host: _Host) -> None:
    """Per-mod checks that need no other mod: user state, files, quarantine, host compat."""
    record = node.record
    if not record.enabled:
        _deactivate(decision, Reason.UserDisabled, "disabled by the user")
        return
    if not record.files_ok:
        _deactivate(decision, Reason.MissingFiles, "a shipped file is missing or its hash does not match")
        return
    if record.quarantined:
        _deactivate(decision, Reason.Quarantined, "quarantined by the compatibility matrix for this host version")
        return
    if node.parse_error:
        _deactivate(decision, Reason.Error, f"invalid version: {node.parse_error}")
        return
    _check_host_compat(node, decision, host)


def _check_host_compat(node: _Node, decision: Decision, host: _Host) -> None:
    """Requirement 1.3: out of range → ``Unsupported`` when strict, else a warning."""
    kirocrew = node.manifest.get("kirocrew")
    if not isinstance(kirocrew, Mapping):
        return
    problems: list[tuple[str, str]] = []
    version_range = kirocrew.get("version")
    if isinstance(version_range, str):
        try:
            if not Range.parse(version_range).contains(host.base_version):
                problems.append(("HostVersionOutOfRange", f"host {host.base_version} is outside kirocrew.version {version_range!r}"))
        except InvalidRange as exc:
            _deactivate(decision, Reason.Error, f"invalid kirocrew.version: {exc}")
            return
    editions = kirocrew.get("editions")
    if isinstance(editions, (list, tuple)) and host.edition not in editions:
        problems.append(("HostEditionMismatch", f"host edition {host.edition!r} is not in {list(editions)}"))
    channels = kirocrew.get("channels")
    if isinstance(channels, (list, tuple)) and host.channel not in channels:
        problems.append(("HostChannelMismatch", f"host channel {host.channel!r} is not in {list(channels)}"))
    if not problems:
        return
    if kirocrew.get("strict"):
        _deactivate(decision, Reason.Unsupported, "; ".join(message for _, message in problems))
    else:
        for code, message in problems:
            decision.warn(code, message + " (loading anyway: strict is false)")


def _apply_dependencies(winners: dict[str, _Node], decisions: dict[str, Decision], host: _Host) -> bool:
    """Requirement 1.4: hard dependencies, including ``floofycrew`` against the framework version."""
    changed = False
    for mod_id in sorted(winners):
        decision = decisions[mod_id]
        if not decision.active:
            continue
        for dep_id, spec in _entries(winners[mod_id].manifest, "dependsOn"):
            try:
                wanted = _range_of(spec)
            except InvalidRange as exc:
                changed |= _deactivate(decision, Reason.Error, f"invalid dependsOn range for {dep_id}: {exc}")
                break
            if dep_id == FRAMEWORK_ID:
                if not wanted.contains(host.framework_version):
                    changed |= _deactivate(
                        decision, Reason.Dependency, f"requires floofycrew {wanted}, this is floofycrew {host.framework_version}"
                    )
                    break
                continue
            target = winners.get(dep_id)
            if target is None:
                changed |= _deactivate(decision, Reason.Dependency, f"requires {dep_id} {wanted}, which is not installed")
                break
            if not decisions[dep_id].active:
                why = decisions[dep_id].reason.value if decisions[dep_id].reason else "inactive"
                changed |= _deactivate(decision, Reason.Dependency, f"requires {dep_id}, which is not active ({why})")
                break
            if not wanted.contains(target.version):
                changed |= _deactivate(
                    decision, Reason.Dependency, f"requires {dep_id} {wanted}, installed is {dep_id}@{target.version}"
                )
                break
    return changed


def _declares(manifest: Mapping[str, Any], key: str, other_id: str, other_version: Version) -> bool:
    for dep_id, spec in _entries(manifest, key):
        if dep_id == other_id:
            try:
                return _range_of(spec).contains(other_version)
            except InvalidRange:
                return True
    return False


def _apply_conflicts(winners: dict[str, _Node], decisions: dict[str, Decision]) -> bool:
    """Requirement 1.5: ``conflicts`` are mutual, ``breaks`` one-directional; both give ``Conflict``."""
    changed = False
    for mod_id in sorted(winners):
        decision = decisions[mod_id]
        if not decision.active:
            continue
        node = winners[mod_id]
        for other_id, spec in _entries(node.manifest, "conflicts"):
            other = winners.get(other_id)
            if other is None or not decisions[other_id].active or other_id == mod_id:
                continue
            try:
                if not _range_of(spec).contains(other.version):
                    continue
            except InvalidRange:
                pass
            mutual = _declares(other.manifest, "conflicts", mod_id, node.version)
            if mutual and mod_id < other_id:
                continue  # both declare it: the greater id is the one disabled, handled on its turn
            changed |= _deactivate(decision, Reason.Conflict, f"conflicts with {other_id}@{other.version}")
            decisions[other_id].warn("ConflictDeclared", f"{mod_id} declares a conflict with this mod and was disabled")
            break
        if not decision.active:
            continue
        for other_id, spec in _entries(node.manifest, "breaks"):
            other = winners.get(other_id)
            if other is None or not decisions[other_id].active or other_id == mod_id:
                continue
            try:
                if not _range_of(spec).contains(other.version):
                    continue
            except InvalidRange:
                pass
            changed |= _deactivate(decision, Reason.Conflict, f"breaks {other_id}@{other.version}")
            break
    return changed


def _fixed_point(winners: dict[str, _Node], decisions: dict[str, Decision], host: _Host) -> None:
    """Deactivation is monotone, so alternating the two passes terminates."""
    while True:
        changed = _apply_dependencies(winners, decisions, host)
        changed = _apply_conflicts(winners, decisions) or changed
        if not changed:
            return


def _edges(winners: dict[str, _Node], active: set[str]) -> dict[str, set[str]]:
    """``u → v`` means u activates before v (dependsOn, loadAfter, loadBefore)."""
    successors: dict[str, set[str]] = {mod_id: set() for mod_id in active}
    for mod_id in active:
        manifest = winners[mod_id].manifest
        for dep_id, _ in _entries(manifest, "dependsOn"):
            if dep_id in active and dep_id != FRAMEWORK_ID:
                successors[dep_id].add(mod_id)
        for other in _ids(manifest, "loadAfter"):
            if other in active:
                successors[other].add(mod_id)
        for other in _ids(manifest, "loadBefore"):
            if other in active:
                successors[mod_id].add(other)
    return successors


def _kahn(successors: dict[str, set[str]]) -> tuple[list[str], set[str]]:
    """Deterministic Kahn: ready nodes are taken in id order. Returns (order, unplaced)."""
    indegree = {node: 0 for node in successors}
    for targets in successors.values():
        for target in targets:
            indegree[target] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for target in sorted(successors[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    return order, {node for node in successors if node not in order}


def _cycles(successors: dict[str, set[str]], nodes: set[str]) -> list[list[str]]:
    """Strongly connected components of size > 1 (or with a self-loop) among ``nodes``."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index_of[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(successors[node]):
            if target not in nodes:
                continue
            if target not in index_of:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index_of[target])
        if low[node] == index_of[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in successors[node]:
                components.append(sorted(component))

    for node in sorted(nodes):
        if node not in index_of:
            visit(node)
    return sorted(components)


def _cycle_path(component: list[str], successors: dict[str, set[str]]) -> str:
    """A readable walk around the cycle starting at its smallest id (``a -> b -> a``)."""
    members = set(component)
    start = component[0]
    path = [start]
    seen = {start}
    current = start
    while True:
        options = sorted(t for t in successors[current] if t in members)
        if not options:
            break
        if start in options and (len(path) > 1 or len(component) == 1):
            nxt = start
        else:
            unseen = [t for t in options if t not in seen]
            nxt = unseen[0] if unseen else options[0]
        path.append(nxt)
        if nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return " -> ".join(path)


def _order(winners: dict[str, _Node], decisions: dict[str, Decision], host: _Host) -> list[str]:
    """Requirement 1.5: topological order; cycle participants become ``Conflict`` and the rest proceeds."""
    while True:
        active = {mod_id for mod_id, d in decisions.items() if d.active}
        successors = _edges(winners, active)
        order, unplaced = _kahn(successors)
        if not unplaced:
            return order
        for component in _cycles(successors, unplaced):
            path = _cycle_path(component, successors)
            for mod_id in component:
                _deactivate(decisions[mod_id], Reason.Conflict, f"load-order cycle: {path}")
        _fixed_point(winners, decisions, host)


def _soft_warnings(winners: dict[str, _Node], decisions: dict[str, Decision]) -> None:
    """Requirement 1.4: ``recommends`` and ``suggests`` never block, they annotate."""
    for mod_id in sorted(winners):
        decision = decisions[mod_id]
        if not decision.active:
            continue
        for key, code in (("recommends", "RecommendsMissing"), ("suggests", "SuggestsMissing")):
            for other_id, spec in _entries(winners[mod_id].manifest, key):
                try:
                    wanted = _range_of(spec)
                except InvalidRange:
                    continue
                other = winners.get(other_id)
                if other is None:
                    decision.warn(code, f"{key} {other_id} {wanted}, which is not installed")
                elif not decisions[other_id].active:
                    decision.warn(code, f"{key} {other_id}, which is not active")
                elif not wanted.contains(other.version):
                    decision.warn(code, f"{key} {other_id} {wanted}, installed is {other_id}@{other.version}")


def resolve(records: Iterable[ModRecord], host: HostFacts) -> Resolution:
    """Decide activation, reasons, warnings and order for ``records`` on ``host``.

    Pure and deterministic: the same input always yields the same
    :class:`Resolution`; unconstrained mods are ordered by id.
    """
    facts = _Host(
        Version.coerce(host.base_version),
        host.edition,
        host.channel,
        Version.coerce(host.framework_version),
    )
    winners, duplicates = _dedupe(records)
    decisions = {mod_id: Decision(mod_id, node.version) for mod_id, node in winners.items()}
    for mod_id in sorted(winners):
        _check_intrinsic(winners[mod_id], decisions[mod_id], facts)
    _fixed_point(winners, decisions, facts)
    order = _order(winners, decisions, facts)
    _soft_warnings(winners, decisions)
    return Resolution(order, decisions, duplicates)
