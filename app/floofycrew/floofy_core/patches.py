"""Patch descriptors (``kind: patch``): fingerprint-gated text ops (Requirement 5.5).

A descriptor names one ``target`` file of a payload and a list of ops. Every text
op is gated by a **content fingerprint that must match exactly once** (a literal,
or a regular expression when ``regex`` is true) and by the descriptor's
host-version gates (``appliesTo`` on the ``X.Y.Z`` base, ``fromBuild`` inclusive /
``toBuild`` exclusive on the full host version, Vencord-style). A ``marker`` is a
literal whose presence means the op has already been applied; without one the
op's own ``content`` serves as the implicit marker.

Ops (design "Data Models → Patch descriptor"):

* ``insert-before`` / ``insert-after`` — ``content`` goes around the single match;
* ``replace`` — the single match becomes ``content`` (literal, no group references);
* ``append-head`` — ``content`` goes just before the single ``</head>``.

**Per-op atomicity.** An op either fully applies or is skipped with a typed
diagnostic naming the target file and the op index — ``NotApplicable``
(descriptor-level gate), ``AlreadyApplied``, ``FingerprintMiss`` (zero matches),
``FingerprintAmbiguous`` (several). A skip never partially rewrites; ops are
independent, so the others still apply. The Patcher writes the result back only
when something changed.

Two helpers serve the spike-1.4 module-patching path: :func:`merge_import_map`
edits the host's inline ``<script type="importmap">`` idempotently by key, and
:func:`drop_modulepreload` removes the ``<link rel="modulepreload">`` hint of a
remapped chunk. :func:`fingerprint_report` is the reporter (Requirement 4.5)
shared with the SPA host and the Forge: which fingerprints still match.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from .schema import load_schema
from .schema.check import validate_instance
from .semver import HostVersion, InvalidRange, InvalidVersion, Range

__all__ = [
    "Applied",
    "ELECTRON_TARGET_PREFIX",
    "FingerprintReport",
    "PatchDescriptor",
    "PatchDescriptorError",
    "PatchOp",
    "PatchResult",
    "SKIP_CODES",
    "Skip",
    "UiAsset",
    "apply_descriptor",
    "drop_modulepreload",
    "find_import_map",
    "fingerprint_report",
    "format_skip",
    "merge_import_map",
]

SKIP_CODES = ("NotApplicable", "AlreadyApplied", "FingerprintMiss", "FingerprintAmbiguous", "IntegrityLocked")

#: ``target: "electron:<member>"`` — a member of the desktop shell's ``app.asar`` (Requirement 5.11).
ELECTRON_TARGET_PREFIX = "electron:"

_HEAD_CLOSE = "</head>"
_IMPORT_MAP_RE = re.compile(r'<script\s+type=(["\'])importmap\1[^>]*>(?P<json>.*?)</script>', re.S | re.I)
_FIRST_SCRIPT_RE = re.compile(r"<script\b", re.I)


class PatchDescriptorError(ValueError):
    """The descriptor does not conform to ``patch.schema.json`` or its fields do not parse."""

    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


@dataclass(frozen=True)
class PatchOp:
    op: str
    content: str
    fingerprint: str | None = None
    regex: bool = False
    marker: str | None = None
    description: str = ""
    #: ``only`` (default: exactly one match), ``first`` or ``last`` (the fingerprint names a class of
    #: locations and the op picks one — e.g. "before the first <script").
    occurrence: str = "only"

    @property
    def effective_marker(self) -> str:
        """The idempotency marker: ``marker`` when given, else the op's own content."""
        return self.marker if self.marker else self.content

    def already_applied(self, text: str) -> bool:
        return bool(self.effective_marker) and self.effective_marker in text


@dataclass(frozen=True)
class UiAsset:
    """A file the Patcher copies under the Loader app's ``ui/`` for a descriptor (boot favicon/logo).

    Not part of the JSON descriptor: attached in memory by the generator
    (:mod:`floofy_core.boot_script`) and recorded in the deployment manifest as an
    added file, so ``restore`` removes it and ``verify`` checks it through the app-UI route.
    """

    source: Path
    dest: str  # relative to ui/, POSIX
    mod: str


