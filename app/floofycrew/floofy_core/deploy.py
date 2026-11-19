"""Deployment manifest, sidecar backups, added-file naming and drift classification.

Requirement 5.2 — one manifest per payload at ``<data_home>/deploy/<payload>.json``
recording every touched file (path, ``sha256(original)``, ``sha256(patched)``,
owning mod and part, host version, timestamp); backups beside the file with the
``.floofybak`` suffix; added files carrying the ``-floofy`` name marker.

Requirement 5.3 — before any apply or remove the manifest is diffed against disk
and every file is classified. The decision table, per ``files[]`` entry:

======================================  ==========  ====================  ==============
disk bytes                              backup      host version changed  class
======================================  ==========  ====================  ==============
file missing                            any         any                   ``mod-deleted``
== patched                              any         any                   ``clean``
== original                             any         any                   ``host-updated``
!= patched and != original              any         yes                   ``host-updated``
!= patched and != original              absent      no                    ``host-updated``
!= patched and != original              present     no                    ``user-edited``
======================================  ==========  ====================  ==============

Reading the table: bytes we wrote are ``clean``; the recorded original back in
place means the host re-laid its file (reinstall, same-version update) and the
patch is simply gone — ``host-updated``; unknown bytes are the host's when the
payload now reports another version or when our sidecar backup vanished with the
rest of our traces (a fresh install), and the user's when our backup is still
there and nothing else moved. For ``added[]`` entries: missing → ``mod-deleted``,
other bytes → ``user-edited``, else ``clean``. Sidelined ``.br``/``.gz`` sidecars
(task 3.4) are ``clean`` while the sidecar stays moved aside and the original
name is absent, ``host-updated`` when the original name is back (the host re-laid
it), ``mod-deleted`` when the moved copy is gone.

Default actions (:data:`DEFAULT_ACTIONS`): ``clean`` → nothing, ``host-updated`` →
``re-derive`` from the new original (the stale backup is refreshed — the one
sanctioned exception to first-write-only backups, because the recorded original
is no longer the host's), ``user-edited`` → ``skip`` and report, ``mod-deleted``
→ ``re-add``. The Patcher (task 3.5) executes the actions; this module only
decides them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from . import __version__ as FLOOFYCREW_VERSION

__all__ = [
    "ADDED_MARKER",
    "AddedFile",
    "BACKUP_SUFFIX",
    "Backups",
    "DEFAULT_ACTIONS",
    "DeployManifest",
    "DriftAction",
    "DriftClass",
    "DriftReport",
    "MANIFEST_SCHEMA",
    "PatchedFile",
    "SidelinedFile",
    "added_name",
    "atomic_write_bytes",
    "atomic_write_text",
    "canonical_path",
    "classify_added",
    "classify_drift",
    "classify_file",
    "classify_sidelined",
    "is_floofy_added",
    "iter_manifests",
    "manifest_filename",
    "sha256_bytes",
    "sha256_file",
    "utc_now",
]

MANIFEST_SCHEMA = 1
#: Sidecar backup suffix (Requirement 5.2, configurable per :class:`Backups`).
BACKUP_SUFFIX = ".floofybak"
#: Name marker every added file carries, just before the extension (``main-1a2b3c4d-floofy.js``).
ADDED_MARKER = "-floofy"

_ADDED_RE = re.compile(r"-floofy(?:\.[^./\\]+)*$")
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temp file in the same directory + ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            shutil.copymode(path, temp_name)
        except OSError:
            pass  # a new file keeps the default mode
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


# --- added-file naming ---------------------------------------------------------------


def added_name(name: str, tag: str | None = None) -> str:
    """``<stem>[-<tag>]-floofy<ext>`` for a file this framework adds beside host files.

    ``added_name("App-x1.js")`` → ``App-x1-floofy.js``; with ``tag="1a2b3c4d"`` →
    ``App-x1-1a2b3c4d-floofy.js`` (the alias-graph shape, task 3.4). Compound
    extensions keep only the final one after the marker (``a.tar.gz`` →
    ``a.tar-floofy.gz``), so :func:`is_floofy_added` recognises the result.
    """
    if is_floofy_added(name):
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or not ext:
        stem, ext, dot = name, "", ""
    middle = f"-{tag}" if tag else ""
    return f"{stem}{middle}{ADDED_MARKER}{dot}{ext}"


def is_floofy_added(path: Path | str) -> bool:
    """``True`` when the file name carries the ``-floofy`` marker before its extension(s)."""
    return _ADDED_RE.search(Path(path).name) is not None and not Path(path).name.endswith(BACKUP_SUFFIX)


def manifest_filename(payload_id: str) -> str:
    """A safe file stem for a payload id: ``venv:crew-venv:0.7.0`` → ``venv_crew-venv_0.7.0.json``."""
    return _UNSAFE_RE.sub("_", payload_id).strip("_") + ".json"


def canonical_path(path: Path | str) -> str:
    """One spelling per file: symlinks resolved (non-strict), used for every manifest key.

    The gateway reaches a payload through ``$HOME`` (often a symlink, e.g.
    ``/home/x -> /local/home/x``) while a shell spells the same file canonically.
    Keyed by raw string, one file collected two manifest entries — each process
    classifying the other's write as ``user-edited`` — so every lookup and every
    recorded path goes through here.
    """
    try:
        return str(Path(path).resolve())
    except OSError:  # pragma: no cover - resolve() is non-strict; OS errors are exotic
        return str(path)


# --- records -------------------------------------------------------------------------


@dataclass
class PatchedFile:
    """A host file whose bytes were rewritten (Requirement 5.2)."""

    path: str
    orig_sha256: str
    patched_sha256: str
    mod: str
    part: str
    ts: str = field(default_factory=utc_now)
    backup: str | None = None  # sidecar path; default ``<path><BACKUP_SUFFIX>``

    def backup_path(self, suffix: str = BACKUP_SUFFIX) -> Path:
        return Path(self.backup) if self.backup else Path(self.path + suffix)


@dataclass
class AddedFile:
    """A file this framework created beside host files (name carries ``-floofy``)."""

    path: str
    sha256: str
    mod: str
    ts: str = field(default_factory=utc_now)


@dataclass
class SidelinedFile:
    """A pre-compressed ``.br``/``.gz`` sidecar moved aside so the patched plain file is served (task 3.4)."""

    path: str
    moved_to: str
    mod: str
    ts: str = field(default_factory=utc_now)


@dataclass
class DeployManifest:
    """The per-payload record of everything the Patcher did (design "Deployment manifest")."""

    payload: str
    host_version: str
    edition: str
    payload_root: str = ""
    files: list[PatchedFile] = field(default_factory=list)
    added: list[AddedFile] = field(default_factory=list)
    sidelined: list[SidelinedFile] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)
    floofycrew_version: str = FLOOFYCREW_VERSION
    schema: int = MANIFEST_SCHEMA

    # -- location -------------------------------------------------------------------

    @staticmethod
    def path_for(data_home: Path, payload_id: str) -> Path:
        return Path(data_home) / "deploy" / manifest_filename(payload_id)

    @classmethod
    def load(cls, path: Path) -> "DeployManifest":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != MANIFEST_SCHEMA:
            raise ValueError(f"{path}: not a schema-{MANIFEST_SCHEMA} deployment manifest")
        manifest = cls(
            payload=str(document["payload"]),
            host_version=str(document["hostVersion"]),
            edition=str(document.get("edition", "unknown")),
            payload_root=str(document.get("payloadRoot", "")),
            files=[PatchedFile(**_pick(e, PatchedFile)) for e in document.get("files", [])],
            added=[AddedFile(**_pick(e, AddedFile)) for e in document.get("added", [])],
            sidelined=[SidelinedFile(**_pick(e, SidelinedFile)) for e in document.get("sidelined", [])],
            updated_at=str(document.get("updatedAt", "")),
            floofycrew_version=str(document.get("floofycrewVersion", "")),
        )
        manifest._canonicalize()
        return manifest

    def _canonicalize(self) -> None:
        """Heal a manifest written before paths were canonical: one entry per FILE.

        Two spellings of one file (a ``$HOME`` symlink: the gateway's ``/home/x/…``
        vs a shell's ``/local/home/x/…``) fold into the canonical spelling, keeping
        the newest record (its ``patched_sha256`` describes the last actual write)
        and the oldest entry's ``orig_sha256`` (first write recorded the host's
        true original bytes).
        """
        for name in ("files", "added", "sidelined"):
            entries = getattr(self, name)
            by_path: dict[str, Any] = {}
            for entry in entries:
                key = canonical_path(entry.path)
                entry.path = key
                held = by_path.get(key)
                if held is None:
                    by_path[key] = entry
                    continue
                older, newer = sorted((held, entry), key=lambda e: e.ts)
                if name == "files":
                    newer.orig_sha256 = older.orig_sha256
                    if newer.backup is None:
                        newer.backup = older.backup
                by_path[key] = newer
            entries[:] = list(by_path.values())

    @classmethod
    def load_if_exists(cls, path: Path) -> "DeployManifest | None":
        return cls.load(path) if Path(path).is_file() else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "floofycrewVersion": self.floofycrew_version,
            "payload": self.payload,
            "hostVersion": self.host_version,
            "edition": self.edition,
            "payloadRoot": self.payload_root,
            "updatedAt": self.updated_at,
            "files": [asdict(f) for f in self.files],
            "added": [asdict(a) for a in self.added],
            "sidelined": [asdict(s) for s in self.sidelined],
        }

    def save(self, path: Path) -> None:
        """Atomic write (temp + rename) so a crash never leaves a torn manifest."""
        self.updated_at = utc_now()
        atomic_write_text(path, json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n")

    # -- recording ----------------------------------------------------------------------

    def find_file(self, path: Path | str) -> PatchedFile | None:
        key = canonical_path(path)
        return next((f for f in self.files if f.path == key), None)

    def find_added(self, path: Path | str) -> AddedFile | None:
        key = canonical_path(path)
        return next((a for a in self.added if a.path == key), None)

    def record_patch(self, path: Path | str, orig_sha256: str, patched_sha256: str, mod: str, part: str, backup: Path | str | None = None) -> PatchedFile:
        """Record (or update) a rewritten file. The original hash is kept from the first record."""
        existing = self.find_file(path)
        if existing is not None:
            existing.patched_sha256 = patched_sha256
            existing.mod = mod
            existing.part = part
            existing.ts = utc_now()
            return existing
        record = PatchedFile(canonical_path(path), orig_sha256, patched_sha256, mod, str(part), backup=str(backup) if backup else None)
        self.files.append(record)
        return record

    def record_added(self, path: Path | str, sha256: str, mod: str) -> AddedFile:
        existing = self.find_added(path)
        if existing is not None:
            existing.sha256, existing.mod, existing.ts = sha256, mod, utc_now()
            return existing
        record = AddedFile(canonical_path(path), sha256, mod)
        self.added.append(record)
        return record

    def record_sidelined(self, path: Path | str, moved_to: Path | str, mod: str) -> SidelinedFile:
        key = canonical_path(path)
        existing = next((s for s in self.sidelined if s.path == key), None)
        if existing is not None:
            existing.moved_to, existing.mod, existing.ts = str(moved_to), mod, utc_now()
            return existing
        record = SidelinedFile(key, str(moved_to), mod)
        self.sidelined.append(record)
        return record

    def forget(self, path: Path | str) -> None:
        key = canonical_path(path)
        self.files = [f for f in self.files if f.path != key]
        self.added = [a for a in self.added if a.path != key]
        self.sidelined = [s for s in self.sidelined if s.path != key]

    @property
    def is_empty(self) -> bool:
        return not (self.files or self.added or self.sidelined)


def _pick(entry: dict[str, Any], cls: type) -> dict[str, Any]:
    names = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return {k: v for k, v in entry.items() if k in names}


# --- backups -------------------------------------------------------------------------


@dataclass(frozen=True)
class Backups:
    """First-write-only sidecar backups beside the file (Requirement 5.2)."""

    suffix: str = BACKUP_SUFFIX

    def backup_path(self, file: Path | str) -> Path:
        file = Path(file)
        return file.with_name(file.name + self.suffix)

    def has_backup(self, file: Path | str) -> bool:
        return self.backup_path(file).is_file()

    def ensure(self, file: Path | str) -> Path:
        """Take the backup once; an existing backup is never overwritten."""
        file = Path(file)
        backup = self.backup_path(file)
        if not backup.exists():
            shutil.copy2(file, backup)
        return backup

    def refresh(self, file: Path | str) -> Path:
        """Replace the backup with the current bytes — only for ``host-updated`` drift.

        The recorded original is no longer the host's; keeping it would make
        ``restore`` downgrade the file to bytes the host no longer ships.
        """
        file = Path(file)
        backup = self.backup_path(file)
        if backup.exists():
            backup.unlink()
        shutil.copy2(file, backup)
        return backup

    def restore_file(self, file: Path | str) -> bool:
        """Move the backup back over ``file``; ``True`` when there was one."""
        file = Path(file)
        backup = self.backup_path(file)
        if not backup.is_file():
            return False
        os.replace(backup, file)
        return True

    def discard(self, file: Path | str) -> bool:
        backup = self.backup_path(file)
        if backup.is_file():
            backup.unlink()
            return True
        return False

    def is_backup(self, path: Path | str) -> bool:
        return Path(path).name.endswith(self.suffix)

    def original_of(self, backup: Path | str) -> Path:
        backup = Path(backup)
        return backup.with_name(backup.name[: -len(self.suffix)])


# --- drift ---------------------------------------------------------------------------


class DriftClass(str, Enum):
    CLEAN = "clean"
    HOST_UPDATED = "host-updated"
    USER_EDITED = "user-edited"
    MOD_DELETED = "mod-deleted"


class DriftAction(str, Enum):
    NONE = "none"
    RE_DERIVE = "re-derive"
    SKIP = "skip"
    RE_ADD = "re-add"


#: Requirement 5.3 default actions per drift class.
DEFAULT_ACTIONS: dict[DriftClass, DriftAction] = {
    DriftClass.CLEAN: DriftAction.NONE,
    DriftClass.HOST_UPDATED: DriftAction.RE_DERIVE,
    DriftClass.USER_EDITED: DriftAction.SKIP,
    DriftClass.MOD_DELETED: DriftAction.RE_ADD,
}


def classify_file(
    *,
    orig_sha256: str,
    patched_sha256: str,
    disk_sha256: str | None,
    backup_present: bool,
    host_version_changed: bool,
) -> DriftClass:
    """The decision table from the module docstring, for one rewritten file (Requirement 5.3)."""
    if disk_sha256 is None:
        return DriftClass.MOD_DELETED
    if disk_sha256 == patched_sha256:
        return DriftClass.CLEAN
    if disk_sha256 == orig_sha256:
        return DriftClass.HOST_UPDATED
    if host_version_changed or not backup_present:
        return DriftClass.HOST_UPDATED
    return DriftClass.USER_EDITED


def classify_added(*, sha256: str, disk_sha256: str | None) -> DriftClass:
    if disk_sha256 is None:
        return DriftClass.MOD_DELETED
    return DriftClass.CLEAN if disk_sha256 == sha256 else DriftClass.USER_EDITED


def classify_sidelined(*, original_present: bool, moved_present: bool) -> DriftClass:
    if not moved_present:
        return DriftClass.MOD_DELETED
    return DriftClass.HOST_UPDATED if original_present else DriftClass.CLEAN


@dataclass
class DriftReport:
    """Per-path classification with the default action attached."""

    classes: dict[str, DriftClass] = field(default_factory=dict)
    actions: dict[str, DriftAction] = field(default_factory=dict)

    def add(self, path: str, drift: DriftClass) -> None:
        self.classes[path] = drift
        self.actions[path] = DEFAULT_ACTIONS[drift]

    def paths(self, drift: DriftClass) -> list[str]:
        return sorted(p for p, d in self.classes.items() if d is drift)

    @property
    def clean(self) -> bool:
        return all(d is DriftClass.CLEAN for d in self.classes.values())

    def summary(self) -> dict[str, int]:
        counts = {d.value: 0 for d in DriftClass}
        for drift in self.classes.values():
            counts[drift.value] += 1
        return counts


def classify_drift(
    manifest: DeployManifest,
    *,
    current_host_version: str | None = None,
    backups: Backups | None = None,
    hasher=sha256_file,
) -> DriftReport:
    """Diff ``manifest`` against disk and classify every recorded path (Requirement 5.3).

    ``current_host_version`` is what the payload reports now; ``None`` means
    "unknown, assume unchanged". ``hasher`` is injectable for tests.
    """
    sidecars = backups or Backups()
    version_changed = current_host_version is not None and current_host_version != manifest.host_version
    report = DriftReport()
    for entry in manifest.files:
        path = Path(entry.path)
        disk = hasher(path) if path.is_file() else None
        report.add(
            entry.path,
            classify_file(
                orig_sha256=entry.orig_sha256,
                patched_sha256=entry.patched_sha256,
                disk_sha256=disk,
                backup_present=entry.backup_path(sidecars.suffix).is_file(),
                host_version_changed=version_changed,
            ),
        )
    for added in manifest.added:
        path = Path(added.path)
        report.add(added.path, classify_added(sha256=added.sha256, disk_sha256=hasher(path) if path.is_file() else None))
    for sidelined in manifest.sidelined:
        report.add(
            sidelined.path,
            classify_sidelined(original_present=Path(sidelined.path).exists(), moved_present=Path(sidelined.moved_to).exists()),
        )
    return report


def iter_manifests(data_home: Path) -> Iterable[tuple[Path, DeployManifest]]:
    """Every readable manifest under ``<data_home>/deploy``; unreadable ones are skipped."""
    deploy_dir = Path(data_home) / "deploy"
    if not deploy_dir.is_dir():
        return
    for path in sorted(deploy_dir.glob("*.json")):
        try:
            yield path, DeployManifest.load(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
