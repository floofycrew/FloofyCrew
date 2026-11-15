"""Module patches on hashed chunks through the import map (Requirement 4.4; design "Spike outcomes" 1.4).

Blob/data module imports are blocked by the served CSP and native ES modules have
no runtime registry to rewrite, so a ``kind: patch`` descriptor with
``"mode": "import-map"`` is applied **at Patcher time**:

1. resolve the ``target`` (a hashed chunk under ``static/dist/assets/``; the
   name may be a glob such as ``useTheme-*.js`` that must match exactly one
   file — a fingerprint on the name, never a hash);
2. read the ORIGINAL chunk (it is never modified on disk), apply the regex ops
   (:func:`floofy_core.patches.apply_descriptor`: exactly-once fingerprints,
   ``find``, ``appliesTo`` / ``fromBuild`` / ``toBuild`` gates);
3. rebase the copy's relative imports (``from"./x.js"`` → ``from"/assets/x.js"``
   …) so they still resolve from the new URL, and prepend a marker comment;
4. write the copy under the Loader app's ``ui/patched/<chunk name>`` (served
   ``no-cache`` by the unauthenticated app-UI route, survives host updates as a
   file) plus a sidecar ``<chunk>.floofy.json`` describing the descriptor's
   fingerprints for the runtime inspection API, and refresh ``ui/patched/index.json``;
5. hand the caller two ``index.html`` edits: the import-map key
   ``"/assets/<chunk>": "/apps/floofycrew/ui/patched/<chunk>"`` and the removal
   of the chunk's ``<link rel="modulepreload">`` hint — both idempotent, both
   recorded in the deployment manifest like any other rewrite.

The entry chunk (the one ``index.html`` loads with ``<script type="module"
src>``) is *not* reachable through the import map (a ``src`` URL is not a
specifier, and a remap of its importers would create a second module instance),
so :func:`is_entry_chunk` lets the Patcher fall back to the in-place +
alias-graph path for it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .deploy import DeployManifest, atomic_write_bytes, sha256_bytes, utc_now
from .governance import LOADER_APP_NAME
from .patches import PatchDescriptor, PatchResult, Skip, apply_descriptor, drop_modulepreload, merge_import_map
from .payloads import Payload
from .semver import HostVersion

__all__ = [
    "ImportMapOutcome",
    "PATCHED_DIR",
    "PATCHED_INDEX",
    "apply_import_map_patch",
    "default_ui_root",
    "is_entry_chunk",
    "patched_url",
    "rebase_imports",
    "refresh_patched_index",
    "resolve_chunk_target",
    "sidecar_for",
    "ui_url_for",
]

#: Subdirectory of the Loader app's ``ui/`` holding patched chunk copies.
PATCHED_DIR = "patched"
#: Index of every remap the Patcher wrote (for ``window.floofy.patches.list()``).
PATCHED_INDEX = "index.json"
#: The URL prefix the app-UI route serves the Loader app's ``ui/`` under.
UI_URL_PREFIX = f"/apps/{LOADER_APP_NAME}/ui"

_REL_IMPORT_RE = re.compile(r"""(?P<lead>(?:\bfrom|\bimport|\bexport\b[^;]*?\bfrom)\s*\(?\s*)(?P<q>["'])\./(?P<path>[^"']+)(?P=q)""")
_DYNAMIC_IMPORT_RE = re.compile(r"""(?P<lead>\bimport\s*\(\s*)(?P<q>["'])\./(?P<path>[^"']+)(?P=q)""")
_SCRIPT_SRC_RE = re.compile(r'<script\b[^>]*\bsrc=(["\'])(?P<url>[^"\']+)\1', re.I)


def default_ui_root(host_home: Path) -> Path:
    """``<host home>/apps/floofycrew/ui`` — where the installed Loader app's UI files live."""
    return Path(host_home) / "apps" / LOADER_APP_NAME / "ui"


def patched_url(chunk_name: str) -> str:
    return f"{UI_URL_PREFIX}/{PATCHED_DIR}/{chunk_name}"


def ui_url_for(ui_root: Path, path: Path) -> str | None:
    """The app-UI route URL of a file under ``ui_root`` (``None`` outside it)."""
    try:
        relative = Path(path).resolve().relative_to(Path(ui_root).resolve())
    except ValueError:
        return None
    return f"{UI_URL_PREFIX}/{relative.as_posix()}"


def sidecar_for(copy_path: Path) -> Path:
    return copy_path.with_name(copy_path.name + ".floofy.json")


def resolve_chunk_target(payload: Payload, target: str) -> tuple[Path | None, Skip | None]:
    """Exactly one existing file for ``target`` (literal or glob) relative to the directory holding ``kiro_crew``."""
    base = payload.package_dir.parent
    if any(ch in target for ch in "*?["):
        matches = sorted(p for p in base.glob(target) if p.is_file())
    else:
        candidate = base / target
        matches = [candidate] if candidate.is_file() else []
    if not matches:
        return None, Skip(target, None, "FingerprintMiss", f"target {target!r} matches no file in {payload.id}")
    if len(matches) > 1:
        return None, Skip(target, None, "FingerprintAmbiguous", f"target {target!r} matches {len(matches)} files in {payload.id}: {', '.join(p.name for p in matches)}")
    resolved = matches[0].resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None, Skip(target, None, "FingerprintMiss", f"target {target!r} escapes the payload")
    return resolved, None


def is_entry_chunk(index_html: str, chunk_name: str) -> bool:
    """True when ``index.html`` loads the chunk with ``<script src>`` — the import map cannot reach it."""
    for match in _SCRIPT_SRC_RE.finditer(index_html):
        if match.group("url").rsplit("/", 1)[-1] == chunk_name:
            return True
    return False


def rebase_imports(text: str, base_url: str = "/assets/") -> str:
    """``./x.js`` module specifiers → ``/assets/x.js`` so a copy served elsewhere still resolves its imports."""
    base = base_url if base_url.endswith("/") else base_url + "/"
    text = _REL_IMPORT_RE.sub(lambda m: f"{m.group('lead')}{m.group('q')}{base}{m.group('path')}{m.group('q')}", text)
    return _DYNAMIC_IMPORT_RE.sub(lambda m: f"{m.group('lead')}{m.group('q')}{base}{m.group('path')}{m.group('q')}", text)


@dataclass
class ImportMapOutcome:
    """What one import-map descriptor produced for one payload."""

    chunk_name: str
    copy_path: Path | None
    result: PatchResult
    index_html: str
    index_changed: bool = False
    fallback: bool = False  # entry chunk → caller uses the in-place + alias-graph path
    copy_written: bool = False  # False when the copy on disk already had these bytes
    skips: list[Skip] = field(default_factory=list)

    @property
    def wrote_copy(self) -> bool:
        return self.copy_path is not None


def apply_import_map_patch(
    payload: Payload,
    descriptor: PatchDescriptor,
    *,
    mod: str,
    part: str,
    ui_root: Path,
    index_html: str,
    manifest: DeployManifest,
    marker_prefix: str = "floofy-patched",
) -> ImportMapOutcome:
    """Run one ``mode: import-map`` descriptor: patched copy under ``ui/patched/`` + index.html edits.

    Returns the new ``index.html`` text (unchanged when nothing applied) and the
    ``PatchResult`` of the ops on the chunk. The original chunk is never written.
    """
    label = f"{mod}#{part}"
    chunk_path, skip = resolve_chunk_target(payload, descriptor.target)
    if chunk_path is None:
        return ImportMapOutcome(descriptor.target, None, PatchResult(text=""), index_html, skips=[skip] if skip else [])
    chunk_name = chunk_path.name
    if is_entry_chunk(index_html, chunk_name):
        return ImportMapOutcome(chunk_name, None, PatchResult(text=""), index_html, fallback=True)
    original = chunk_path.read_text(encoding="utf-8", errors="surrogateescape")
    result = apply_descriptor(original, descriptor, payload.host_version)
    if not result.applied:
        return ImportMapOutcome(chunk_name, None, result, index_html, skips=list(result.skipped))

    marker = f"/* {marker_prefix}: {label} on {chunk_name} (host {payload.host_version.text}) */\n"
    copy_text = marker + rebase_imports(result.text)
    copy_dir = Path(ui_root) / PATCHED_DIR
    copy_path = copy_dir / chunk_name
    copy_bytes = copy_text.encode("utf-8", "surrogateescape")
    copy_written = not copy_path.is_file() or copy_path.read_bytes() != copy_bytes
    if copy_written:
        atomic_write_bytes(copy_path, copy_bytes)
    manifest.record_added(copy_path, sha256_bytes(copy_bytes), mod)

    sidecar = sidecar_for(copy_path)
    sidecar_doc = {
        "schema": 1,
        "mod": mod,
        "part": part,
        "label": label,
        "chunk": chunk_name,
        "original": f"/assets/{chunk_name}",
        "patched": patched_url(chunk_name),
        "hostVersion": payload.host_version.text,
        "marker": marker.strip(),
        "target": descriptor.target,
        "find": descriptor.find,
        "appliesTo": descriptor.applies_to.text if descriptor.applies_to is not None else None,
        "fromBuild": str(descriptor.from_build) if descriptor.from_build is not None else None,
        "toBuild": str(descriptor.to_build) if descriptor.to_build is not None else None,
        "ops": [
            {"index": index, "op": op.op, "fingerprint": op.fingerprint, "regex": op.regex, "marker": op.marker, "description": op.description}
            for index, op in enumerate(descriptor.ops)
        ],
        "applied": [a.op_index for a in result.applied],
        "ts": utc_now(),
    }
    sidecar_bytes = (json.dumps(sidecar_doc, indent=2) + "\n").encode("utf-8")
    if not sidecar.is_file() or sidecar.read_bytes() != sidecar_bytes:
        atomic_write_bytes(sidecar, sidecar_bytes)
    manifest.record_added(sidecar, sha256_bytes(sidecar_bytes), mod)

    new_index, map_changed = merge_import_map(index_html, {f"/assets/{chunk_name}": patched_url(chunk_name)})
    new_index, preload_dropped = drop_modulepreload(new_index, f"/assets/{chunk_name}")
    outcome = ImportMapOutcome(chunk_name, copy_path, result, new_index, index_changed=map_changed or preload_dropped, copy_written=copy_written, skips=list(result.skipped))
    outcome.result.import_map_applied = map_changed
    return outcome


def refresh_patched_index(ui_root: Path, manifests: list[DeployManifest]) -> Path | None:
    """Rewrite ``ui/patched/index.json`` from the sidecars the manifests still reference (the runtime's ``list()``)."""
    copy_dir = Path(ui_root) / PATCHED_DIR
    referenced = {Path(a.path) for m in manifests for a in m.added}
    entries: list[dict[str, Any]] = []
    for sidecar in sorted(copy_dir.glob("*.floofy.json")) if copy_dir.is_dir() else []:
        if sidecar not in referenced and sidecar.resolve() not in {p.resolve() for p in referenced}:
            continue
        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries.append({k: document.get(k) for k in ("mod", "part", "label", "chunk", "original", "patched", "hostVersion", "marker")} | {"sidecar": f"{patched_url(sidecar.name)}"})
    if not entries:
        index_path = copy_dir / PATCHED_INDEX
        if index_path.exists():
            index_path.unlink()
        return None
    index_path = copy_dir / PATCHED_INDEX
    atomic_write_bytes(index_path, (json.dumps({"schema": 1, "generatedAt": utc_now(), "patches": entries}, indent=2) + "\n").encode("utf-8"))
    return index_path