@dataclass(frozen=True)
class PatchDescriptor:
    """A validated ``kind: patch`` descriptor."""

    target: str
    ops: tuple[PatchOp, ...]
    applies_to: Range | None = None
    from_build: HostVersion | None = None
    to_build: HostVersion | None = None
    cache_bust: bool = False
    import_map: dict[str, str] = field(default_factory=dict)
    description: str = ""
    source: str = "<inline>"
    #: ``in-place`` (rewrite the target) or ``import-map`` (patched copy swapped in through the import map — spike 1.4).
    mode: str = "in-place"
    #: Optional literal that must occur in the target text before any op applies (module identification).
    find: str | None = None
    #: Files copied under the Loader app's ``ui/`` when the descriptor applies (in-memory only).
    ui_assets: tuple[UiAsset, ...] = ()

    def with_ui_assets(self, assets: Iterable[UiAsset]) -> "PatchDescriptor":
        return replace(self, ui_assets=tuple(assets))

    @property
    def is_import_map(self) -> bool:
        return self.mode == "import-map"

    @property
    def is_electron(self) -> bool:
        """``target`` names a member of the desktop shell's ``app.asar`` (``electron:<member>``, Requirement 5.11)."""
        return self.target.startswith(ELECTRON_TARGET_PREFIX)

    @property
    def asar_member(self) -> str:
        """The member path inside ``app.asar`` for an electron target (POSIX, no leading slash)."""
        return self.target[len(ELECTRON_TARGET_PREFIX) :].lstrip("/") if self.is_electron else ""

    @property
    def target_is_glob(self) -> bool:
        return not self.is_electron and any(ch in self.target for ch in "*?[")

    @classmethod
    def load(cls, path: Path | str) -> "PatchDescriptor":
        """Read and validate a descriptor file against ``patch.schema.json``."""
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PatchDescriptorError(str(path), [f"not readable JSON: {exc}"]) from exc
        return cls.from_dict(document, source=str(path))

    @classmethod
    def from_dict(cls, document: Any, *, source: str = "<inline>") -> "PatchDescriptor":
        problems = [f"{e.path or '/'}: {e.message}" for e in validate_instance(document, load_schema("patch"))]
        if problems:
            raise PatchDescriptorError(source, problems)
        applies_to = from_build = to_build = None
        if isinstance(document.get("appliesTo"), str):
            try:
                applies_to = Range.parse(document["appliesTo"])
            except InvalidRange as exc:
                problems.append(f"/appliesTo: {exc}")
        for key in ("fromBuild", "toBuild"):
            if isinstance(document.get(key), str):
                try:
                    parsed = HostVersion.parse(document[key])
                except InvalidVersion as exc:
                    problems.append(f"/{key}: {exc}")
                    continue
                if key == "fromBuild":
                    from_build = parsed
                else:
                    to_build = parsed
        ops: list[PatchOp] = []
        for index, raw in enumerate(document["ops"]):
            fingerprint = raw.get("fingerprint")
            if raw.get("regex") and isinstance(fingerprint, str):
                try:
                    re.compile(fingerprint)
                except re.error as exc:
                    problems.append(f"/ops/{index}/fingerprint: not a valid regular expression: {exc}")
            ops.append(
                PatchOp(
                    op=raw["op"],
                    content=raw["content"],
                    fingerprint=fingerprint,
                    regex=bool(raw.get("regex", False)),
                    marker=raw.get("marker"),
                    description=raw.get("description", ""),
                    occurrence=str(raw.get("occurrence") or "only"),
                )
            )
        if problems:
            raise PatchDescriptorError(source, problems)
        return cls(
            target=document["target"],
            ops=tuple(ops),
            applies_to=applies_to,
            from_build=from_build,
            to_build=to_build,
            cache_bust=bool(document.get("cacheBust", False)),
            import_map=dict(document.get("importMap") or {}),
            description=document.get("description", ""),
            source=source,
            mode=str(document.get("mode") or "in-place"),
            find=document.get("find") if isinstance(document.get("find"), str) and document.get("find") else None,
        )

    def applicability(self, host_version: HostVersion | str | None) -> str | None:
        """``None`` when the descriptor applies to ``host_version``, else the reason it does not."""
        if host_version is None:
            return None
        version = HostVersion.coerce(host_version)
        if self.applies_to is not None and not self.applies_to.contains(version.base):
            return f"host base {version.base} is outside appliesTo {self.applies_to.text or self.applies_to}"
        if self.from_build is not None and version < self.from_build:
            return f"host {version} is below fromBuild {self.from_build}"
        if self.to_build is not None and not version < self.to_build:
            return f"host {version} is not below toBuild {self.to_build}"
        return None

    @property
    def markers(self) -> list[str]:
        return [op.effective_marker for op in self.ops if op.effective_marker]


# --- results -----------------------------------------------------------------------


@dataclass(frozen=True)
class Skip:
    """One skipped op (``op_index`` is ``None`` for a descriptor-level skip)."""

    target: str
    op_index: int | None
    code: str
    message: str

    def format(self) -> str:
        return format_skip(self)


def format_skip(skip: Skip) -> str:
    where = skip.target if skip.op_index is None else f"{skip.target}#ops/{skip.op_index}"
    return f"SKIP {where}: {skip.code} — {skip.message}"


@dataclass(frozen=True)
class Applied:
    target: str
    op_index: int
    op: str
    position: int


@dataclass
class PatchResult:
    """Outcome of :func:`apply_descriptor`; ``text`` is the original when nothing applied."""

    text: str
    applied: list[Applied] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    import_map_applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.applied) or self.import_map_applied

    def codes(self) -> list[str]:
        return [s.code for s in self.skipped]

    def diagnostics(self) -> list[str]:
        return [s.format() for s in self.skipped]


# --- matching ----------------------------------------------------------------------


def _find_matches(text: str, fingerprint: str, regex: bool) -> list[tuple[int, int]]:
    if regex:
        return [(m.start(), m.end()) for m in re.finditer(fingerprint, text)]
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(fingerprint, start)
        if index < 0:
            return spans
        spans.append((index, index + len(fingerprint)))
        start = index + max(1, len(fingerprint))


def _apply_op(text: str, op: PatchOp, target: str, index: int) -> tuple[str, Applied | None, Skip | None]:
    """Apply one op to ``text``; exactly one of ``Applied``/``Skip`` is returned."""
    label = f"{op.op}" + (f" ({op.description})" if op.description else "")
    if op.already_applied(text):
        return text, None, Skip(target, index, "AlreadyApplied", f"{label}: marker {op.effective_marker[:60]!r} already present in {target}")
    if op.op == "append-head":
        fingerprint, regex = _HEAD_CLOSE, False
    else:
        fingerprint, regex = op.fingerprint or "", op.regex
    spans = _find_matches(text, fingerprint, regex)
    if len(spans) == 0:
        return text, None, Skip(target, index, "FingerprintMiss", f"{label}: fingerprint {fingerprint[:80]!r} matches nowhere in {target}")
    if len(spans) > 1 and op.occurrence == "only":
        return text, None, Skip(target, index, "FingerprintAmbiguous", f"{label}: fingerprint {fingerprint[:80]!r} matches {len(spans)} times in {target}; exactly one match is required")
    start, end = spans[-1] if op.occurrence == "last" else spans[0]
    if op.op in ("insert-before", "append-head"):
        new_text, position = text[:start] + op.content + text[start:], start
    elif op.op == "insert-after":
        new_text, position = text[:end] + op.content + text[end:], end
    elif op.op == "replace":
        new_text, position = text[:start] + op.content + text[end:], start
    else:  # pragma: no cover - the schema forbids other ops
        return text, None, Skip(target, index, "FingerprintMiss", f"unknown op {op.op!r}")
    return new_text, Applied(target, index, op.op, position), None


def apply_descriptor(text: str, descriptor: PatchDescriptor, host_version: HostVersion | str | None = None) -> PatchResult:
    """Apply every op of ``descriptor`` to ``text`` with per-op atomicity (Requirement 5.5).

    A descriptor whose gates exclude ``host_version`` skips everything with one
    ``NotApplicable``. Each op is matched against the text as left by the ops
    before it, so a later op may anchor on an earlier op's content.
    """
    result = PatchResult(text=text)
    reason = descriptor.applicability(host_version)
    if reason is not None:
        result.skipped.append(Skip(descriptor.target, None, "NotApplicable", f"{reason} ({descriptor.source})"))
        return result
    if descriptor.find is not None and descriptor.find not in text:
        result.skipped.append(Skip(descriptor.target, None, "FingerprintMiss", f"find literal {descriptor.find[:80]!r} is absent from {descriptor.target}; the module this descriptor targets is not this file ({descriptor.source})"))
        return result
    working = text
    for index, op in enumerate(descriptor.ops):
        working, applied, skip = _apply_op(working, op, descriptor.target, index)
        if applied is not None:
            result.applied.append(applied)
        if skip is not None:
            result.skipped.append(skip)
    if descriptor.import_map:
        merged, changed = merge_import_map(working, descriptor.import_map)
        if changed:
            working, result.import_map_applied = merged, True
    result.text = working
    return result


# --- reporter ----------------------------------------------------------------------


@dataclass
class FingerprintReport:
    """Which fingerprints match exactly once (or are already applied) in a text (Requirement 4.5)."""

    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missed)

    def to_dict(self) -> dict[str, Any]:
        return {"matched": len(self.matched), "total": self.total, "missed": list(self.missed), "details": dict(self.details)}


def fingerprint_report(text: str, descriptors: Iterable[PatchDescriptor], host_version: HostVersion | str | None = None) -> FingerprintReport:
    """Evaluate every fingerprint of every applicable descriptor against ``text``."""
    report = FingerprintReport()
    for descriptor in descriptors:
        if descriptor.applicability(host_version) is not None:
            continue
        find_ok = descriptor.find is None or descriptor.find in text
        for index, op in enumerate(descriptor.ops):
            key = f"{descriptor.target}#ops/{index}"
            if not find_ok:
                report.missed.append(key)
                report.details[key] = "find-miss"
                continue
            if op.already_applied(text):
                report.matched.append(key)
                report.details[key] = "already-applied"
                continue
            fingerprint, regex = (_HEAD_CLOSE, False) if op.op == "append-head" else (op.fingerprint or "", op.regex)
            count = len(_find_matches(text, fingerprint, regex))
            if count == 1 or (count > 1 and op.occurrence != "only"):
                report.matched.append(key)
                report.details[key] = "matched"
            else:
                report.missed.append(key)
                report.details[key] = "miss" if count == 0 else f"ambiguous ({count})"
    return report


# --- import map helpers (spike 1.4) ------------------------------------------------


def find_import_map(index_html: str) -> tuple[re.Match[str] | None, dict[str, Any] | None]:
    """The single inline import map of ``index_html`` and its parsed JSON (``(None, None)`` when absent).

    Raises ``ValueError`` when several import maps exist or the JSON is invalid —
    both are conditions the Patcher must not paper over.
    """
    matches = list(_IMPORT_MAP_RE.finditer(index_html))
    if not matches:
        return None, None
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} import maps in the document; exactly one is expected")
    try:
        parsed = json.loads(matches[0].group("json"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"import map is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("import map is not a JSON object")
    return matches[0], parsed


def merge_import_map(index_html: str, mapping: dict[str, str | None]) -> tuple[str, bool]:
    """Add, replace or (``None``) remove ``imports`` keys in the inline import map, idempotently.

    Other keys and other map sections (``scopes``, ``integrity``) are preserved.
    When the document has no import map and there is something to add, a new
    ``<script type="importmap">`` is inserted before the first ``<script`` (an
    import map must precede module scripts). Returns ``(html, changed)``.
    """
    match, parsed = find_import_map(index_html)
    imports: dict[str, Any] = dict(parsed.get("imports", {})) if parsed else {}
    original = dict(imports)
    for key, value in mapping.items():
        if value is None:
            imports.pop(key, None)
        else:
            imports[key] = value
    if imports == original:
        return index_html, False
    document = dict(parsed) if parsed else {}
    document["imports"] = imports
    serialized = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    if match is not None:
        start, end = match.start("json"), match.end("json")
        return index_html[:start] + serialized + index_html[end:], True
    tag = f'<script type="importmap">{serialized}</script>\n    '
    first = _FIRST_SCRIPT_RE.search(index_html)
    if first is None:
        raise ValueError("no <script> tag to anchor a new import map on")
    return index_html[: first.start()] + tag + index_html[first.start() :], True


def drop_modulepreload(index_html: str, url: str) -> tuple[str, bool]:
    """Remove the ``<link rel="modulepreload" href="<url>">`` hint(s) for a remapped chunk (any attribute order)."""
    pattern = re.compile(
        r'[ \t]*<link\b(?=[^>]*\brel=(["\'])modulepreload\1)(?=[^>]*\bhref=(["\'])' + re.escape(url) + r'\2)[^>]*>[ \t]*\n?',
        re.I,
    )
    new_html, count = pattern.subn("", index_html)
    return new_html, count > 0
